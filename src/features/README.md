# Feature Store (блок 4.1)

Единый API чтения признаков для обучения и скоринга: offline-хранилище в
PostgreSQL (SQL-функции исторического заполнения со срезом на дату) и
online-хранилище в Redis с материализацией. Требование FR-6 в
docs/requirements.md.

## Состав

| Файл | Назначение |
|---|---|
| sql/feature_store.sql | таблицы features.fs_students и features.fs_snapshots, функции semester_dates, build_snapshot, backfill, latest_snapshot, представление fs_latest |
| store.py | класс FeatureStore: построение срезов, чтение offline и online, материализация |
| run_store.py | команды init, build, backfill, materialize, show, meta |
| verify_offline.py | проверка согласованности SQL-функций с витриной features.vs_student_semester |

## Отличие от витрины признаков

Витрина features.vs_student_semester (блок 2.2, src/data/features.py)
фиксирована на датах начал семестров. SQL-функции Feature Store строят срез
на произвольную дату: дата после конца семестра считается послесессионной,
студент переходит в следующий семестр, внесённая сессия входит в
академическую историю. После конца последнего семестра программы точек нет.
Правила среза те же: события с датой события и entered_at не позже даты
среза, статус из истории приказов, таргет по окну до конца следующего
семестра (docs/experiment_plan.md, раздел 1).

Поле target для даты из будущего считается по ещё не случившимся событиям,
поэтому для скоринга текущего дня не используется; исторические срезы
нужны для обучения.

## Согласованность с витриной

verify_offline.py строит срезы на все 18 дат витрины и сравнивает все
колонки: 24343 точки совпадают один к одному, расхождений нет. Дробные
колонки допускают разницу последнего знака округления (построитель витрины
считает в float, SQL-функции в numeric), на текущих данных расхождений не
возникло.

## Online-хранилище

Материализация переносит последний offline-срез целиком: ключ
fs:student:<student_id> хеш признаков, fs:meta содержит дату среза, время,
число студентов и версию состава признаков. Значения хранятся строками,
пустая строка это NULL; приведение типов при чтении по явным спискам
колонок, идентификаторы групп не превращаются в числа.

## Запуск

```
docker compose -f deployment/docker-compose.yml --env-file .env up -d
.venv/Scripts/python -I src/features/run_store.py init
.venv/Scripts/python -I src/features/run_store.py backfill --dates 2023-09-01,2024-02-05
.venv/Scripts/python -I src/features/run_store.py build --as-of 2026-11-21
.venv/Scripts/python -I src/features/run_store.py materialize --as-of 2026-11-21
.venv/Scripts/python -I src/features/run_store.py show --student <student_id>
.venv/Scripts/python -I src/features/verify_offline.py
```
