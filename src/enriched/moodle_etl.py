"""ETL Moodle по стандартным веб-службам (блок 4.1, FR-13).

Данные берутся только через REST API Moodle (решение по интеграции): POST
на webservice/rest/server.php с wstoken и параметрами, формат json. Прямой
доступ к базе Moodle не используется. Персональные данные не выгружаются:
из профиля Moodle читается только логин, он сразу заменяется хешем с солью
(salted hash), имена и почты отбрасываются.

Ключ соответствия: логин Moodle равен номеру зачётной книжки (механика
существующей обработки 1С «Синхронизация зачетных книг с Moodle»). В
демонстрационном контуре зачётку имитирует student_id синтетики; при
подключении реальной базы соответствие студента и зачётки приходит из
расширения 1С.

Выгрузка на студента:
- сданные работы (mod_assign_get_assignments + mod_assign_get_submissions,
  время сдачи);
- попытки тестов (mod_quiz_get_quizzes_by_courses + mod_quiz_get_user_attempts,
  время завершения);
- обсуждения на форумах (mod_forum_get_forums_by_courses +
  mod_forum_get_forum_discussions, время создания и последнего ответа);
- оценки (gradereport_user_get_grade_items).

Журнал событий Moodle (core_log_get_logs) в версии 4.5 не объявлен как
внешняя функция, недельная динамика строится по временам этих действий.

Результат: enriched.student_map, enriched.activity_weekly (недели семестра
по учебному календарю, 0 это межсессионный период) и enriched.activity_agg.
Оценки, работы и попытки без дисциплинарной привязки (она отложена в
backlog) учитываются суммарно за весь период и одинаковы во всех строках
студента. Функция collect_activity возвращает сводку покрытия для проверки
H2 на реальном контуре.

Запуск: .venv/Scripts/python -I src/enriched/moodle_etl.py [--mock]
Адрес и токен из .env: MOODLE_URL, MOODLE_TOKEN, MOODLE_SALT.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from utils.calendar import semester_dates  # noqa: E402
from utils.db import connect  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]


class MoodleError(RuntimeError):
    """Ошибка веб-службы Moodle (exception в ответе REST)."""


def _is_local(base_url: str) -> bool:
    """Локальный адрес: запросы идут напрямую, без системного прокси."""
    host = httpx.URL(base_url).host
    return host in ("localhost", "127.0.0.1", "::1")


class MoodleClient:
    """Минимальный клиент REST веб-служб Moodle."""

    def __init__(self, base_url: str, token: str):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.http = httpx.Client(
            timeout=60.0,
            headers={"User-Agent": "academic-risk-1c/etl"},
            trust_env=not _is_local(base_url),
        )

    def call(self, function: str, **params):
        data = {"wstoken": self.token, "wsfunction": function, "moodlewsrestformat": "json"}
        for key, value in params.items():
            if isinstance(value, list):
                for i, item in enumerate(value):
                    data[f"{key}[{i}]"] = str(item)
            else:
                data[key] = str(value)
        response = self.http.post(f"{self.base_url}/webservice/rest/server.php", data=data)
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, dict) and "exception" in payload:
            raise MoodleError(f"{function}: {payload.get('message', payload['exception'])}")
        return payload

    def close(self) -> None:
        self.http.close()


def username_hash(salt: str, username: str) -> str:
    """Псевдоним логина: sha256 от соли и логина, без исходного значения."""
    return hashlib.sha256((salt + username.lower()).encode("utf-8")).hexdigest()[:32]


def semester_week(adm_year: int, sem_max: int, day: date) -> tuple[int, int] | None:
    """Семестр и неделя события по учебному календарю студента.

    Возврат None: событие до начала обучения или после конца программы.
    Неделя 0: межсессионный период (после конца семестра, до начала
    следующего). Нумерация недель с 1.
    """
    current = None
    for s in range(1, sem_max + 1):
        if semester_dates(adm_year, s)[0] <= day:
            current = s
        else:
            break
    if current is None:
        return None
    start, end = semester_dates(adm_year, current)
    if day > end:
        if current >= sem_max:
            return None  # обучение завершено
        current += 1
        start, _ = semester_dates(adm_year, current)
        if day < start:
            return current, 0
    return current, (day - start).days // 7 + 1


def build_student_map(client: MoodleClient, salt: str, conn) -> int:
    """Заполнить enriched.student_map по участникам курсов Moodle.

    Студент связывается, если его логин (зачётка) найден в 1С-контингенте.
    """
    courses = client.call("core_course_get_courses")
    usernames = set()
    for course in courses:
        for user in client.call("core_enrol_get_enrolled_users", courseid=course["id"]):
            usernames.add(user.get("username", "").lower())

    with conn.cursor() as cur:
        cur.execute("SELECT student_id FROM cleaned.vs_students")
        known_ids = {row[0].lower() for row in cur.fetchall()}

    matched = 0
    with conn.cursor() as cur:
        cur.execute("TRUNCATE TABLE enriched.student_map")
        for username in sorted(usernames & known_ids):
            cur.execute(
                """INSERT INTO enriched.student_map (student_id, username_hash, linked_at)
                   VALUES (%s, %s, %s)
                   ON CONFLICT (student_id) DO UPDATE
                       SET username_hash = EXCLUDED.username_hash,
                           linked_at = EXCLUDED.linked_at""",
                (username, username_hash(salt, username), datetime.now(timezone.utc)),
            )
            matched += 1
    conn.commit()
    return matched


def collect_activity(client: MoodleClient, salt: str, conn) -> dict:
    """Выгрузить активность по курсам и собрать действия по псевдонимам.

    Действия (сдача работы, попытка теста, сообщение на форуме) связываются
    со студентами только через хеш логина; идентификатор пользователя Moodle
    служебный и в enriched не попадает.
    """
    with conn.cursor() as cur:
        cur.execute(
            """SELECT m.username_hash, m.student_id, st.admission_year, st.program_id
               FROM enriched.student_map m
               JOIN cleaned.vs_students st USING (student_id)"""
        )
        students = {row[0]: (row[1], row[2], row[3]) for row in cur.fetchall()}

    moodle_usernames: dict[int, str] = {}
    enrolled_users: set[int] = set()
    active_users: set[int] = set()
    events_by_student: dict[str, list[tuple[date, int]]] = defaultdict(list)
    grades: dict[str, int] = defaultdict(int)
    submissions: dict[str, int] = defaultdict(int)
    attempts: dict[str, int] = defaultdict(int)
    discussions: dict[str, int] = defaultdict(int)

    def add_action(timestamp: int | None, user_id: int | None, course_id: int) -> None:
        """Зарегистрировать действие пользователя в день и курс."""
        if timestamp is None:
            return
        active_users.add(user_id)
        h = moodle_usernames.get(user_id)
        if h in students:
            day = datetime.fromtimestamp(timestamp, timezone.utc).date()
            events_by_student[h].append((day, course_id))

    courses = client.call("core_course_get_courses")
    n_events = 0
    for course in courses:
        course_id = course["id"]
        for user in client.call("core_enrol_get_enrolled_users", courseid=course_id):
            if user.get("username"):
                moodle_usernames[user["id"]] = username_hash(salt, user["username"])
            enrolled_users.add(user["id"])

        # Сданные работы: время сдачи.
        assignments = client.call("mod_assign_get_assignments", courseids=[course_id])
        for course_block in assignments.get("courses", []):
            for task in course_block.get("assignments", []):
                sub = client.call("mod_assign_get_submissions", assignmentids=[task["id"]])
                for block in sub.get("assignments", []):
                    for item in block.get("submissions", []):
                        submissions[moodle_usernames.get(item.get("userid"))] += 1
                        add_action(item.get("timemodified"), item.get("userid"), course_id)
                        n_events += 1

        # Попытки тестов: время завершения.
        quizzes = client.call("mod_quiz_get_quizzes_by_courses", courseids=[course_id])
        quiz_ids = []
        for quiz_block in quizzes.get("quizzes", []):
            quiz_ids += [q["id"] for q in quiz_block.get("quizzes", [])]
        if quiz_ids:
            attempts_data = client.call("mod_quiz_get_user_attempts", quizids=quiz_ids)
            for attempt in attempts_data.get("attempts", []):
                attempts[moodle_usernames.get(attempt.get("userid"))] += 1
                add_action(attempt.get("timefinish") or attempt.get("timestart"),
                           attempt.get("userid"), course_id)
                n_events += 1

        # Обсуждения на форумах: создание и последний ответ.
        forums = client.call("mod_forum_get_forums_by_courses", courseids=[course_id])
        for forum in forums:
            data = client.call("mod_forum_get_forum_discussions", forumid=forum["id"])
            for d in data.get("discussions", []):
                discussions[moodle_usernames.get(d.get("userid"))] += 1
                add_action(d.get("created"), d.get("userid"), course_id)
                if d.get("timemodified") and d.get("timemodified") != d.get("created"):
                    add_action(d.get("timemodified"), d.get("usermodified"), course_id)
                    n_events += 1
                n_events += 1

        # Оценки: счётчик элементов без времени.
        grades_data = client.call("gradereport_user_get_grade_items", courseid=course_id)
        for user_grades in grades_data.get("usergrades", []):
            h = moodle_usernames.get(user_grades.get("userid"))
            if h in students:
                grades[h] += len(user_grades.get("gradeitems", []))

    return {
        "n_courses": len(courses),
        "n_events": n_events,
        "students": students,
        "events_by_student": events_by_student,
        "grades": grades,
        "submissions": submissions,
        "attempts": attempts,
        "discussions": discussions,
        "enrolled_users": enrolled_users,
        "active_users": active_users,
    }


def load_activity(summary: dict, conn) -> tuple[int, int]:
    """Собрать недельную динамику и агрегаты, загрузить в enriched.

    Возвращает число недельных записей и число агрегатов.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT program_id, max(semester) FROM cleaned.vs_load GROUP BY program_id")
        sem_max_by_program = dict(cur.fetchall())

    weekly = {}
    for h, (student_id, adm_year, program_id) in summary["students"].items():
        events = summary["events_by_student"].get(h, [])
        sem_max = sem_max_by_program.get(program_id)
        if not events or sem_max is None:
            continue
        for day, course_id in events:
            bucket = semester_week(adm_year, sem_max, day)
            if bucket is None:
                continue
            semester_no, week = bucket
            key = (student_id, semester_no, week)
            rec = weekly.setdefault(key, {"events": 0, "days": set(), "courses": set()})
            rec["events"] += 1
            rec["days"].add(day)
            rec["courses"].add(course_id)

    agg = defaultdict(lambda: {"events": 0, "days": set(), "weeks": [], "courses": set()})
    for (student_id, semester_no, week), rec in weekly.items():
        a = agg[(student_id, semester_no)]
        a["events"] += rec["events"]
        a["days"] |= rec["days"]
        a["weeks"].append(week)
        a["courses"] |= rec["courses"]

    with conn.cursor() as cur:
        cur.execute("TRUNCATE TABLE enriched.activity_weekly")
        cur.execute("TRUNCATE TABLE enriched.activity_agg")
        for (student_id, semester_no, week), rec in sorted(weekly.items()):
            cur.execute(
                """INSERT INTO enriched.activity_weekly
                   (student_id, semester_no, week_number, events, active_days, courses)
                   VALUES (%s, %s, %s, %s, %s, %s)""",
                (student_id, semester_no, week, rec["events"], len(rec["days"]),
                 len(rec["courses"])),
            )
        for (student_id, semester_no), a in sorted(agg.items()):
            cur.execute(
                """INSERT INTO enriched.activity_agg
                   (student_id, semester_no, events_total, active_days,
                    first_week, last_week, courses, grades_count,
                    submissions, quiz_attempts)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (student_id, semester_no, a["events"], len(a["days"]),
                 min(a["weeks"]), max(a["weeks"]), len(a["courses"]), 0, 0, 0),
            )
        # Оценки, работы и попытки: суммарно за весь период наблюдения
        # (дисциплинарная привязка отложена в backlog), псевдоним ключа.
        for h, (student_id, _, _) in summary["students"].items():
            if not agg or not any(k[0] == student_id for k in agg):
                continue
            cur.execute(
                """UPDATE enriched.activity_agg
                   SET grades_count = %s, submissions = %s, quiz_attempts = %s
                   WHERE student_id = %s""",
                (summary["grades"].get(h, 0), summary["submissions"].get(h, 0),
                 summary["attempts"].get(h, 0), student_id),
            )
    conn.commit()
    return len(weekly), len(agg)


def main() -> None:
    from dotenv import load_dotenv

    parser = argparse.ArgumentParser(description="ETL Moodle по веб-службам")
    parser.add_argument("--mock", action="store_true",
                        help="демонстрационный контур против локального эмулятора")
    args = parser.parse_args()

    load_dotenv(ROOT / ".env")
    salt = os.getenv("MOODLE_SALT", "")
    if not salt:
        print("MOODLE_SALT не задан в .env, выгрузка не выполняется")
        return
    if args.mock:
        base_url = "http://127.0.0.1:8010"
        token = "mocktoken"
    else:
        base_url = os.getenv("MOODLE_URL", "")
        token = os.getenv("MOODLE_TOKEN", "")
        if not base_url or not token:
            print("MOODLE_URL и MOODLE_TOKEN не заданы в .env, выгрузка не выполняется")
            return

    client = MoodleClient(base_url, token)
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute((ROOT / "sql" / "enriched.sql").read_text(encoding="utf-8"))
        conn.commit()

        try:
            info = client.call("core_webservice_get_site_info")
            print(f"связь установлена: {info.get('sitename')} (пользователь службы: {info.get('username')})")
        except MoodleError as error:
            print(f"связь установлена (информация о службе недоступна: {error})")

        matched = build_student_map(client, salt, conn)
        print(f"связано студентов по логину-зачётке: {matched}")

        summary = collect_activity(client, salt, conn)
        n_weeks, n_agg = load_activity(summary, conn)
        with_active = len(summary["events_by_student"])
        print(f"курсов: {summary['n_courses']}, действий студентов контингента: {summary['n_events']}")
        print(f"студентов с активностью: {with_active}, недельных записей: {n_weeks}, агрегатов: {n_agg}")
        coverage = f"{with_active / matched:.0%}" if matched else "-"
        print(f"покрытие активностью (замер через API): {coverage}")
        enrolled = len(summary["enrolled_users"])
        active = len(summary["active_users"])
        if enrolled:
            print(f"пользователей Moodle на курсах: {enrolled}, "
                  f"с событиями: {active} ({active / enrolled:.0%})")
    finally:
        conn.close()
        client.close()


if __name__ == "__main__":
    main()
