"""Feature Store: единый API чтения offline и online признаков (блок 4.1).

Offline-хранилище: PostgreSQL, SQL-функции из sql/feature_store.sql
(features.build_snapshot, features.backfill) со срезом на дату.
Online-хранилище: Redis, актуальный срез признаков для скоринга.
Материализация переносит последний offline-срез в Redis целиком.

Ключи Redis:
- fs:student:<student_id> хеш признаков одного студента (значения строками,
  пустая строка означает NULL);
- fs:meta хеш метаданных материализации: дата среза, время, число студентов,
  версия состава признаков.

Запуск команд: .venv/Scripts/python src/features/run_store.py <команда>.
"""

from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import psycopg2
import redis

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from utils.db import connect, engine  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_VERSION = 1
META_KEY = "fs:meta"
STUDENT_KEY = "fs:student:"

# Типы колонок среза для чтения из Redis: значения хранятся строками,
# приведение выполняется по явному списку, а не по догадкам (идентификаторы
# групп с ведущими нулями не должны превращаться в числа).
INT_COLUMNS = {
    "semester_no", "age", "semesters_done", "worsening_streak", "n_retries",
    "n_no_shows", "debts_open", "debts_closed", "debts_days_open",
    "overdue_max", "n_unpaid", "n_late_paid", "n_exams_planned",
    "leaves_taken", "target",
    # поля потока событий (streaming/consumer.py)
    "n_grade_events", "n_debt_open_events", "n_debt_closed_events",
    "n_payment_events",
}
FLOAT_COLUMNS = {
    "gpa_mean", "gpa_last", "gpa_prev", "gpa_delta", "gpa_slope", "gpa_std",
    "first_attempt_share", "group_gpa_prev", "credits_planned", "exam_score",
}
BOOL_COLUMNS = {"is_non_local", "hostel", "privilege", "medal", "on_leave"}


class FeatureStore:
    """Чтение признаков из offline (PostgreSQL) и online (Redis) хранилищ."""

    def __init__(self, pg: psycopg2.extensions.connection | None = None,
                 redis_client: redis.Redis | None = None):
        self._pg = pg
        self._redis = redis_client

    # ---------- соединения ----------

    @property
    def pg(self):
        if self._pg is None or self._pg.closed:
            self._pg = connect()
        return self._pg

    @property
    def redis(self) -> redis.Redis:
        if self._redis is None:
            self._redis = redis.Redis(
                host=os.getenv("REDIS_HOST", "localhost"),
                port=int(os.getenv("REDIS_PORT", "6379")),
                decode_responses=True,
            )
        return self._redis

    # ---------- offline: PostgreSQL ----------

    def apply_ddl(self) -> None:
        """Создать таблицы и функции Feature Store (идемпотентно)."""
        sql = (ROOT / "sql" / "feature_store.sql").read_text(encoding="utf-8")
        with self.pg.cursor() as cur:
            cur.execute(sql)
        self.pg.commit()

    def build_offline(self, as_of: date) -> int:
        """Построить один срез признаков на дату. Возвращает число точек."""
        with self.pg.cursor() as cur:
            cur.execute("SELECT features.build_snapshot(%s)", (as_of,))
            n = cur.fetchone()[0]
        self.pg.commit()
        return n

    def backfill(self, dates: list[date]) -> pd.DataFrame:
        """Историческое заполнение по списку дат наблюдения."""
        with self.pg.cursor() as cur:
            cur.execute("SELECT * FROM features.backfill(%s)", (dates,))
            rows = cur.fetchall()
            cols = [d.name for d in cur.description]
        self.pg.commit()
        return pd.DataFrame(rows, columns=cols)

    def snapshot_dates(self) -> list[date]:
        """Даты всех построенных срезов."""
        with self.pg.cursor() as cur:
            cur.execute("SELECT as_of FROM features.fs_snapshots ORDER BY as_of")
            return [r[0] for r in cur.fetchall()]

    def read_offline(self, as_of: date | None = None,
                     student_ids: list[str] | None = None) -> pd.DataFrame:
        """Прочитать offline-срез. Без даты берётся последний."""
        eng = engine()
        sql = "SELECT * FROM features.fs_students WHERE as_of = %s"
        params: tuple = (as_of or self.latest_as_of(),)
        if student_ids:
            sql += " AND student_id = ANY(%s)"
            params = params + (student_ids,)
        return pd.read_sql(sql, eng, params=params)

    def latest_as_of(self) -> date:
        with self.pg.cursor() as cur:
            cur.execute("SELECT max(as_of) FROM features.fs_students")
            return cur.fetchone()[0]

    # ---------- online: Redis ----------

    def materialize(self, as_of: date | None = None) -> int:
        """Перенести offline-срез в Redis. Возвращает число студентов."""
        df = self.read_offline(as_of)
        r = self.redis
        pipe = r.pipeline()
        old_keys = list(r.scan_iter(f"{STUDENT_KEY}*"))
        if old_keys:
            pipe.delete(*old_keys)
        for row in df.itertuples(index=False):
            mapping = {k: _to_redis(v) for k, v in zip(df.columns, row)}
            pipe.hset(f"{STUDENT_KEY}{row.student_id}", mapping=mapping)
        pipe.execute()
        meta = {
            "as_of": str(df["as_of"].iloc[0]),
            "built_at": pd.Timestamp.now().isoformat(timespec="seconds"),
            "n_students": str(len(df)),
            "schema_version": str(SCHEMA_VERSION),
            "columns": ",".join(df.columns),
        }
        r.hset(META_KEY, mapping=meta)
        return len(df)

    def read_online(self, student_id: str) -> dict | None:
        """Признаки одного студента из Redis. None, если студента нет."""
        values = self.redis.hgetall(f"{STUDENT_KEY}{student_id}")
        if not values:
            return None
        return {k: _from_redis(k, v) for k, v in values.items()}

    def read_online_batch(self, student_ids: list[str]) -> dict[str, dict]:
        """Признаки нескольких студентов: словарь по идентификатору."""
        pipe = self.redis.pipeline()
        for sid in student_ids:
            pipe.hgetall(f"{STUDENT_KEY}{sid}")
        result = {}
        for sid, values in zip(student_ids, pipe.execute()):
            if values:
                result[sid] = {k: _from_redis(k, v) for k, v in values.items()}
        return result

    def online_meta(self) -> dict:
        """Метаданные последней материализации."""
        return {k: _from_redis(k, v) for k, v in self.redis.hgetall(META_KEY).items()}


def _to_redis(v) -> str:
    """Значение колонки в строку Redis: None это пустая строка."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    if isinstance(v, pd.Timestamp):
        return v.date().isoformat()
    return str(v)


def _from_redis(column: str, v: str):
    """Обратное приведение по типу колонки: пустая строка это NULL.

    Целые колонки при материализации могли быть записаны строкой с
    дробной частью: колонки с пропусками pandas приводит к float64.
    """
    if v == "":
        return None
    if column in INT_COLUMNS:
        return int(float(v))
    if column in FLOAT_COLUMNS:
        return float(v)
    if column in BOOL_COLUMNS:
        return v.lower() == "true"
    return v
