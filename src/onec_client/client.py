"""Клиент HTTP-сервиса расширения 1С АР_АналитикаРиска (сторона приемника).

Контракт в src/1c_ext/README.md: ответы имеют конверт data, meta, errors;
в meta входят count, nextCursor, asOf, schemaVersion. Пагинация курсорная:
cursor берется из meta.nextCursor предыдущей страницы, размер страницы
задается параметром limit (сервис принимает не более 10000).

Устойчивость к повторным запросам:
- GET-запросы идемпотентны, при сетевом сбое или ошибке сервера
  502/503/504 повторяются с нарастающей паузой;
- обход страниц защищен от зацикливания курсора;
- события дедуплицируются по event_id (повторная выгрузка того же
  диапазона не создает дубликатов);
- POST /scoring/batch у сервиса идемпотентен по calculationId: повторная
  отправка пакета с тем же идентификатором заменяет прежний набор расчета,
  поэтому после сетевого сбоя пакет отправляется снова без изменений.

Адрес и учетные данные берутся из .env: 1C_BASE_URL, 1C_USER, 1C_PASSWORD.
Локальные адреса идут напрямую, без системного прокси (trust_env=False).
"""

from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]

RETRY_STATUS = {502, 503, 504}
DEFAULT_BASE_URL = "http://localhost/uni/hs/analytics/v1"


def _is_local(base_url: str) -> bool:
    """Локальный адрес: запросы идут напрямую, без системного прокси."""
    return httpx.URL(base_url).host in ("localhost", "127.0.0.1", "::1")


class OneCServiceError(RuntimeError):
    """Ошибка HTTP-сервиса 1С: код состояния и машиночитаемый текст."""

    def __init__(self, status_code: int, message: str, path: str = ""):
        super().__init__(f"{path or 'сервис'}: код {status_code}: {message}")
        self.status_code = status_code
        self.message = message
        self.path = path


def book_hash(salt: str, book_number: str) -> str:
    """Псевдоним номера зачетной книжки.

    Тот же алгоритм и соль, что и у username_hash для логина Moodle
    (src/enriched/moodle_etl.py): sha256 от соли и значения без учета
    регистра, первые 32 символа. Совпадение hash зачетной книжки и hash
    логина означает совпадение значений.
    """
    return hashlib.sha256((salt + book_number.lower()).encode("utf-8")).hexdigest()[:32]


class OneCClient:
    """Чтение витрин и событий, отправка оценок риска."""

    def __init__(self, base_url: str, user: str, password: str,
                 read_timeout: float = 300.0, max_retries: int = 3, transport=None):
        self.base_url = base_url.rstrip("/")
        self.http = httpx.Client(
            base_url=self.base_url,
            auth=(user, password),
            timeout=httpx.Timeout(10.0, read=read_timeout),
            headers={"User-Agent": "academic-risk-1c/client"},
            trust_env=not _is_local(base_url),
            transport=transport,
        )
        self.max_retries = max_retries

    @classmethod
    def from_env(cls) -> "OneCClient":
        """Клиент с параметрами из .env корня репозитория."""
        load_dotenv(ROOT / ".env")
        return cls(
            os.getenv("1C_BASE_URL", DEFAULT_BASE_URL),
            os.getenv("1C_USER", "ws"),
            os.getenv("1C_PASSWORD", ""),
        )

    def close(self) -> None:
        self.http.close()

    # ---------- низкий уровень ----------

    def _request(self, method: str, path: str, *, params=None, json_body=None):
        """Запрос с повторами на сетевые сбои и ошибки сервера."""
        for attempt in range(self.max_retries + 1):
            try:
                response = self.http.request(method, path, params=params, json=json_body)
            except httpx.TransportError:
                if attempt >= self.max_retries:
                    raise
            else:
                if response.status_code not in RETRY_STATUS:
                    return response
                if attempt >= self.max_retries:
                    break
            time.sleep(2 ** attempt)
        return response  # последняя ошибка сервера: разбор ниже

    @staticmethod
    def _error_message(payload) -> str:
        """Текст ошибки из конверта или из тела ответа."""
        if isinstance(payload, dict):
            errors = payload.get("errors")
            if isinstance(errors, list) and errors:
                parts = []
                for item in errors:
                    if isinstance(item, dict):
                        parts.append(str(item.get("message") or item.get("code") or item))
                    else:
                        parts.append(str(item))
                return "; ".join(parts)
            for key in ("message", "exception", "error"):
                if payload.get(key):
                    return str(payload[key])
        if isinstance(payload, str):
            return payload[:300]
        return "ошибка без описания"

    def _json(self, method: str, path: str, *, params=None, json_body=None):
        response = self._request(method, path, params=params, json_body=json_body)
        if response.status_code >= 400:
            try:
                payload = response.json()
            except ValueError:
                payload = response.text
            raise OneCServiceError(response.status_code, self._error_message(payload), path)
        return response.json()

    @staticmethod
    def _envelope(payload, path: str):
        """Данные и meta из конверта; ответ без конверта считается ошибкой."""
        if not isinstance(payload, dict) or "data" not in payload:
            raise OneCServiceError(200, "ответ не содержит конверт data/meta/errors", path)
        return payload["data"], payload.get("meta") or {}

    # ---------- служебные методы ----------

    def health(self) -> dict:
        """Доступность и версии (ответ без конверта)."""
        return self._json("GET", "/health")

    def schema(self) -> dict:
        """Состав полей витрин (ответ без конверта)."""
        return self._json("GET", "/schema")

    def reference(self) -> dict:
        """Перечни значений справочников."""
        payload = self._json("GET", "/reference")
        return payload.get("data", payload)

    # ---------- витрины ----------

    def _page(self, route: str, params):
        payload = self._json("GET", f"/{route}", params=params)
        return self._envelope(payload, route)

    def walk(self, route: str, params=None, page_size: int | None = 10000):
        """Генератор страниц витрины: каждая страница список строк.

        Курсор берется из meta.nextCursor; повтор курсора считается
        зацикливанием и прерывает обход ошибкой. page_size задает размер
        страницы (limit); None означает, что размер уже в params.
        """
        params = dict(params or {})
        if page_size is not None:
            params["limit"] = page_size
        seen_cursors = set()
        cursor = None
        while True:
            page_params = dict(params)
            if cursor:
                page_params["cursor"] = cursor
            data, meta = self._page(route, page_params)
            yield data
            cursor = meta.get("nextCursor")
            if not cursor:
                return
            if cursor in seen_cursors:
                raise OneCServiceError(200, f"курсор зациклился на обходе {route}", route)
            seen_cursors.add(cursor)

    def fetch(self, route: str, params=None, page_size: int = 10000, on_page=None):
        """Вся витрина списком; on_page(страница, всего) для индикации."""
        rows: list = []
        for page in self.walk(route, params=params, page_size=page_size):
            rows.extend(page)
            if on_page is not None:
                on_page(page, len(rows))
        return rows

    def fetch_events(self, params=None, page_size: int = 10000):
        """События диапазона с дедупликацией по event_id.

        Возврат: (события без повторов, число пропущенных повторов).
        """
        seen: set = set()
        events: list = []
        duplicates = 0
        for page in self.walk("events", params=params, page_size=page_size):
            for event in page:
                event_id = event.get("event_id")
                if event_id in seen:
                    duplicates += 1
                    continue
                seen.add(event_id)
                events.append(event)
        return events, duplicates

    # ---------- прием оценок риска ----------

    def scoring_batch(self, calculation_id: str, items: list[dict],
                      calculation_date: str | None = None) -> dict:
        """POST /scoring/batch: пакет оценок (не более 5000 записей).

        studentId это идентификатор записи справочника зачетных книг,
        riskProbability число от 0 до 1. Пакет идемпотентен по
        calculationId: повторная отправка заменяет набор расчета.
        """
        if not 0 < len(items) <= 5000:
            raise ValueError("пакет должен содержать от 1 до 5000 записей")
        body: dict = {"calculationId": calculation_id, "items": items}
        if calculation_date:
            body["calculationDate"] = calculation_date
        payload = self._json("POST", "/scoring/batch", json_body=body)
        data, _ = self._envelope(payload, "/scoring/batch")
        return data

    def scoring_latest(self, limit: int = 100) -> list[dict]:
        """Последние записанные оценки."""
        return self.fetch("scoring/latest", params={"limit": limit}, page_size=limit)
