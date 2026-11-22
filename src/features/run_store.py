"""Команды Feature Store (блок 4.1).

Запуск: .venv/Scripts/python -I src/features/run_store.py <команда> [аргументы]

Команды:
- init         создать таблицы и SQL-функции Feature Store;
- build        построить срез на дату: --as-of 2024-09-01;
- backfill     историческое заполнение: --dates 2023-09-01,2024-02-05;
- materialize  перенести последний (или --as-of) offline-срез в Redis;
- show         признаки студента из Redis: --student <student_id>;
- meta         метаданные материализации online-хранилища.

Пример цикла обновления: срез после сессии, материализация, чтение сервисом.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from features.store import FeatureStore  # noqa: E402


def parse_date(text: str) -> date:
    return date.fromisoformat(text)


def parse_dates(text: str) -> list[date]:
    return [parse_date(d.strip()) for d in text.split(",") if d.strip()]


def main() -> None:
    parser = argparse.ArgumentParser(description="Команды Feature Store")
    parser.add_argument("command", choices=["init", "build", "backfill", "materialize", "show", "meta"])
    parser.add_argument("--as-of", type=parse_date)
    parser.add_argument("--dates", type=parse_dates)
    parser.add_argument("--student")
    args = parser.parse_args()

    store = FeatureStore()

    if args.command == "init":
        store.apply_ddl()
        print("таблицы и SQL-функции Feature Store созданы")

    elif args.command == "build":
        if not args.as_of:
            parser.error("build требует --as-of")
        n = store.build_offline(args.as_of)
        print(f"срез на {args.as_of}: {n} точек")

    elif args.command == "backfill":
        if not args.dates:
            parser.error("backfill требует --dates через запятую")
        report = store.backfill(args.dates)
        print(report.to_string(index=False))

    elif args.command == "materialize":
        n = store.materialize(args.as_of)
        print(f"материализовано студентов: {n}, срез {store.online_meta()['as_of']}")

    elif args.command == "show":
        if not args.student:
            parser.error("show требует --student")
        values = store.read_online(args.student)
        if values is None:
            print("студент не найден в online-хранилище")
            return
        for key in sorted(values):
            print(f"{key} = {values[key]}")

    elif args.command == "meta":
        for key in sorted(store.online_meta()):
            print(f"{key} = {store.online_meta()[key]}")


if __name__ == "__main__":
    main()
