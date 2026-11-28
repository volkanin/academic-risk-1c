"""Замер соответствия номеров зачетных книжек и логинов Moodle (FR-13).

Действующие студенты берутся из витрины /mapping/students-books расширения
1С (состояния «Является студентом» и «Задолжник», как в существующей
обработке «Синхронизация зачетных книжек с Moodle»), пользователи Moodle из
стандартной веб-службы core_user_get_users_by_field по полю username.
Сравнение идет через псевдонимы: номер книжки и логин заменяются sha256
хешем с общей солью MOODLE_SALT (book_hash совпадает с username_hash при
совпадении значений). Сырые номера не хранятся и не печатаются; в запросе
к веб-службе Moodle передаются как логины, источник которых она сама.

Совпадающие пары записываются в enriched.student_map (только student_id и
username_hash), это подключает соответствие для ETL активности Moodle.

Запуск: .venv/Scripts/python -I src/onec_client/measure_moodle.py [--mock]
Параметры: 1C_BASE_URL, 1C_USER, 1C_PASSWORD, MOODLE_URL, MOODLE_TOKEN,
MOODLE_SALT из .env.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from enriched.moodle_etl import MoodleClient, username_hash  # noqa: E402
from onec_client.client import OneCClient, book_hash  # noqa: E402
from utils.db import connect  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]

ACTIVE_STATES = ("является студентом", "задолжник")
BATCH = 500


def active_pairs(rows: list[dict]) -> dict[str, list[str]]:
    """Действующие студенты: student_id и список номеров книжек.

    У одного студента может быть несколько зачетных книжек; состояние
    сравнивается без учета регистра. Номера живут только в памяти на время
    замера, не печатаются и не сохраняются; в enriched попадают лишь
    псевдонимы.
    """
    pairs: dict[str, list[str]] = {}
    for row in rows:
        state = (row.get("student_state") or "").lower()
        if state not in ACTIVE_STATES:
            continue
        book = row.get("book_number")
        if book:
            pairs.setdefault(row["student_id"], []).append(book)
    return pairs


def moodle_users(client: MoodleClient, salt: str, books: list[str]) -> dict[str, int]:
    """Псевдонимы логинов Moodle и их идентификаторы; имена не читаются.

    Запросы пакетами по BATCH значений; логин и его хеш приводятся к общему
    виду, регистр не важен.
    """
    found: dict[str, int] = {}
    for start in range(0, len(books), BATCH):
        values = books[start:start + BATCH]
        for user in client.call("core_user_get_users_by_field", field="username", values=values):
            login = user.get("username") or ""
            if login:
                found[username_hash(salt, login)] = user.get("id")
    return found


def main() -> None:
    from dotenv import load_dotenv

    parser = argparse.ArgumentParser(description="Замер соответствия зачетных книжек и логинов Moodle")
    parser.add_argument("--mock", action="store_true", help="демонстрационный контур (эмулятор Moodle)")
    args = parser.parse_args()

    load_dotenv(ROOT / ".env")
    salt = os.getenv("MOODLE_SALT", "")
    if not salt:
        print("MOODLE_SALT не задан в .env, замер не выполняется")
        return

    one_c = OneCClient.from_env()
    rows = one_c.fetch("mapping/students-books", params={"asOf": datetime.now().date().isoformat()})
    one_c.close()
    pairs = active_pairs(rows)
    books = sorted({b for values in pairs.values() for b in values})

    if args.mock:
        moodle = MoodleClient("http://127.0.0.1:8010", "mocktoken")
    else:
        base = os.getenv("MOODLE_URL", "")
        token = os.getenv("MOODLE_TOKEN", "")
        if not base or not token:
            print("MOODLE_URL и MOODLE_TOKEN не заданы в .env, замер не выполняется")
            return
        moodle = MoodleClient(base, token)

    print(f"действующих студентов: {len(pairs)}, зачетных книжек: {len(books)}", flush=True)
    try:
        users = moodle_users(moodle, salt, books)
    finally:
        moodle.close()
    print(f"найдено пользователей Moodle по логину-зачетке: {len(users)}", flush=True)

    matched_students = 0
    matched_books = 0
    map_rows = []
    for student_id, values in pairs.items():
        hits = [book_hash(salt, b) for b in values if book_hash(salt, b) in users]
        if hits:
            matched_students += 1
            matched_books += len(hits)
            for h in hits:
                map_rows.append((student_id, h, users[h],
                                 datetime.now(timezone.utc)))

    total = len(pairs)
    share = f"{matched_students / total:.1%}" if total else "-"
    print(f"студентов с совпадением книжки и логина: {matched_students} из {total} ({share})",
          flush=True)
    print(f"совпавших пар студент-книжка: {matched_books}", flush=True)

    conn = connect()
    try:
        with conn.cursor() as cur:
            for student_id, hash_value, user_id, linked in map_rows:
                cur.execute("""
                    INSERT INTO enriched.student_map (student_id, username_hash, moodle_user_id, linked_at)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (student_id) DO UPDATE
                    SET username_hash = EXCLUDED.username_hash,
                        moodle_user_id = EXCLUDED.moodle_user_id,
                        linked_at = EXCLUDED.linked_at
                """, (student_id, hash_value, user_id, linked))
        conn.commit()
        print(f"записей в enriched.student_map: {len(map_rows)}", flush=True)

        # Витрина соответствия видит все пары студент-книжка, включая
        # исторические записи по прежним книжкам. Доля по текущему состоянию
        # витрины студентов (одна запись на студента) считается отдельно.
        matched_ids = {row[0] for row in map_rows}
        with conn.cursor() as cur:
            cur.execute("""
                SELECT student_id FROM cleaned.vs_students
                WHERE lower(state) IN ('является студентом', 'задолжник')
            """)
            current_active = {row[0] for row in cur.fetchall()}
        if current_active:
            hit = len(current_active & matched_ids)
            print(f"по текущему состоянию витрины студентов: {hit} из {len(current_active)} "
                  f"({hit / len(current_active):.1%})", flush=True)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
