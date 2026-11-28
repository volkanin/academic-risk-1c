"""Тесты клиента и приемника HTTP-сервиса расширения 1С.

Все данные фиктивные: идентификаторы синтетические, номера зачетных книжек
и даты рождения взяты тестовые, персональных данных нет (NFR-2).
"""

import httpx
import pytest

from onec_client.client import OneCClient, OneCServiceError, book_hash
from onec_client.load import age_at, order_class, semester_num, transform_students

import json

BASE = "http://localhost/uni/hs/analytics/v1"
SALT = "test-salt"


def envelope(rows, cursor=None):
    return {"data": rows, "meta": {"count": len(rows), "nextCursor": cursor,
                                   "asOf": "2026-11-28", "schemaVersion": "1"},
            "errors": []}


def make_client(handler, max_retries=3):
    transport = httpx.MockTransport(handler)
    return OneCClient(BASE, "ws", "", transport=transport, max_retries=max_retries)


# ---------- пагинация и устойчивость ----------


def test_walk_follows_cursor_and_stops():
    by_cursor = {"": ["a1", "a2"], "c2": ["b1"]}

    def handler(request):
        cursor = request.url.params.get("cursor") or ""
        return httpx.Response(200, json=envelope(by_cursor[cursor],
                                                 "c2" if cursor == "" else None))

    client = make_client(handler)
    try:
        rows = client.fetch("dim/students")
        assert rows == ["a1", "a2", "b1"]
    finally:
        client.close()


def test_cursor_loop_detected():
    by_cursor = {"": ["a1"], "c2": ["b1"]}

    def handler(request):
        cursor = request.url.params.get("cursor") or ""
        return httpx.Response(200, json=envelope(by_cursor[cursor], "c2" if cursor == "" else "c2"))

    client = make_client(handler)
    try:
        with pytest.raises(OneCServiceError, match="зациклился"):
            client.fetch("dim/students")
    finally:
        client.close()


def test_retry_on_server_error(monkeypatch):
    monkeypatch.setattr("onec_client.client.time.sleep", lambda seconds: None)
    attempts = {"n": 0}

    def handler(request):
        attempts["n"] += 1
        if attempts["n"] == 1:
            return httpx.Response(503, text="перезапуск")
        return httpx.Response(200, json=envelope([{"student_id": "x"}]))

    client = make_client(handler)
    try:
        rows = client.fetch("dim/students")
        assert len(rows) == 1
        assert attempts["n"] == 2
    finally:
        client.close()


def test_error_envelope_raises():
    def handler(request):
        return httpx.Response(400, json={"data": [], "meta": {}, "errors":
                                         [{"code": "badCursor", "message": "Параметр cursor поврежден"}]})

    client = make_client(handler)
    try:
        with pytest.raises(OneCServiceError) as error:
            client.fetch("dim/students")
        assert error.value.status_code == 400
        assert "cursor поврежден" in error.value.message
    finally:
        client.close()


def test_events_deduplicated_by_event_id():
    by_cursor = {
        "": [{"event_id": "grade:u1:1", "kind": "grade"}, {"event_id": "grade:u1:2", "kind": "grade"}],
        "c2": [{"event_id": "grade:u1:2", "kind": "grade"}, {"event_id": "grade:u1:3", "kind": "grade"}],
    }

    def handler(request):
        cursor = request.url.params.get("cursor") or ""
        return httpx.Response(200, json=envelope(by_cursor[cursor],
                                                 "c2" if cursor == "" else None))

    client = make_client(handler)
    try:
        events, duplicates = client.fetch_events()
        assert [event["event_id"] for event in events] == ["grade:u1:1", "grade:u1:2", "grade:u1:3"]
        assert duplicates == 1
    finally:
        client.close()


# ---------- псевдонимы и служебные методы ----------


def test_book_hash_matches_moodle_username_hash():
    from enriched.moodle_etl import username_hash

    # один и тот же алгоритм и соль: совпадение значений дает совпадение хешей
    assert book_hash(SALT, "Тест-Книга01") == username_hash(SALT, "тест-книга01")
    assert book_hash(SALT, "Тест-Книга01") != book_hash("other-salt", "Тест-Книга01")


def test_health_returns_bare_object():
    def handler(request):
        return httpx.Response(200, json={"state": "ok", "version": "1.0.0.1"})

    client = make_client(handler)
    try:
        assert client.health()["state"] == "ok"
    finally:
        client.close()


def test_scoring_batch_sends_contract():
    captured = {}

    def handler(request):
        captured["body"] = request.read()
        return httpx.Response(200, json=envelope({"status": "ok", "calculationId": "calc-1", "accepted": 1}))

    client = make_client(handler)
    try:
        item = {"studentId": "00000000-0000-4000-8000-000000000001",
                "riskProbability": 0.42, "riskCategory": "высокий",
                "modelVersion": "test-1", "factors": {"gpa": 4.0}}
        result = client.scoring_batch("calc-1", [item], calculation_date="2026-11-28")
        assert result["accepted"] == 1
        import json
        body = json.loads(captured["body"])
        assert body["calculationId"] == "calc-1"
        assert body["calculationDate"] == "2026-11-28"
        assert body["items"][0]["studentId"].startswith("00000000")
    finally:
        client.close()


def test_scoring_batch_rejects_wrong_size():
    client = make_client(lambda request: httpx.Response(500))
    try:
        with pytest.raises(ValueError):
            client.scoring_batch("calc-1", [])
        with pytest.raises(ValueError):
            client.scoring_batch("calc-1", [{"i": n} for n in range(5001)])
    finally:
        client.close()


# ---------- приемник: преобразования без персональных данных ----------


def test_semester_num_parses_ordinals():
    assert semester_num("Первый семестр") == 1
    assert semester_num("Двенадцатый семестр") == 12
    assert semester_num("Четвёртый семестр") == 4
    assert semester_num("Летняя практика") is None
    assert semester_num("") is None


def test_order_class_by_analytics_and_kind():
    assert order_class("За академическую неуспеваемость", "") == "академическое"
    assert order_class("", "Отчисление за академическую неуспеваемость") == "академическое"
    assert order_class("Невыполнение условий договора об оказании платных образовательных услуг",
                       "") == "финансовое"
    assert order_class("", "Отчисление по собственному желанию") == "нейтральное"
    assert order_class("Восстановление в число студентов", "") == "нейтральное"


def test_age_at_full_years():
    assert age_at("2000-06-15", __import__("datetime").date(2026, 11, 28)) == 26
    assert age_at("2000-12-31", __import__("datetime").date(2026, 11, 28)) == 25
    assert age_at("", __import__("datetime").date(2026, 11, 28)) is None


def test_transform_students_pseudonymizes():
    row = {"student_id": "00000000-0000-4000-8000-000000000001", "book_number": "Тест-Книга01",
           "program_id": "p1", "group_code": "101", "faculty": "институт", "study_form": "очная",
           "basis": "бюджетная основа", "gender": "Женский", "birth_date": "2003-03-10",
           "region": "", "hostel": None, "privilege": None, "state": "Является студентом"}
    rows = transform_students([row], SALT, __import__("datetime").date(2026, 11, 28))
    values = dict(zip(("student_id", "book_hash", "program_id", "group_code", "faculty",
                       "study_form", "basis", "gender", "age", "region", "hostel",
                       "privilege", "state"), rows[0]))
    assert values["book_hash"] == book_hash(SALT, "Тест-Книга01")
    assert values["age"] == 23
    assert values["state"] == "Является студентом"
    # номера книжки и даты рождения в строке нет ни в каком виде
    flat = repr(rows[0])
    assert "Тест-Книга01" not in flat
    assert "2003-03-10" not in flat


def test_unknown_field_stops_load():
    from onec_client.load import _check_keys

    with pytest.raises(ValueError, match="персональные данные"):
        _check_keys([{"student_id": "x", "full_name": "Тестовый Студент"}], "students")


def test_transform_movements_classifies():
    from onec_client.load import transform_movements

    row = {"student_id": "s1", "book_number": "Тест-Книга02", "order_type": "Отчисление",
           "order_kind": "Отчисление за академическую неуспеваемость", "analytics": "",
           "reason": "", "order_date": "2026-02-16", "effective_date": "2026-02-16",
           "program_id": "p1", "group_code": "101"}
    rows = transform_movements([row], SALT)
    assert rows[0][-1] == "академическое"
    assert rows[0][1] == book_hash(SALT, "Тест-Книга02")
