"""Загрузка реальных витрин из HTTP-сервиса расширения 1С в хранилище.

Приемник данных проекта: клиент с курсорной пагинацией (src/onec_client/
client.py) выгружает витрины расширения АР_АналитикаРиска и помещает их в
слой raw, затем sql/onec_cleaned.sql переносит их в таблицы cleaned слоя
вместо синтетики (контракты витрин писались по полям cleaned,
docs/1c_enhancements_tz.md). Финансовая витрина не загружается: в этой
базе данных нет, источник появится в базе бухгалтерии.

Правила ПДн при приеме (NFR-2, FR-13): персональные данные не записываются
ни в базу, ни в журналы, ни в репозиторий:
- номер зачетной книжки заменяется псевдонимом book_hash (sha256 от соли
  MOODLE_SALT и значения без учета регистра) до первой записи в хранилище;
- дата рождения заменяется возрастом age на дату среза;
- ФИО, паспорта, контакты витринами не отдаются, связь только по UUID;
- соль общая с ETL Moodle, поэтому book_hash совпадает с username_hash
  в enriched.student_map;
- неизвестные поля витрины останавливают загрузку (защита от появления
  персональных данных в будущих версиях сервиса).

Идемпотентность: повторный запуск выгружает витрины заново и заменяет
содержимое raw и cleaned (TRUNCATE перед COPY); данные воспроизводимы,
персональные значения нигде не сохраняются между запусками.

Запуск: .venv/Scripts/python -I src/onec_client/load.py [--as-of 2026-11-28]
Параметры: 1C_BASE_URL, 1C_USER, 1C_PASSWORD, MOODLE_SALT, POSTGRES_* из .env.
"""

from __future__ import annotations

import argparse
import csv
import io
import os
import sys
import time
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from onec_client.client import OneCClient, book_hash  # noqa: E402
from utils.db import connect  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]

SQL_RAW = ROOT / "sql" / "onec_raw.sql"
SQL_CLEANED = ROOT / "sql" / "onec_cleaned.sql"

# Классы приказов по утвержденному делению аналитики
# (docs/metadata-mapping.md, п. 4, решение заказчика от 28.11.2026).
ACADEMIC_MARKERS = ("академическую неуспеваемость", "невыполнение учебного плана", "не прошедших гиа")
FINANCE_MARKERS = ("условий договора",)

# Разрешенные поля витрин; появление любого другого поля останавливает
# загрузку, чтобы новые персональные данные не попадали в хранилище.
FIELDS = {
    "students": ("student_id", "book_number", "program_id", "group_code", "faculty",
                 "study_form", "basis", "gender", "birth_date", "region", "hostel",
                 "privilege", "state"),
    "programs": ("program_id", "title", "faculty", "study_form", "year", "hours_per_credit"),
    "disciplines": ("program_id", "discipline_id", "discipline_title", "row_id", "semester",
                    "load_type", "unit", "amount", "credits"),
    "movements": ("student_id", "book_number", "order_type", "order_kind", "analytics",
                  "reason", "order_date", "effective_date", "program_id", "group_code",
                  "entered_at"),
    "grades": ("student_id", "book_number", "program_id", "discipline_id", "discipline_title",
               "semester", "control_type", "attempt_no", "grade", "absent", "exam_date",
               "entered_at", "event_id"),
    "debts": ("student_id", "book_number", "program_id", "discipline_id", "discipline_title",
              "semester", "arose_date", "closed_date", "event_id_open"),
    "load": ("program_id", "semester", "n_disciplines", "hours", "n_exams", "n_tests",
             "n_course_works", "hours_per_credit", "credits", "credits_source"),
}

ORDINALS = {
    "первый": 1, "второй": 2, "третий": 3, "четвертый": 4, "пятый": 5,
    "шестой": 6, "седьмой": 7, "восьмой": 8, "девятый": 9, "десятый": 10,
    "одиннадцатый": 11, "двенадцатый": 12, "тринадцатый": 13, "четырнадцатый": 14,
}


def semester_num(text: str) -> int | None:
    """Номер семестра из наименования периода контроля, None если не разобрано."""
    words = (text or "").lower().replace("ё", "е").split()
    if len(words) == 2 and words[1] == "семестр" and words[0] in ORDINALS:
        return ORDINALS[words[0]]
    return None


def order_class(analytics: str, order_kind: str) -> str:
    """Класс приказа: академическое, финансовое или нейтральное."""
    text = f"{analytics or ''} {order_kind or ''}".lower().replace("ё", "е")
    if any(marker in text for marker in ACADEMIC_MARKERS):
        return "академическое"
    if any(marker in text for marker in FINANCE_MARKERS):
        return "финансовое"
    return "нейтральное"


def age_at(birth_date: str, as_of: date) -> int | None:
    """Полных лет на дату среза; None без даты рождения."""
    if not birth_date:
        return None
    born = datetime.fromisoformat(birth_date).date()
    return as_of.year - born.year - ((as_of.month, as_of.day) < (born.month, born.day))


def _check_keys(rows: list[dict], table: str) -> None:
    """Защита ПДн: неизвестные поля витрины останавливают загрузку."""
    allowed = set(FIELDS[table])
    for row in rows[:1]:
        extra = set(row) - allowed
        if extra:
            raise ValueError(f"витрина {table} вернула незнакомые поля {sorted(extra)}; "
                             "проверьте их на персональные данные и добавьте в белый список")


def year_windows(first_year: int, as_of: date) -> list[tuple[str, str]]:
    """Годовые окна выгрузки фактов: запросы меньшего объема."""
    return [(f"{year}-01-01", f"{year}-12-31")
            for year in range(first_year, as_of.year + 1)]


def _csv(rows: list[tuple], columns: tuple[str, ...]) -> io.StringIO:
    """Поток CSV для COPY: None пустой строкой, булевы true/false."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    for row in rows:
        writer.writerow(["" if value is None else
                         ("true" if value is True else "false" if value is False else value)
                         for value in row])
    buffer.seek(0)
    return buffer


def _copy_rows(conn, table: str, columns: tuple[str, ...], rows: list[tuple]) -> None:
    """Порция COPY в таблицу приема (TRUNCATE делается до выгрузки)."""
    with conn.cursor() as cur:
        cur.copy_expert(
            f"COPY raw.onec_{table} ({', '.join(columns)}) FROM STDIN WITH (FORMAT csv, NULL '')",
            _csv(rows, columns),
        )
    conn.commit()


def _load_table(client: OneCClient, conn, *, route: str, table: str,
                columns: tuple[str, ...], transform, windows=None,
                extra_params=None, dedup_key: str | None = None,
                require_student_id: bool = False,
                page_size: int = 10000, chunk: int = 20000) -> int:
    """Выгрузка таблицы с преобразованием на каждой странице.

    ПДн применяется при приеме (book_hash, age), персональные значения не
    задерживаются в памяти приемника целиком. Окна делаются по годам:
    каждый запрос страницы пересчитывает витрину в 1С заново, меньшие
    диапазоны обходятся быстрее. Задолженности могут встретиться в двух
    окнах (возникла в одном году, закрыта в другом), они дедуплицируются
    по event_id_open. Строки фактов без студента (пустое физическое лицо
    в строке табличной части) пропускаются: они не несут сведений о
    студенте, число пропущенных печатается.
    """
    with conn.cursor() as cur:
        cur.execute(f"TRUNCATE TABLE raw.onec_{table}")
    conn.commit()

    total = 0
    duplicates = 0
    skipped = 0
    buf: list[tuple] = []
    seen: set = set()
    started = time.time()
    windows = windows or [None]
    for window in windows:
        params = dict(extra_params or {})
        if window:
            params.update({"dateFrom": window[0], "dateTo": window[1]})
        for page in client.walk(route, params=params, page_size=page_size):
            _check_keys(page, table)
            if dedup_key:
                unique = [row for row in page if row.get(dedup_key) not in seen]
                duplicates += len(page) - len(unique)
                seen.update(row[dedup_key] for row in unique)
                page = unique
            if require_student_id:
                with_student = [row for row in page if row.get("student_id")]
                skipped += len(page) - len(with_student)
                page = with_student
            if not page:
                continue
            buf.extend(transform(page))
            total += len(page)
            if len(buf) >= chunk:
                _copy_rows(conn, table, columns, buf)
                buf.clear()
    if buf:
        _copy_rows(conn, table, columns, buf)
    elapsed = time.time() - started
    extra = []
    if dedup_key:
        extra.append(f"дубликатов пропущено {duplicates}")
    if skipped:
        extra.append(f"строк без студента пропущено {skipped}")
    tail = "; " + ", ".join(extra) if extra else ""
    print(f"  raw.onec_{table}: {total} строк за {elapsed:.0f} с{tail}", flush=True)
    return total


def transform_students(rows: list[dict], salt: str, as_of: date) -> list[tuple]:
    """Студенты: book_number псевдонимом, birth_date возрастом."""
    _check_keys(rows, "students")
    out = []
    for row in rows:
        out.append((
            row["student_id"],
            book_hash(salt, row["book_number"]) if row.get("book_number") else None,
            row.get("program_id") or None,
            row.get("group_code") or None,
            row.get("faculty") or None,
            row.get("study_form") or None,
            row.get("basis") or None,
            row.get("gender") or None,
            age_at(row.get("birth_date"), as_of),
            row.get("region") or None,
            row.get("hostel"),
            row.get("privilege"),
            row.get("state") or None,
        ))
    return out


def transform_movements(rows: list[dict], salt: str) -> list[tuple]:
    _check_keys(rows, "movements")
    out = []
    for row in rows:
        out.append((
            row["student_id"],
            book_hash(salt, row["book_number"]) if row.get("book_number") else None,
            row.get("order_type") or None,
            row.get("order_kind") or None,
            row.get("analytics") or None,
            row.get("reason") or None,
            row.get("order_date") or None,
            row.get("effective_date") or None,
            row.get("program_id") or None,
            row.get("group_code") or None,
            order_class(row.get("analytics") or "", row.get("order_kind") or ""),
        ))
    return out


def transform_disciplines(rows: list[dict]) -> list[tuple]:
    _check_keys(rows, "disciplines")
    out = []
    for row in rows:
        out.append((
            row.get("program_id") or None,
            row["discipline_id"],
            row.get("discipline_title") or None,
            row.get("row_id") or None,
            row.get("semester") or None,
            semester_num(row.get("semester") or ""),
            row.get("load_type") or None,
            row.get("unit") or None,
            row.get("amount"),
            row.get("credits"),
        ))
    return out


def transform_grades(rows: list[dict], salt: str) -> list[tuple]:
    _check_keys(rows, "grades")
    out = []
    for row in rows:
        out.append((
            row["student_id"],
            book_hash(salt, row["book_number"]) if row.get("book_number") else None,
            row.get("program_id") or None,
            row.get("discipline_id") or None,
            row.get("discipline_title") or None,
            row.get("semester") or None,
            semester_num(row.get("semester") or ""),
            row.get("control_type") or None,
            row.get("attempt_no"),
            row.get("grade") or None,
            row.get("absent"),
            row.get("exam_date") or None,
            row.get("event_id") or None,
        ))
    return out


def transform_debts(rows: list[dict], salt: str) -> list[tuple]:
    _check_keys(rows, "debts")
    out = []
    for row in rows:
        out.append((
            row["student_id"],
            book_hash(salt, row["book_number"]) if row.get("book_number") else None,
            row.get("program_id") or None,
            row.get("discipline_id") or None,
            row.get("discipline_title") or None,
            row.get("semester") or None,
            semester_num(row.get("semester") or ""),
            row.get("arose_date") or None,
            row.get("closed_date") or None,
            row.get("event_id_open") or None,
        ))
    return out


def transform_load(rows: list[dict]) -> list[tuple]:
    """Нагрузка план-семестр: semester_num разбирается из наименования."""
    _check_keys(rows, "load")
    out = []
    for row in rows:
        out.append((
            row.get("program_id"),
            row.get("semester"),
            semester_num(row.get("semester") or ""),
            row.get("n_disciplines"),
            row.get("hours"),
            row.get("n_exams"),
            row.get("n_tests"),
            row.get("n_course_works"),
            row.get("hours_per_credit"),
            row.get("credits"),
            row.get("credits_source"),
        ))
    return out


def transform_simple(rows: list[dict], table: str, columns: tuple[str, ...]) -> list[tuple]:
    """Программы: поля совпадают с колонками таблицы."""
    _check_keys(rows, table)
    return [tuple(row.get(name) for name in columns) for row in rows]


def _report(conn) -> None:
    """Счетчики загруженного и проверки ПДн (только числа, без значений)."""
    with conn.cursor() as cur:
        checks = [
            ("raw.onec_students", "SELECT count(*) FROM raw.onec_students"),
            ("raw.onec_programs", "SELECT count(*) FROM raw.onec_programs"),
            ("raw.onec_disciplines", "SELECT count(*) FROM raw.onec_disciplines"),
            ("raw.onec_movements", "SELECT count(*) FROM raw.onec_movements"),
            ("raw.onec_grades", "SELECT count(*) FROM raw.onec_grades"),
            ("raw.onec_debts", "SELECT count(*) FROM raw.onec_debts"),
            ("raw.onec_load", "SELECT count(*) FROM raw.onec_load"),
            ("cleaned.vs_students", "SELECT count(*) FROM cleaned.vs_students"),
            ("cleaned.vs_grades", "SELECT count(*) FROM cleaned.vs_grades"),
            ("cleaned.vs_debts открытых", "SELECT count(*) FROM cleaned.vs_debts WHERE closed_date IS NULL"),
            ("cleaned.vs_movements", "SELECT count(*) FROM cleaned.vs_movements"),
            ("cleaned.vs_load", "SELECT count(*) FROM cleaned.vs_load"),
        ]
        for label, query in checks:
            cur.execute(query)
            print(f"  {label}: {cur.fetchone()[0]}", flush=True)

        # ПДн: в таблицах приема нет колонок персональных данных,
        # возраст заполнен, дат рождения и исходных номеров нет.
        cur.execute("""
            SELECT table_name, column_name FROM information_schema.columns
            WHERE table_schema = 'raw' AND table_name LIKE 'onec_%'
              AND column_name IN ('book_number', 'birth_date')
        """)
        leaked = cur.fetchall()
        if leaked:
            raise SystemExit(f"ПДн: в raw найдены колонки {leaked}")
        cur.execute("SELECT count(*) FROM cleaned.vs_students WHERE birth_date IS NOT NULL")
        print(f"  ПДн: дат рождения в cleaned.vs_students: {cur.fetchone()[0]} (должно быть 0)", flush=True)
        cur.execute("SELECT count(*), count(age) FROM cleaned.vs_students")
        total, with_age = cur.fetchone()
        print(f"  ПДн: возраст заполнен у {with_age} из {total}", flush=True)
        cur.execute("SELECT count(*), count(book_hash) FROM cleaned.vs_students")
        total, with_hash = cur.fetchone()
        print(f"  ПДн: псевдонимы зачетных книжек заполнены у {with_hash} из {total}", flush=True)


def main() -> None:
    from dotenv import load_dotenv

    parser = argparse.ArgumentParser(description="Загрузка реальных витрин 1С в raw/cleaned")
    parser.add_argument("--as-of", default=date.today().isoformat(), help="дата среза студентов")
    parser.add_argument("--page-size", type=int, default=10000, help="размер страницы выгрузки")
    parser.add_argument("--tables", default="students,programs,disciplines,movements,grades,debts,load",
                        help="какие таблицы выгружать заново (остальные берутся из raw)")
    args = parser.parse_args()
    wanted = {name.strip() for name in args.tables.split(",") if name.strip()}

    load_dotenv(ROOT / ".env")
    salt = os.getenv("MOODLE_SALT", "")
    if not salt:
        print("MOODLE_SALT не задан в .env, загрузка не выполняется")
        return
    as_of = date.fromisoformat(args.as_of)

    client = OneCClient.from_env()
    health = client.health()
    print(f"сервис: {health.get('state')}, расширение {health.get('version')}", flush=True)

    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(SQL_RAW.read_text(encoding="utf-8"))
        conn.commit()
        print("DDL raw.onec_* готов", flush=True)

        # Порядок колонок таблиц приема задан в sql/onec_raw.sql.
        columns = {
            "students": ("student_id", "book_hash", "program_id", "group_code", "faculty",
                         "study_form", "basis", "gender", "age", "region", "hostel",
                         "privilege", "state"),
            "programs": ("program_id", "title", "faculty", "study_form", "year",
                         "hours_per_credit"),
            "disciplines": ("program_id", "discipline_id", "discipline_title", "row_id",
                            "semester", "semester_num", "load_type", "unit", "amount",
                            "credits"),
            "movements": ("student_id", "book_hash", "order_type", "order_kind", "analytics",
                          "reason", "order_date", "effective_date", "program_id",
                          "group_code", "order_class"),
            "grades": ("student_id", "book_hash", "program_id", "discipline_id",
                       "discipline_title", "semester", "semester_num", "control_type",
                       "attempt_no", "grade", "absent", "exam_date", "event_id"),
            "debts": ("student_id", "book_hash", "program_id", "discipline_id",
                      "discipline_title", "semester", "semester_num", "arose_date",
                      "closed_date", "event_id_open"),
            "load": ("program_id", "semester", "semester_num", "n_disciplines", "hours",
                     "n_exams", "n_tests", "n_course_works", "hours_per_credit", "credits",
                     "credits_source"),
        }

        # Измерения: страницы полных витрин; факты: годовые окна с постраничным
        # преобразованием. ПДн применяется на каждой странице.
        if "students" in wanted:
            _load_table(client, conn, route="dim/students", table="students",
                        columns=columns["students"],
                        transform=lambda rows: transform_students(rows, salt, as_of),
                        extra_params={"asOf": as_of.isoformat()}, require_student_id=True,
                        page_size=args.page_size)
        if "programs" in wanted:
            _load_table(client, conn, route="dim/programs", table="programs",
                        columns=columns["programs"],
                        transform=lambda rows: transform_simple(rows, "programs", FIELDS["programs"]),
                        page_size=args.page_size)
        if "disciplines" in wanted:
            _load_table(client, conn, route="dim/disciplines", table="disciplines",
                        columns=columns["disciplines"],
                        transform=transform_disciplines, page_size=args.page_size)
        if "movements" in wanted:
            _load_table(client, conn, route="fact/movements", table="movements",
                        columns=columns["movements"],
                        transform=lambda rows: transform_movements(rows, salt),
                        windows=year_windows(2012, as_of), require_student_id=True,
                        page_size=args.page_size)
        if "grades" in wanted:
            _load_table(client, conn, route="fact/grades", table="grades",
                        columns=columns["grades"],
                        transform=lambda rows: transform_grades(rows, salt),
                        windows=year_windows(2012, as_of), require_student_id=True,
                        page_size=args.page_size)
        if "debts" in wanted:
            _load_table(client, conn, route="fact/debts", table="debts",
                        columns=columns["debts"],
                        transform=lambda rows: transform_debts(rows, salt),
                        windows=year_windows(2012, as_of), dedup_key="event_id_open",
                        require_student_id=True, page_size=args.page_size)
        if "load" in wanted:
            _load_table(client, conn, route="fact/load", table="load",
                        columns=columns["load"],
                        transform=transform_load, page_size=args.page_size)

        with conn.cursor() as cur:
            cur.execute(SQL_CLEANED.read_text(encoding="utf-8"))
        conn.commit()
        print("cleaned vs_* перенесены на реальные данные", flush=True)

        _report(conn)
    finally:
        conn.close()
        client.close()


if __name__ == "__main__":
    main()
