"""Producer потока событий успеваемости и оплат (блок 5, FR-7).

В реальном контуре события публикует HTTP-сервис 1С по ТЗ: оценка введена,
задолженность возникла или закрыта, платёж внесён или просрочен. В
демонстрационном контуре producer формирует поток из витрин cleaned: записи
оценок, задолженностей и оплат как поток событий изменения состояния
студента. Каждое событие: ключ партиции student_id, значение JSON с
event_id (для дедупликации потребителем), видом события и полезной
нагрузкой.

Запуск: .venv/Scripts/python -I streaming/producer.py --count 300
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from confluent_kafka import Producer, KafkaError
from confluent_kafka.admin import AdminClient, NewTopic
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TOPIC = "ar1c.events"


def ensure_topic(bootstrap: str, topic: str, partitions: int = 3) -> bool:
    """Создать топик, если его нет. Возвращает True, если создан."""
    admin = AdminClient({"bootstrap.servers": bootstrap})
    if topic in admin.list_topics(timeout=10).topics:
        return False
    futures = admin.create_topics([NewTopic(topic, num_partitions=partitions, replication_factor=1)])
    for future in futures.values():
        future.result(timeout=30)
    return True


def query_events(conn, count: int) -> list[dict]:
    """События из витрин cleaned: оценки, задолженности, оплаты.

    Каждая запись витрины транслируется в событие изменения состояния
    студента, как это сделал бы HTTP-сервис 1С в реальном контуре.
    """
    events = []
    per_kind = max(1, count // 3)
    with conn.cursor() as cur:
        cur.execute(
            """SELECT student_id, exam_date, grade, discipline_id, semester
               FROM cleaned.vs_grades
               ORDER BY exam_date DESC, entered_at DESC LIMIT %s""",
            (per_kind,),
        )
        for row in cur.fetchall():
            events.append({
                "kind": "grade",
                "student_id": row[0],
                "occurred_at": row[1].isoformat(),
                "payload": {"discipline": row[3], "grade": row[2], "semester": row[4]},
            })
        cur.execute(
            """SELECT student_id, arose_date, closed_date, discipline_id
               FROM cleaned.vs_debts
               ORDER BY arose_date DESC LIMIT %s""",
            (per_kind,),
        )
        for row in cur.fetchall():
            kind = "debt_closed" if row[2] else "debt_open"
            occurred = row[2] or row[1]
            events.append({
                "kind": kind,
                "student_id": row[0],
                "occurred_at": occurred.isoformat(),
                "payload": {"discipline": row[3], "arose_date": row[1].isoformat(),
                            "closed_date": row[2].isoformat() if row[2] else None},
            })
        cur.execute(
            """SELECT student_id, paid_date, amount, overdue_days, accrual_date
               FROM cleaned.vs_finance
               ORDER BY accrual_date DESC LIMIT %s""",
            (per_kind,),
        )
        for row in cur.fetchall():
            events.append({
                "kind": "payment",
                "student_id": row[0],
                "occurred_at": (row[1] or row[4]).isoformat(),
                "payload": {"amount": float(row[2]), "overdue_days": row[3],
                            "accrual_date": row[4].isoformat()},
            })
    return events[:count] if len(events) > count else events


def main() -> None:
    from dotenv import load_dotenv

    parser = argparse.ArgumentParser(description="Публикация событий успеваемости и оплат в Kafka")
    parser.add_argument("--count", type=int, default=300, help="число событий в потоке")
    parser.add_argument("--topic", default=os.getenv("KAFKA_TOPIC", DEFAULT_TOPIC))
    parser.add_argument("--out", help="сохранить поток событий в файл JSON")
    parser.add_argument("--in", dest="in_file", help="опубликовать поток событий из файла (повтор той же доставки)")
    args = parser.parse_args()

    load_dotenv(ROOT / ".env")
    bootstrap = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")

    created = ensure_topic(bootstrap, args.topic)
    print(f"топик {args.topic} {'создан' if created else 'уже существует'}")

    if args.in_file:
        events = json.loads(Path(args.in_file).read_text(encoding="utf-8"))
    else:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
        from utils.db import connect

        conn = connect()
        try:
            events = query_events(conn, args.count)
        finally:
            conn.close()
        # идентификатор сохраняется при повторной публикации той же доставки
        # (файл --in), чтобы потребитель отбросил дубликаты
        for event in events:
            event.setdefault("event_id", uuid.uuid4().hex)
        if args.out:
            Path(args.out).write_text(json.dumps(events, ensure_ascii=False), encoding="utf-8")

    producer = Producer({"bootstrap.servers": bootstrap})
    delivered = 0
    kinds: dict[str, int] = {}

    def delivery_report(err, msg) -> None:
        nonlocal delivered
        if err is not None and err.code() != KafkaError.NO_ERROR:
            print(f"доставка не удалась: {err}")
        else:
            delivered += 1

    for event in events:
        producer.produce(
            args.topic,
            key=event["student_id"],
            value=json.dumps(event, ensure_ascii=False).encode("utf-8"),
            on_delivery=delivery_report,
        )
        producer.poll(0)
    producer.flush(30)

    for event in events:
        kinds[event["kind"]] = kinds.get(event["kind"], 0) + 1
    print(f"опубликовано событий: {delivered} из {len(events)}")
    print("по видам:", ", ".join(f"{k}: {v}" for k, v in sorted(kinds.items())))


if __name__ == "__main__":
    main()
