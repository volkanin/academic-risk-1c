# Потоковая обработка событий (блок 5, FR-7)

События успеваемости и оплат идут через Kafka; группа потребителей
фиксирует смещение только после успешной записи в хранилище. Инфраструктура:
контейнер kafka в deployment/docker-compose.yml (Apache Kafka 4.0 в режиме
KRaft, zookeeper не требуется), топик ar1c.events (три партиции, ключ
партиции student_id).

## События

В реальном контуре публикует HTTP-сервис 1С по ТЗ: оценка введена,
задолженность возникла или закрыта, платёж внесён. В демонстрационном
контуре producer формирует поток из витрин cleaned, транслируя записи
оценок, задолженностей и оплат в события изменения состояния студента.
Событие: ключ student_id, значение JSON: event_id (uuid), kind
(grade, debt_open, debt_closed, payment), occurred_at, payload.

## Producer

producer.py: создаёт топик при отсутствии (AdminClient), публикует поток,
печатает сводку по видам. Опции: --count N (число событий), --out FILE
(сохранить поток вместе с event_id), --in FILE (опубликовать сохранённый
поток повторно: те же идентификаторы, потребитель отбрасывает дубликаты).
Опции работают и как проверка повторяемости доставки.

## Consumer

consumer.py: группа ar1c-feature-bridge, автоматическая фиксация смещения
отключена: партия событий (до 200 или 2 секунды) записывается в Redis
единым конвейером, затем смещение фиксируется синхронно (at-least-once).
На партицию фиксируется наибольший обработанный offset. Запись в Redis:
ключ fs:student:<student_id>, инкрементальные счётчики n_grade_events,
n_debt_open_events, n_debt_closed_events, n_payment_events и последние
значения last_event_at, last_event_kind. Дедупликация по event_id в
множестве fs:kafka:seen (защита от перечитывания после сбоя между записью
и фиксацией; в производственном контуре вместо неё идемпотентная запись
или транзакции exactly-once). События студентов вне текущей материализации
учитываются счётчиком events_outside_snapshot: частичные ключи не
создаются, студенты войдут в обработку после следующей материализации.
Метаданные: ключ fs:kafka:meta (потреблено, записано, дубликаты, вне
материализации, offset каждой партиции).

Опции: --max-events N (обработать N событий и выйти, для проверок),
--topic, --group. Без ограничений потребитель работает до Ctrl+C.

## Проверенный сценарий

1. producer --count 300 --out events.json: опубликовано 300
   (grade 100, debt_open 57, debt_closed 43, payment 100).
2. consumer --max-events 300: потреблено 300, записано 212, дубликатов 0,
   вне материализации 88 (срез 55 студентов, события остальных учтутся
   после следующей материализации); смещение зафиксировано после записи.
3. Повторная публикация того же потока (producer --in events.json) и
   повторное потребление: потреблено 300, записано 0, дубликатов 300;
   счётчики студентов не изменились, last_event_at остался прежним.

## Запуск

```
docker compose -f deployment/docker-compose.yml --env-file .env up -d kafka
.venv/Scripts/python -I streaming/producer.py --count 300 --out events.json
.venv/Scripts/python -I streaming/consumer.py --max-events 300
.venv/Scripts/python -I streaming/consumer.py        # регламентный режим до Ctrl+C
```

Переменные: KAFKA_BOOTSTRAP_SERVERS (localhost:9092), KAFKA_TOPIC
(ar1c.events) в .env. Прочитать метаданные потребления:
fs.redis.hgetall("fs:kafka:meta") через FeatureStore.
