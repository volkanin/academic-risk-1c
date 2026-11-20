"""Переход raw -> cleaned: приведение типов и нормализация значений.

Правила перехода зафиксированы в заголовке sql/layers.sql и воспроизводятся
SQL-скриптом: пропуски (пустая строка, '?') в NULL, коды категорий в целые,
единая числовая шкала оценок, производные флаги (is_pass, is_closed,
days_open, is_paid), целевая переменная UCI.

Запуск: .venv/Scripts/python src/data/clean.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from utils.db import connect

ROOT = Path(__file__).resolve().parents[2]


def main() -> None:
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute((ROOT / "sql" / "layers.sql").read_text(encoding="utf-8"))
        conn.commit()
        with conn.cursor() as cur:
            cur.execute("SELECT relname FROM pg_class WHERE relnamespace = 'cleaned'::regnamespace AND relkind = 'r' ORDER BY relname")
            names = [row[0] for row in cur.fetchall()]
            for name in names:
                cur.execute(f"SELECT count(*) FROM cleaned.{name}")
                print(f"cleaned.{name}: {cur.fetchone()[0]}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
