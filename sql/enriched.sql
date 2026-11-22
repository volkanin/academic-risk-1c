-- Слой enriched (блок 4.1): внешние данные, дополняющие признаки ML.
-- Наполнение:
-- 1. enriched.programs_info и enriched.finance_info собирает скрейпинг
--    открытых сведений вуза: раздел «Сведения об образовательной
--    организации» официального сайта (обязательный, размечен микроразметкой
--    schema.org). Исполнитель: src/enriched/scrape_programs.py.
-- 2. enriched.activity_weekly и enriched.activity_agg наполняет ETL Moodle
--    по стандартным веб-службам (src/enriched/moodle_etl.py, FR-13).
-- Связь с витриной 1С: код направления разбирается из заголовка программы.
-- Персональных данных слой не содержит (NFR-2, п. 9 ТЗ).

-- ---------- Направления подготовки из открытых сведений вуза ----------
-- Одна строка на образовательную программу (направление + профиль).
CREATE TABLE IF NOT EXISTS enriched.programs_info (
    source_url  text,
    scraped_at  timestamptz,
    edu_code    text,
    edu_name    text,
    edu_profile text,
    edu_level   text,
    edu_forms   text,
    edu_terms   text
);
CREATE INDEX IF NOT EXISTS idx_programs_info_code ON enriched.programs_info (edu_code);

-- ---------- Финансовое обеспечение вуза (открытые сведения) ----------
CREATE TABLE IF NOT EXISTS enriched.finance_info (
    source_url      text,
    scraped_at      timestamptz,
    fin_year        smallint,
    budget_federal  numeric,
    budget_regional numeric,
    budget_municipal numeric,
    paid_contracts  numeric
);

-- ---------- Контекст направления подготовки ----------
-- Производный признак: сколько программ вуза ведётся по коду направления,
-- какие формы и уровни, относится ли код к творческому профилю
-- (архитектура и искусство).
CREATE OR REPLACE VIEW enriched.v_program_context AS
SELECT edu_code,
       count(*) AS n_programs,
       string_agg(DISTINCT edu_level, '; ') AS levels,
       bool_or(edu_forms LIKE '%Очная%') AS has_fulltime,
       (edu_code LIKE '07.%' OR edu_code LIKE '54.%') AS is_creative
FROM enriched.programs_info
GROUP BY edu_code;

-- ---------- Активность Moodle: соответствие и события ----------
-- Соответствие 1С-студент и пользователя Moodle: логин равен номеру
-- зачётной книжки, в слое только псевдонимы: хеш логина с солью (FR-13).
-- moodle_user_id служебный ключ выгрузки, имена и почты не хранятся.
CREATE TABLE IF NOT EXISTS enriched.student_map (
    student_id     text PRIMARY KEY,
    username_hash  text NOT NULL,
    moodle_user_id integer,
    linked_at      timestamptz
);

-- Недельная динамика активности: неделя семестра по учебному календарю,
-- 0 это межсессионный период.
CREATE TABLE IF NOT EXISTS enriched.activity_weekly (
    student_id   text NOT NULL,
    semester_no  smallint NOT NULL,
    week_number  smallint NOT NULL,
    events       integer NOT NULL,
    active_days  integer NOT NULL,
    courses      integer NOT NULL,
    PRIMARY KEY (student_id, semester_no, week_number)
);

-- Агрегаты активности за семестр: события, дни, курсы, оценки,
-- сданные работы, попытки тестов.
CREATE TABLE IF NOT EXISTS enriched.activity_agg (
    student_id     text NOT NULL,
    semester_no    smallint NOT NULL,
    events_total   integer NOT NULL,
    active_days    integer NOT NULL,
    first_week     smallint,
    last_week      smallint,
    courses        integer NOT NULL,
    grades_count   integer NOT NULL,
    submissions    integer NOT NULL,
    quiz_attempts  integer NOT NULL,
    PRIMARY KEY (student_id, semester_no)
);

-- ---------- Контекст направления для студентов ----------
-- Направления, которых нет в открытых сведениях вуза (синтетика включает
-- инженерные коды для разнообразия), остаются с пустым контекстом.
CREATE OR REPLACE VIEW enriched.v_student_program_context AS
SELECT st.student_id,
       pr.program_id,
       substring(pr.title from '^[0-9]{2}[.][0-9]{2}[.][0-9]{2}') AS edu_code,
       ctx.n_programs,
       ctx.levels,
       ctx.has_fulltime,
       ctx.is_creative
FROM cleaned.vs_students st
JOIN cleaned.vs_programs pr ON pr.program_id = st.program_id
LEFT JOIN enriched.v_program_context ctx
    ON ctx.edu_code = substring(pr.title from '^[0-9]{2}[.][0-9]{2}[.][0-9]{2}');
