-- Слой raw: реальные витрины HTTP-сервиса расширения 1С АР_АналитикаРиска.
-- Приемник: src/onec_client/load.py (запросы с курсорной пагинацией, COPY).
--
-- Правила ПДн при приеме (NFR-2, FR-13): номер зачетной книжки хранится
-- только псевдонимом book_hash (sha256 от соли MOODLE_SALT и значения без
-- учета регистра, первые 32 символа; тот же алгоритм, что и для логина
-- Moodle в src/enriched/moodle_etl.py), дата рождения возрастом age на
-- дату среза. ФИО, паспортов и контактов в витринах нет, связь по UUID.
-- Даты внесения записей в источниках отсутствуют, колонка entered_at
-- не загружается.
--
-- semester_num это номер семестра, разобранный приемником из наименования
-- периода контроля («Первый семестр» и далее); order_class это класс
-- приказа по утвержденному делению аналитики (metadata-mapping.md, п. 4).

CREATE TABLE IF NOT EXISTS raw.onec_students (
    student_id  text NOT NULL,
    book_hash   text,
    program_id  text,
    group_code  text,
    faculty     text,
    study_form  text,
    basis       text,
    gender      text,
    age         integer,
    region      text,
    hostel      boolean,
    privilege   boolean,
    state       text
);

CREATE TABLE IF NOT EXISTS raw.onec_programs (
    program_id       text NOT NULL,
    title            text,
    faculty          text,
    study_form       text,
    year             text,
    hours_per_credit integer
);

CREATE TABLE IF NOT EXISTS raw.onec_disciplines (
    program_id       text,
    discipline_id    text NOT NULL,
    discipline_title text,
    row_id           text,
    semester         text,
    semester_num     integer,
    load_type        text,
    unit             text,
    amount           numeric,
    credits          numeric
);

CREATE TABLE IF NOT EXISTS raw.onec_movements (
    student_id     text NOT NULL,
    book_hash      text,
    order_type     text,
    order_kind     text,
    analytics      text,
    reason         text,
    order_date     date,
    effective_date date,
    program_id     text,
    group_code     text,
    order_class    text
);

CREATE TABLE IF NOT EXISTS raw.onec_grades (
    student_id       text NOT NULL,
    book_hash        text,
    program_id       text,
    discipline_id    text,
    discipline_title text,
    semester         text,
    semester_num     integer,
    control_type     text,
    attempt_no       integer,
    grade            text,
    absent           boolean,
    exam_date        date,
    event_id         text
);

CREATE TABLE IF NOT EXISTS raw.onec_debts (
    student_id       text NOT NULL,
    book_hash        text,
    program_id       text,
    discipline_id    text,
    discipline_title text,
    semester         text,
    semester_num     integer,
    arose_date       date,
    closed_date      date,
    event_id_open    text
);

CREATE TABLE IF NOT EXISTS raw.onec_load (
    program_id       text NOT NULL,
    semester         text,
    semester_num     integer,
    n_disciplines    integer,
    hours            integer,
    n_exams          integer,
    n_tests          integer,
    n_course_works   integer,
    hours_per_credit integer,
    credits          numeric,
    credits_source   text
);
