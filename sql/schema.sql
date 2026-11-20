-- Схема базы данных проекта (блок 2 требований).
-- Идемпотентна: повторный запуск не пересоздаёт таблицы, наполнение делает ETL.

CREATE SCHEMA IF NOT EXISTS raw;
CREATE SCHEMA IF NOT EXISTS cleaned;
CREATE SCHEMA IF NOT EXISTS features;
CREATE SCHEMA IF NOT EXISTS enriched;

-- =====================================================================
-- Слой raw: данные «как в файлах», типы не нормализованы.
-- Внешние датасеты загружаются текстовыми колонками, приведение типов
-- выполняется при переходе в cleaned. Имена колонок приведены к
-- snake_case только для удобства, соответствие исходным заголовкам
-- зафиксировано в комментарии к таблице.
-- =====================================================================

-- UCI «Predict Students' Dropout and Academic Success» (4424 строки, 37 колонок,
-- разделитель «;», кодировка Latin-1). Исходные заголовки сохранены в комментарии.
-- Marital status; Application mode; Application order; Course; Daytime/evening attendance;
-- Previous qualification; Previous qualification (grade); Nacionality; Mother's qualification;
-- Father's qualification; Mother's occupation; Father's occupation; Admission grade; Displaced;
-- Educational special needs; Debtor; Tuition fees up to date; Gender; Scholarship holder;
-- Age at enrollment; International; Curricular units 1st sem (credited/enrolled/evaluations/
-- approved/grade/without evaluations); Curricular units 2nd sem (...); Unemployment rate;
-- Inflation rate; GDP; Target.
CREATE TABLE IF NOT EXISTS raw.uci_dropout (
    marital_status                          text,
    application_mode                        text,
    application_order                       text,
    course                                  text,
    daytime_evening_attendance              text,
    previous_qualification                  text,
    previous_qualification_grade            text,
    nacionality                             text,
    mother_s_qualification                  text,
    father_s_qualification                  text,
    mother_s_occupation                     text,
    father_s_occupation                     text,
    admission_grade                         text,
    displaced                               text,
    educational_special_needs               text,
    debtor                                  text,
    tuition_fees_up_to_date                 text,
    gender                                  text,
    scholarship_holder                      text,
    age_at_enrollment                       text,
    international                           text,
    curricular_units_1st_sem_credited           text,
    curricular_units_1st_sem_enrolled           text,
    curricular_units_1st_sem_evaluations        text,
    curricular_units_1st_sem_approved           text,
    curricular_units_1st_sem_grade              text,
    curricular_units_1st_sem_without_evaluations text,
    curricular_units_2nd_sem_credited           text,
    curricular_units_2nd_sem_enrolled           text,
    curricular_units_2nd_sem_evaluations        text,
    curricular_units_2nd_sem_approved           text,
    curricular_units_2nd_sem_grade              text,
    curricular_units_2nd_sem_without_evaluations text,
    unemployment_rate                       text,
    inflation_rate                          text,
    gdp                                     text,
    target                                  text
);

-- =====================================================================
-- Слой raw: синтетические витрины в терминах 1С:Университет.
-- Генератор: src/data/generator.py, выгрузка: data/raw/synthetic/*.csv.
-- Структура повторяет предметные витрины из ТЗ интеграционного слоя,
-- даты в ISO 8601, идентификаторы UUID, entered_at это дата внесения
-- записи в систему (для проверки среза на дату наблюдения).
-- =====================================================================

CREATE TABLE IF NOT EXISTS raw.vs_programs (
    program_id   text NOT NULL,
    title        text,
    faculty      text,
    study_form   text,
    basis        text
);

CREATE TABLE IF NOT EXISTS raw.vs_students (
    student_id     text NOT NULL,
    program_id     text,
    group_code     text,
    gender         text,
    birth_date     date,
    region         text,
    hostel         boolean,
    privilege      boolean,
    admission_year integer,
    admission_date date
);

CREATE TABLE IF NOT EXISTS raw.vs_disciplines (
    discipline_id text NOT NULL,
    title         text,
    program_id    text,
    semester      integer,
    control_type  text,
    credits       numeric
);

CREATE TABLE IF NOT EXISTS raw.vs_admission (
    student_id        text NOT NULL,
    exam_score        numeric,
    previous_education text,
    priority          integer,
    medal             boolean
);

CREATE TABLE IF NOT EXISTS raw.vs_movements (
    student_id     text NOT NULL,
    order_type     text,
    reason         text,
    order_date     date,
    effective_date date,
    entered_at     date
);

CREATE TABLE IF NOT EXISTS raw.vs_grades (
    student_id    text NOT NULL,
    discipline_id text,
    semester      integer,
    attempt_no    integer,
    grade         text,
    exam_date     date,
    absent        boolean,
    entered_at    date
);

CREATE TABLE IF NOT EXISTS raw.vs_debts (
    student_id    text NOT NULL,
    discipline_id text,
    semester      integer,
    arose_date    date,
    closed_date   date,
    entered_at    date
);

CREATE TABLE IF NOT EXISTS raw.vs_finance (
    student_id   text NOT NULL,
    accrual_date date,
    amount       numeric,
    paid_date    date,
    overdue_days integer,
    entered_at   date
);

CREATE TABLE IF NOT EXISTS raw.vs_load (
    program_id        text NOT NULL,
    semester          integer,
    n_exams           integer,
    n_tests           integer,
    n_course_projects integer,
    credits           numeric
);

-- Эволюция схемы: идемпотентное добавление колонок при изменении структуры витрин.
ALTER TABLE raw.vs_load ADD COLUMN IF NOT EXISTS n_course_projects integer;

-- =====================================================================
-- Слой raw: OULAD (Open University Learning Analytics Dataset).
-- Зеркало: kaggle.com/datasets/anlgrbz/student-demographics-online-education-dataoulad.
-- Все колонки текстовые, приведение типов выполняется в cleaned.
-- =====================================================================

CREATE TABLE IF NOT EXISTS raw.oulad_courses (
    code_module               text,
    code_presentation         text,
    module_presentation_length text
);

CREATE TABLE IF NOT EXISTS raw.oulad_assessments (
    code_module       text,
    code_presentation text,
    id_assessment     text,
    assessment_type   text,
    "date"            text,
    weight            text
);

CREATE TABLE IF NOT EXISTS raw.oulad_vle (
    id_site           text,
    code_module       text,
    code_presentation text,
    activity_type     text,
    week_from         text,
    week_to           text
);

CREATE TABLE IF NOT EXISTS raw.oulad_student_info (
    code_module          text,
    code_presentation    text,
    id_student           text,
    gender               text,
    region               text,
    highest_education    text,
    imd_band             text,
    age_band             text,
    num_of_prev_attempts text,
    studied_credits      text,
    disability           text,
    final_result         text
);

CREATE TABLE IF NOT EXISTS raw.oulad_student_registration (
    code_module         text,
    code_presentation   text,
    id_student          text,
    date_registration   text,
    date_unregistration text
);

CREATE TABLE IF NOT EXISTS raw.oulad_student_assessment (
    id_assessment  text,
    id_student     text,
    date_submitted text,
    is_banked      text,
    score          text
);

CREATE TABLE IF NOT EXISTS raw.oulad_student_vle (
    code_module       text,
    code_presentation text,
    id_student        text,
    id_site           text,
    "date"            text,
    sum_click         text
);
