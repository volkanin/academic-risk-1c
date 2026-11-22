"""Веб-скрейпинг открытых сведений о направлениях подготовки (блок 4.1).

Источник: раздел «Сведения об образовательной организации» официального
сайта вуза (обязательный по приказу Рособрнадзора, размечен микроразметкой
schema.org). Извлекаются:
- перечень образовательных программ (sveden/education): код направления,
  название, профиль, уровень, формы обучения, нормативные сроки;
- финансовое обеспечение (sveden/budget): год и объёмы по источникам.

Сырые страницы сохраняются в data/external/usaaa (в репозиторий не входят),
извлечённые таблицы загружаются в слой enriched идемпотентно (TRUNCATE +
INSERT). Связь с витриной: код направления из заголовка программы
(cleaned.vs_programs.title), представление enriched.v_student_program_context.

Запуск: .venv/Scripts/python -I src/enriched/scrape_programs.py
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from utils.db import connect  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
BASE = "https://www.usaaa.ru"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

PAGES = {
    "education": f"{BASE}/sveden/education",
    "budget": f"{BASE}/sveden/budget",
}


def fetch(url: str) -> str:
    response = httpx.get(url, headers=HEADERS, timeout=30.0, follow_redirects=True)
    response.raise_for_status()
    return response.text


def save_raw(name: str, html: str) -> Path:
    folder = ROOT / "data" / "external" / "usaaa"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{name}.html"
    path.write_text(html, encoding="utf-8")
    return path


def _clean(text: str) -> str:
    return " ".join(text.split())


def parse_programs(html: str) -> list[dict]:
    """Строки tr[itemprop=eduAccred]: одна образовательная программа."""
    soup = BeautifulSoup(html, "html.parser")
    rows = []
    for tr in soup.select("tr[itemprop=eduAccred]"):
        def cell(prop: str) -> str:
            td = tr.select_one(f"td[itemprop={prop}]")
            return _clean(td.get_text(" ")) if td else None

        rows.append({
            "edu_code": cell("eduCode"),
            "edu_name": cell("eduName"),
            "edu_profile": cell("eduProf"),
            "edu_level": cell("eduLevel"),
            "edu_forms": cell("eduForm"),
            "edu_terms": cell("learningTerm"),
        })
    return rows


def parse_number(text: str) -> float:
    """Объём из открытых сведений: '292 773,2' в число."""
    return float(text.replace(" ", "").replace(",", "."))


def parse_finance(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    def value(prop: str) -> float:
        tag = soup.select_one(f"[itemprop={prop}]")
        return parse_number(tag.get_text()) if tag else None
    year = soup.select_one("[itemprop=finYear]")
    return {
        "fin_year": int(year.get_text()) if year else None,
        "budget_federal": value("finBFVolume"),
        "budget_regional": value("finBRVolume"),
        "budget_municipal": value("finBMVolume"),
        "paid_contracts": value("finPVolume"),
    }


def load(programs: list[dict], finance: dict, source_urls: dict[str, str]) -> None:
    ddl = (ROOT / "sql" / "enriched.sql").read_text(encoding="utf-8")
    conn = connect()
    now = datetime.now(timezone.utc)
    try:
        with conn.cursor() as cur:
            cur.execute(ddl)
            cur.execute("TRUNCATE TABLE enriched.programs_info")
            cur.executemany(
                """INSERT INTO enriched.programs_info
                   (source_url, scraped_at, edu_code, edu_name, edu_profile,
                    edu_level, edu_forms, edu_terms)
                   VALUES (%(source_url)s, %(scraped_at)s, %(edu_code)s,
                           %(edu_name)s, %(edu_profile)s, %(edu_level)s,
                           %(edu_forms)s, %(edu_terms)s)""",
                [{**row, "source_url": source_urls["education"], "scraped_at": now}
                 for row in programs],
            )
            cur.execute("TRUNCATE TABLE enriched.finance_info")
            cur.execute(
                """INSERT INTO enriched.finance_info
                   (source_url, scraped_at, fin_year, budget_federal,
                    budget_regional, budget_municipal, paid_contracts)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                (source_urls["budget"], now, finance["fin_year"],
                 finance["budget_federal"], finance["budget_regional"],
                 finance["budget_municipal"], finance["paid_contracts"]),
            )
        conn.commit()
    finally:
        conn.close()


def main() -> None:
    source_urls, programs, finance = {}, [], {}
    for name, url in PAGES.items():
        html = fetch(url)
        path = save_raw(name, html)
        source_urls[name] = url
        if name == "education":
            programs = parse_programs(html)
        else:
            finance = parse_finance(html)
        print(f"{name}: {url} ({path.stat().st_size} байт)")

    load(programs, finance, source_urls)

    codes = sorted({p["edu_code"] for p in programs})
    print(f"программ: {len(programs)}, кодов направлений: {len(codes)}")
    print("коды:", ", ".join(codes))
    print(f"финансовое обеспечение {finance.get('fin_year')}: федеральный бюджет "
          f"{finance.get('budget_federal')} тыс. руб., договоры {finance.get('paid_contracts')} тыс. руб.")


if __name__ == "__main__":
    main()
