# Сервис раннего предупреждения об академическом риске студентов

Выпускная работа по программе профессиональной переподготовки «Инженер-архитектор в сфере искусственного интеллекта» (Университет Иннополис).

Автор: Леонид Волканин.

## О проекте

Сервис на основе данных информационной системы вуза 1С:Университет оценивает вероятность отчисления студента в предстоящем семестре, объясняет, какие обстоятельства привели к оценке, и возвращает результат в информационную систему для сотрудников деканата и кураторов. Сервис не принимает решений за сотрудника, а формирует обоснованный список студентов для внимания.

Модель обучается на открытых наборах данных учебной аналитики (UCI Predict Students' Dropout and Academic Success, OULAD). Витрины 1С:Университет демонстрируются синтетическим контингентом, сгенерированным по структуре реальных учебных планов вуза; реальные персональные данные студентов не используются. Активность в электронной среде обучения берётся из действующей СЭО Moodle вуза через стандартные веб-службы (REST, токен службы чтения): полный обход СЭО и замер покрытия выполнены, витрины активности наполняются по мере подключения соответствия студентов из расширения 1С (см. docs/moodle_service_setup.md). Открытые сведения о направлениях подготовки собираются веб-скрейпингом с сайта вуза и образуют контекст направления (слой enriched).

Признаки доступны через Feature Store: offline-хранилище в PostgreSQL (SQL-функции исторического заполнения со срезом на дату) и online-хранилище в Redis с материализацией, единый API чтения (src/features/). Ключевое методологическое требование: признаки формируются строго по сведениям, известным на дату наблюдения (срез на дату), что исключает утечку будущих данных в обучение и оценку.

## Структура репозитория

```
academic-risk-1c/
├── README.md
├── requirements.txt
├── .gitignore
├── .env.example
├── data/
│   ├── raw/              # исходные файлы датасетов
│   ├── processed/        # промежуточные выгрузки
│   ├── external/         # сырые страницы веб-скрейпинга (в репозиторий не входят)
│   └── samples/          # небольшие примеры для проверки
├── sql/
│   ├── schema.sql            # схемы и таблицы
│   ├── layers.sql            # слои raw и cleaned
│   ├── features.sql          # витрина признаков и признаки UCI, OULAD
│   ├── feature_store.sql     # SQL-функции Feature Store со срезом на дату
│   ├── enriched.sql          # таблицы enriched: направления, Moodle, представления
│   └── queries.sql           # аналитические запросы
├── notebooks/
│   ├── 01_data_preparation.ipynb
│   ├── 02_baseline_model.ipynb
│   ├── 03_model_experiments.ipynb
│   └── 04_final_model.ipynb
├── src/
│   ├── data/             # генератор витрин, загрузка raw, очистка, признаки
│   ├── features/         # Feature Store: SQL-функции, API, материализация, проверка
│   ├── enriched/         # веб-скрейпинг направлений, Moodle ETL, эмулятор Moodle
│   ├── models/           # загрузчик модели с проверкой окружения
│   ├── utils/            # подключение к БД, учебный календарь
│   └── etl_pipeline.py   # загрузка всех источников в raw
├── api/                  # REST API на FastAPI
├── streaming/            # Kafka: producer и consumer
├── tests/                # unit и интеграционные тесты
├── deployment/           # docker-compose.yml (PostgreSQL, Redis)
└── docs/
    ├── architecture.png      # диаграмма архитектуры
    ├── moodle_service_setup.md  # справка по настройке Moodle для выгрузки
    ├── requirements.md       # требования к сервису (FR, NFR)
    ├── experiment_plan.md    # план экспериментов, гипотезы, журнал отклонений
    ├── data_generation.md    # обоснование параметров генератора
    ├── report.pdf
    └── presentation.pdf
```

Каждый модуль сопровождается README рядом с кодом: src/data/README.md (генератор), src/features/README.md (Feature Store), src/enriched/README.md (enriched слой).

## Быстрый старт

```bash
git clone https://github.com/volkanin/academic-risk-1c.git
cd academic-risk-1c
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

# инфраструктура: PostgreSQL и Redis (Docker Desktop должен быть запущен)
docker compose -f deployment/docker-compose.yml --env-file .env up -d

# загрузка данных и слоёв
.venv/Scripts/python src/etl_pipeline.py       # raw
.venv/Scripts/python src/data/clean.py         # cleaned
.venv/Scripts/python src/data/features.py      # features (витрина признаков)

# Feature Store
.venv/Scripts/python -I src/features/run_store.py init
.venv/Scripts/python -I src/features/run_store.py backfill --dates 2023-09-01,2024-02-05
.venv/Scripts/python -I src/features/run_store.py materialize
.venv/Scripts/python -I src/features/verify_offline.py   # согласованность с витриной

# enriched: открытые сведения о направлениях подготовки
.venv/Scripts/python -I src/enriched/scrape_programs.py

# Moodle: демонстрационный контур и реальная выгрузка
.venv/Scripts/python -I src/enriched/moodle_mock.py
.venv/Scripts/python -I src/enriched/moodle_etl.py --mock
.venv/Scripts/python -I src/enriched/moodle_etl.py --verbose
```

Адрес и токен Moodle задаются в локальном .env (MOODLE_URL, MOODLE_TOKEN,
MOODLE_SALT), настройки службы описаны в docs/moodle_service_setup.md.

## Статус работ

| Этап | Состояние |
|---|---|
| Фаза 0. Требования, план экспериментов, архитектура | завершена |
| Фаза 1. Данные, EDA, слои PostgreSQL (raw, cleaned, features) | завершена |
| Фаза 2. Базовая модель и эксперименты с алгоритмами | завершена |
| Фаза 3. Финальная модель: глубокая оценка, SHAP, справедливость, артефакты | завершена |
| Фаза 4. Feature Store (offline PostgreSQL + online Redis), Moodle-ETL, веб-скрейпинг направлений | завершена |
| Фаза 5. Kafka: поток событий признаков | в работе |
| Фаза 6. REST API (FastAPI), Docker Compose, интерфейс | не начата |
| Фаза 7. Тесты, документация | не начата |
| Фаза 8. Отчёт, презентация, защита | не начата |
