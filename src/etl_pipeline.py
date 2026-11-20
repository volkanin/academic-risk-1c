"""ETL-конвейер: файлы датасетов -> PostgreSQL (схема raw).

Этап 1 (блок 2.1 требований): загрузка исходных файлов в слой raw.
Очистка (cleaned) и признаки (features) реализуются на следующих шагах.

Источники:
1. UCI «Predict Students' Dropout and Academic Success»: data/raw/uci_dropout/data.csv,
   разделитель «;», кодировка Latin-1. Загружается текстовыми колонками «как в файле»,
   имена колонок приведены к snake_case (без изменения значений).
2. OULAD (Open University Learning Analytics Dataset): data/raw/oulad/*.csv,
   зеркало с Kaggle (anlgrbz/student-demographics-online-education-dataoulad).
   Загружается потоково через COPY (studentVle.csv содержит около 10,6 млн строк).
3. Синтетические витрины 1С:Университет: data/raw/synthetic/vs_*.csv из
   src/data/generator.py. Типы колонок заданы в sql/schema.sql.

Запуск: .venv/Scripts/python src/etl_pipeline.py
Переменные соединения берутся из .env в корне репозитория.
"""

from __future__ import annotations

import csv
import io
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SYNTHETIC_DIR = ROOT / "data" / "raw" / "synthetic"
OULAD_DIR = ROOT / "data" / "raw" / "oulad"

SYNTHETIC_TABLES = [
    "vs_programs",
    "vs_students",
    "vs_disciplines",
    "vs_admission",
    "vs_movements",
    "vs_grades",
    "vs_debts",
    "vs_finance",
    "vs_load",
]

OULAD_FILES = {
    "oulad_courses": "courses.csv",
    "oulad_assessments": "assessments.csv",
    "oulad_vle": "vle.csv",
    "oulad_student_info": "studentInfo.csv",
    "oulad_student_registration": "studentRegistration.csv",
    "oulad_student_assessment": "studentAssessment.csv",
    "oulad_student_vle": "studentVle.csv",
}

from utils.db import connect  # noqa: E402  (общее подключение, sys.path указывает на src)


def apply_schema(conn) -> None:
    with conn.cursor() as cur:
        cur.execute((ROOT / "sql" / "schema.sql").read_text(encoding="utf-8"))
    conn.commit()


def load_rows(conn, table: str, header: list[str], rows: list[list[str]]) -> int:
    """Загрузка пачки строк через COPY (формат csv, пустая строка = NULL)."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerows(rows)
    buf.seek(0)
    return copy_stream(conn, table, header, buf)


def copy_stream(conn, table: str, header: list[str], stream) -> int:
    """COPY из открытого текстового потока; возвращает число загруженных строк.

    Поток должен указывать на первую строку данных: заголовок из файла
    пропускается до вызова (f.readline()), в буферах заголовка нет вообще.
    """
    cols = ", ".join(h if h.isidentifier() else f'"{h}"' for h in header)
    with conn.cursor() as cur:
        cur.execute(f"TRUNCATE TABLE raw.{table}")
        cur.copy_expert(
            f"COPY raw.{table} ({cols}) FROM STDIN WITH (FORMAT csv, NULL '')",
            stream,
        )
    conn.commit()
    return count_rows(conn, table)


def count_rows(conn, table: str) -> int:
    with conn.cursor() as cur:
        cur.execute(f"SELECT count(*) FROM raw.{table}")
        return cur.fetchone()[0]


def load_uci(conn) -> int:
    path = ROOT / "data" / "raw" / "uci_dropout" / "data.csv"
    with path.open(encoding="latin-1", newline="") as f:
        rows = list(csv.reader(f, delimiter=";"))
    header, data = rows[0], rows[1:]
    # Только нормализация имён: убираем BOM и табуляцию в заголовках.
    header = [re.sub(r"[^a-z0-9]+", "_", h.replace("\ufeff", "").replace("\t", " ").strip().lower()).strip("_") for h in header]
    return load_rows(conn, "uci_dropout", header, data)


def load_oulad(conn) -> dict[str, int]:
    counts = {}
    for table, filename in OULAD_FILES.items():
        path = OULAD_DIR / filename
        with path.open(encoding="utf-8", newline="") as f:
            reader = csv.reader(f)
            header = next(reader)
        with path.open(encoding="utf-8", newline="") as f:
            f.readline()  # пропускаем строку заголовка, COPY начинает с данных
            counts[table] = copy_stream(conn, table, header, f)
        print(f"raw.{table}: {counts[table]} строк")
    return counts


def load_synthetic(conn) -> dict[str, int]:
    counts = {}
    for table in SYNTHETIC_TABLES:
        path = SYNTHETIC_DIR / f"{table}.csv"
        with path.open(encoding="utf-8", newline="") as f:
            reader = csv.reader(f)
            header = next(reader)
            data = [r for r in reader]
        counts[table] = load_rows(conn, table, header, data)
    return counts


def main() -> None:
    conn = connect()
    try:
        apply_schema(conn)
        n_uci = load_uci(conn)
        print(f"raw.uci_dropout: {n_uci} строк")
        load_oulad(conn)
        for table, n in load_synthetic(conn).items():
            print(f"raw.{table}: {n} строк")
        print("[проверка] количество строк по всем таблицам raw:")
        with conn.cursor() as cur:
            cur.execute("SELECT relname FROM pg_class WHERE relnamespace = 'raw'::regnamespace AND relkind = 'r' ORDER BY relname")
            for (name,) in cur.fetchall():
                print(f"  raw.{name}: {count_rows(conn, name)}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
