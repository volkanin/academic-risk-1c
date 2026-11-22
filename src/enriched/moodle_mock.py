"""Локальный эмулятор веб-служб Moodle для демонстрационного контура.

Отдаёт фикстуры в формате REST API Moodle на 127.0.0.1:8010, чтобы ETL
(src/enriched/moodle_etl.py --mock) работал по настоящему REST-контуру до
подключения реального сервера. Логины фикстур берутся из синтетического
контингента (student_id имитирует зачётку). Активность задана действиями
стандартных функций Moodle 4.5 (сданные работы, попытки тестов, обсуждения
на форумах): журнал событий core_log_get_logs в 4.5 как внешняя функция
отсутствует. При подключении реального Moodle эмулятор не нужен: ETL
переключается адресом и токеном в .env.

Запуск: .venv/Scripts/python -I src/enriched/moodle_mock.py
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from utils.calendar import semester_dates  # noqa: E402
from utils.db import connect  # noqa: E402

PORT = 8010

# Пользователи: логин = student_id; активность задана профилем:
# user 1 активен почти каждую неделю, user 2 в половину недель,
# user 3 редко, user 4 и 5 участников курсов без действий.
ACTIVE_WEEKS = {0: range(1, 16), 1: range(2, 16, 2), 2: (1, 6, 11)}
EVENT_SEMESTERS = (3, 4)

COURSES = [
    {"id": 101, "shortname": "ARCH-1-2024", "fullname": "Архитектурное проектирование 1"},
    {"id": 102, "shortname": "DESIGN-2-2024", "fullname": "Проектирование в графическом дизайне"},
]

ENROLLED = {
    101: [1, 2, 4, 5],
    102: [1, 3],
}

FORUMS = {
    101: [{"id": 1101, "course": 101, "name": "Обсуждение проектов"}],
    102: [{"id": 1102, "course": 102, "name": "Обсуждение макетов"}],
}

ASSIGNMENTS = {101: [{"id": 9001}]}
QUIZZES = {101: [{"id": 8001}]}

USERS: list[dict] = []
GLOBAL: dict = {}


def load_users() -> list[dict]:
    """Пять студентов контингента: логин, год поступления, программа."""
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT student_id, admission_year, program_id FROM cleaned.vs_students LIMIT 5")
            rows = cur.fetchall()
    finally:
        conn.close()
    return [{"username": r[0], "adm_year": r[1], "program": r[2]} for r in rows]


def ts(start, offset: int, hour: int = 10) -> int:
    day = start + timedelta(days=offset)
    return int(datetime(day.year, day.month, day.day, hour, 0, tzinfo=timezone.utc).timestamp())


def build_fixtures() -> None:
    """Дискуссии, работы и попытки с временами по неделям семестров."""
    discussions: dict[int, list] = {1101: [], 1102: []}
    submissions: list = []
    attempts: list = []
    for i in range(3):
        for s in EVENT_SEMESTERS:
            start, _ = semester_dates(USERS[i]["adm_year"], s)
            forum = 1101 if s % 2 == 1 else 1102
            course = 101 if s % 2 == 1 else 102
            for w in ACTIVE_WEEKS.get(i, []):
                offset = (w - 1) * 7 + i % 7
                discussions[forum].append({
                    "discussion": 20000 + i * 100 + w,
                    "userid": i + 1,
                    "usermodified": i + 1,
                    "created": ts(start, offset, 10),
                    "timemodified": ts(start, offset, 12),
                })
            if i == 0:
                submissions.append({"userid": 1, "status": "submitted", "timemodified": ts(start, 21, 14)})
                attempts.append({"userid": 1, "quiz": 8001, "state": "finished",
                                 "timestart": ts(start, 23, 9), "timefinish": ts(start, 23, 11)})
    GLOBAL["discussions"] = discussions
    GLOBAL["submissions"] = submissions
    GLOBAL["attempts"] = attempts


def handle(wsfunction: str, params: dict):
    if wsfunction == "core_webservice_get_site_info":
        return {"sitename": "Учебный Moodle (демонстрационный контур)",
                "username": "svc_mlreader", "userid": 777}
    if wsfunction == "core_course_get_courses":
        return COURSES
    if wsfunction == "core_enrol_get_enrolled_users":
        course_id = int(params.get("courseid", 0))
        return [{"id": u, "username": USERS[u - 1]["username"]}
                for u in ENROLLED.get(course_id, [])]
    if wsfunction == "mod_assign_get_assignments":
        course_id = int(params.get("courseids[0]", 0))
        return {"courses": [{"id": course_id, "assignments": ASSIGNMENTS.get(course_id, [])}]}
    if wsfunction == "mod_assign_get_submissions":
        assignment_id = int(params.get("assignmentids[0]", 0))
        return {"assignments": [{"assignmentid": assignment_id,
                                 "submissions": GLOBAL["submissions"]}]}
    if wsfunction == "mod_quiz_get_quizzes_by_courses":
        course_id = int(params.get("courseids[0]", 0))
        return {"quizzes": [{"courseid": course_id, "quizzes": QUIZZES.get(course_id, [])}]}
    if wsfunction == "mod_quiz_get_user_attempts":
        return {"attempts": GLOBAL["attempts"]}
    if wsfunction == "mod_forum_get_forums_by_courses":
        course_id = int(params.get("courseids[0]", 0))
        return FORUMS.get(course_id, [])
    if wsfunction == "mod_forum_get_forum_discussions":
        forum_id = int(params.get("forumid", 0))
        return {"discussions": GLOBAL["discussions"].get(forum_id, [])}
    if wsfunction == "gradereport_user_get_grade_items":
        return {"usergrades": [
            {"userid": 1, "gradeitems": [{"itemname": f"Задание {k}"} for k in range(1, 5)]},
        ]}
    return {"exception": "invalidfunction", "message": f"Функция не поддерживается: {wsfunction}"}


class Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        try:
            length = int(self.headers.get("Content-Length", 0))
            params = {k: v[0] for k, v in parse_qs(self.rfile.read(length).decode("utf-8")).items()}
            token = params.get("wstoken", "")
            if token != "mocktoken":
                payload = {"exception": "invalidtoken", "message": "Недействительный токен"}
            else:
                payload = handle(params.get("wsfunction", ""), params)
            status = 200
        except Exception as error:  # диагностика: ошибка обработки в ответе
            payload = {"exception": "servererror", "message": repr(error)}
            status = 500
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args) -> None:
        print(fmt % args)


def main() -> None:
    global USERS
    USERS = load_users()
    build_fixtures()
    print(f"эмулятор Moodle на http://127.0.0.1:{PORT}, пользователей: {len(USERS)}")
    print(f"логины имитируют зачётки: {[u['username'][:8] + '...' for u in USERS]}")
    HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
