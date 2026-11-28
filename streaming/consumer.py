"""Consumer потока событий успеваемости и оплат (блок 5, FR-7).

Группа потребителей ar1c-feature-bridge обновляет online-хранилище признаков
(Redis): по каждому студенту инкрементально накапливает счётчики событий и
последние значения, ключ fs:student:<student_id>. Автоматическая фиксация
смещения отключена: offset фиксируется только после успешной записи партии
в Redis (at-least-once). Повтор после сбоя мог бы доставить те же события,
поэтому дедупликация по event_id в множестве fs:kafka:seen; в
производственном контуре вместо неё используют идемпотентную запись или
транзакции exactly-once.

Метаданные потребителя: ключ fs:kafka:meta (потреблено, записано,
дубликаты, последнее время события).

Запуск: .venv/Scripts/python -I streaming/consumer.py [--max-events 300]
Без ограничений потребитель работает до Ctrl+C (регламентная выгрузка).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import redis
from confluent_kafka import Consumer, KafkaError, TopicPartition

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TOPIC = "ar1c.events"
META_KEY = "fs:kafka:meta"
SEEN_KEY = "fs:kafka:seen"
STUDENT_KEY = "fs:student:"
BATCH_SIZE = 200
BATCH_TIMEOUT = 2.0


def process_batch(consumer, r, batch: list) -> tuple[int, int, int]:
    """Обработать партию сообщений: дедупликация, запись в Redis, commit.

    Возвращает (записано, дубликатов, вне материализации). Дедупликация по
    event_id; пишутся только студенты, присутствующие в материализации
    online-хранилища: события студентов вне среза учтутся при следующей
    материализации, частичные ключи не создаются. Смещение фиксируется
    только после успешной записи партии.
    """
    pipe = r.pipeline()
    for msg in batch:
        pipe.sadd(SEEN_KEY, msg["event_id"])
        pipe.exists(f"{STUDENT_KEY}{msg['student_id']}")
    raw_results = pipe.execute()
    seen_flags, exist_flags = raw_results[0::2], raw_results[1::2]
    new_items = [msg for msg, seen, present in zip(batch, seen_flags, exist_flags)
                 if seen == 1 and present == 1]
    duplicates = sum(1 for seen in seen_flags if seen == 0)
    outside = sum(1 for msg, seen, present in zip(batch, seen_flags, exist_flags)
                  if seen == 1 and present == 0)

    if new_items:
        pipe = r.pipeline()
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        for msg in new_items:
            key = f"{STUDENT_KEY}{msg['student_id']}"
            pipe.hincrby(key, f"n_{msg['kind']}_events", 1)
            pipe.hset(key, mapping={
                f"last_{msg['kind']}_at": msg["occurred_at"],
                "last_event_at": now,
                "last_event_kind": msg["kind"],
            })
        pipe.hincrby(META_KEY, "events_written", len(new_items))
        pipe.hset(META_KEY, "last_event_at", now)
        pipe.execute()

    # На партицию фиксируется наибольший обработанный offset: список
    # TopicPartition с повторяющимися партициями librdkafka применяет не
    # по последнему значению, что привело бы к недокоммиту и перечитыванию.
    highest: dict[int, int] = {}
    for msg in batch:
        p = msg["_tp"].partition
        highest[p] = max(highest.get(p, 0), msg["_offset"] + 1)
    topic = batch[0]["_tp"].topic
    offsets = [TopicPartition(topic, p, o) for p, o in sorted(highest.items())]
    consumer.commit(offsets=offsets, asynchronous=False)
    pipe = r.pipeline()
    for tp in offsets:
        pipe.hset(META_KEY, f"offset:{tp.topic}:{tp.partition}", tp.offset)
    pipe.hincrby(META_KEY, "events_consumed", len(batch))
    pipe.hincrby(META_KEY, "events_duplicated", duplicates)
    pipe.hincrby(META_KEY, "events_outside_snapshot", outside)
    pipe.execute()
    return len(new_items), duplicates, outside


def main() -> None:
    from dotenv import load_dotenv

    parser = argparse.ArgumentParser(description="Потребление событий в online-хранилище признаков")
    parser.add_argument("--topic", default=os.getenv("KAFKA_TOPIC", DEFAULT_TOPIC))
    parser.add_argument("--group", default="ar1c-feature-bridge")
    parser.add_argument("--max-events", type=int, help="обработать N событий и выйти")
    args = parser.parse_args()

    load_dotenv(ROOT / ".env")
    bootstrap = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")

    r = redis.Redis(
        host=os.getenv("REDIS_HOST", "localhost"),
        port=int(os.getenv("REDIS_PORT", "6379")),
        decode_responses=True,
    )
    consumer = Consumer({
        "bootstrap.servers": bootstrap,
        "group.id": args.group,
        "enable.auto.commit": False,
        "auto.offset.reset": "earliest",
    })
    consumer.subscribe([args.topic])
    print(f"подписка на {args.topic}, группа {args.group}, фиксация смещения после записи")

    consumed = written = duplicates = outside = 0
    batch: list = []

    def flush_batch() -> None:
        """Обработать накопленную партию: запись в Redis, затем commit."""
        nonlocal consumed, written, duplicates, outside, batch
        if batch:
            w, d, o = process_batch(consumer, r, batch)
            written += w
            duplicates += d
            outside += o
            consumed += len(batch)
            batch = []

    try:
        while True:
            raw = consumer.poll(1.0)
            if raw is None:
                flush_batch()
            elif raw.error():
                if raw.error().code() == KafkaError._PARTITION_EOF:
                    continue
                print(f"ошибка потребления: {raw.error()}")
            else:
                msg = json.loads(raw.value().decode("utf-8"))
                msg["_tp"] = TopicPartition(raw.topic(), raw.partition())
                msg["_offset"] = raw.offset()
                batch.append(msg)
                if len(batch) >= BATCH_SIZE:
                    flush_batch()
            if args.max_events and consumed >= args.max_events:
                break
    except KeyboardInterrupt:
        pass
    finally:
        flush_batch()
        consumer.close()

    print(f"потреблено: {consumed}, записано: {written}, дубликатов: {duplicates}, "
          f"вне материализации: {outside}")
    meta = r.hgetall(META_KEY)
    print("метаданные:", {k: meta[k] for k in sorted(meta) if not k.startswith("offset:")})


if __name__ == "__main__":
    main()
