-- Логические слои данных (блок 2.2 требований): cleaned и features.
-- Файл идемпотентен: CREATE IF NOT EXISTS, TRUNCATE + INSERT при наполнении.
-- Исполнитель: src/data/clean.py. Слой features наполняется признаками
-- со срезом на дату наблюдения (следующий шаг, src/data/features.py).

-- =====================================================================
-- Слой cleaned: приведение типов и нормализация значений.
-- Правила перехода raw -> cleaned:
-- 1. Пропуски: пустая строка и заглушка '?' становятся NULL.
-- 2. Коды категорий и идентификаторы приводятся к целым типам.
-- 3. Целевая переменная UCI: target_dropout = 1 для Dropout, иначе 0;
--    исходный текст сохраняется в target_text.
-- 4. Единая числовая шкала оценок синтетики: числовые виды контроля 2..5,
--    зачёт эквивалентен 4, незачёт эквивалентен 2, неявка даёт NULL
--    (попытка не состоялась, в средний балл не включается).
-- 5. Производные флаги: is_pass, is_closed, days_open, is_paid.
-- =====================================================================

-- ---------- UCI dropout ----------
CREATE TABLE IF NOT EXISTS cleaned.uci_dropout (
    marital_status                              smallint,
    application_mode                            smallint,
    application_order                           smallint,
    course                                      smallint,
    daytime_evening_attendance                  smallint,
    previous_qualification                      smallint,
    previous_qualification_grade                numeric(6,2),
    nationality                                 smallint,
    mother_s_qualification                      smallint,
    father_s_qualification                      smallint,
    mother_s_occupation                         smallint,
    father_s_occupation                         smallint,
    admission_grade                             numeric(6,2),
    displaced                                   smallint,
    educational_special_needs                   smallint,
    debtor                                      smallint,
    tuition_fees_up_to_date                     smallint,
    gender                                      smallint,
    scholarship_holder                          smallint,
    age_at_enrollment                           smallint,
    international                               smallint,
    curricular_units_1st_sem_credited           smallint,
    curricular_units_1st_sem_enrolled           smallint,
    curricular_units_1st_sem_evaluations        smallint,
    curricular_units_1st_sem_approved           smallint,
    curricular_units_1st_sem_grade              numeric(6,2),
    curricular_units_1st_sem_without_evaluations smallint,
    curricular_units_2nd_sem_credited           smallint,
    curricular_units_2nd_sem_enrolled           smallint,
    curricular_units_2nd_sem_evaluations        smallint,
    curricular_units_2nd_sem_approved           smallint,
    curricular_units_2nd_sem_grade              numeric(6,2),
    curricular_units_2nd_sem_without_evaluations smallint,
    unemployment_rate                           numeric(5,2),
    inflation_rate                              numeric(5,2),
    gdp                                         numeric(5,2),
    target_text                                 text,
    target_dropout                              smallint
);
TRUNCATE TABLE cleaned.uci_dropout;
-- Опечатка Nacionality из исходника исправляется в cleaned (nationality).
INSERT INTO cleaned.uci_dropout
SELECT
    marital_status::smallint,
    application_mode::smallint,
    application_order::smallint,
    course::smallint,
    daytime_evening_attendance::smallint,
    previous_qualification::smallint,
    previous_qualification_grade::numeric,
    nacionality::smallint,
    mother_s_qualification::smallint,
    father_s_qualification::smallint,
    mother_s_occupation::smallint,
    father_s_occupation::smallint,
    admission_grade::numeric,
    displaced::smallint,
    educational_special_needs::smallint,
    debtor::smallint,
    tuition_fees_up_to_date::smallint,
    gender::smallint,
    scholarship_holder::smallint,
    age_at_enrollment::smallint,
    international::smallint,
    curricular_units_1st_sem_credited::smallint,
    curricular_units_1st_sem_enrolled::smallint,
    curricular_units_1st_sem_evaluations::smallint,
    curricular_units_1st_sem_approved::smallint,
    curricular_units_1st_sem_grade::numeric,
    curricular_units_1st_sem_without_evaluations::smallint,
    curricular_units_2nd_sem_credited::smallint,
    curricular_units_2nd_sem_enrolled::smallint,
    curricular_units_2nd_sem_evaluations::smallint,
    curricular_units_2nd_sem_approved::smallint,
    curricular_units_2nd_sem_grade::numeric,
    curricular_units_2nd_sem_without_evaluations::smallint,
    unemployment_rate::numeric,
    inflation_rate::numeric,
    gdp::numeric,
    target,
    CASE WHEN target = 'Dropout' THEN 1 ELSE 0 END
FROM raw.uci_dropout;

-- ---------- OULAD ----------
CREATE TABLE IF NOT EXISTS cleaned.oulad_courses (
    code_module                text,
    code_presentation          text,
    module_presentation_length smallint
);
TRUNCATE TABLE cleaned.oulad_courses;
INSERT INTO cleaned.oulad_courses
SELECT code_module, code_presentation, module_presentation_length::smallint
FROM raw.oulad_courses;

CREATE TABLE IF NOT EXISTS cleaned.oulad_assessments (
    code_module       text,
    code_presentation text,
    id_assessment     integer,
    assessment_type   text,
    assessment_date   smallint,
    weight            numeric(5,2)
);
TRUNCATE TABLE cleaned.oulad_assessments;
INSERT INTO cleaned.oulad_assessments
SELECT code_module, code_presentation, id_assessment::integer, assessment_type,
       NULLIF(NULLIF("date", '?'), '')::smallint, NULLIF(NULLIF(weight, '?'), '')::numeric
FROM raw.oulad_assessments;

CREATE TABLE IF NOT EXISTS cleaned.oulad_vle (
    id_site           integer,
    code_module       text,
    code_presentation text,
    activity_type     text,
    week_from         smallint,
    week_to           smallint
);
TRUNCATE TABLE cleaned.oulad_vle;
INSERT INTO cleaned.oulad_vle
SELECT id_site::integer, code_module, code_presentation, activity_type,
       NULLIF(NULLIF(week_from, '?'), '')::smallint, NULLIF(NULLIF(week_to, '?'), '')::smallint
FROM raw.oulad_vle;

CREATE TABLE IF NOT EXISTS cleaned.oulad_student_info (
    code_module          text,
    code_presentation    text,
    id_student           integer,
    gender               text,
    region               text,
    highest_education    text,
    imd_band             text,
    age_band             text,
    num_of_prev_attempts smallint,
    studied_credits      smallint,
    disability           text,
    final_result         text
);
TRUNCATE TABLE cleaned.oulad_student_info;
INSERT INTO cleaned.oulad_student_info
SELECT code_module, code_presentation, id_student::integer, gender, region,
       highest_education, NULLIF(imd_band, '') AS imd_band, age_band,
       num_of_prev_attempts::smallint, studied_credits::smallint, disability, final_result
FROM raw.oulad_student_info;

CREATE TABLE IF NOT EXISTS cleaned.oulad_student_registration (
    code_module         text,
    code_presentation   text,
    id_student          integer,
    date_registration   smallint,
    date_unregistration smallint
);
TRUNCATE TABLE cleaned.oulad_student_registration;
INSERT INTO cleaned.oulad_student_registration
SELECT code_module, code_presentation, id_student::integer,
       NULLIF(NULLIF(date_registration, '?'), '')::smallint,
       NULLIF(NULLIF(date_unregistration, '?'), '')::smallint
FROM raw.oulad_student_registration;

CREATE TABLE IF NOT EXISTS cleaned.oulad_student_assessment (
    id_assessment  integer,
    id_student     integer,
    date_submitted smallint,
    is_banked      smallint,
    score          numeric(5,2)
);
TRUNCATE TABLE cleaned.oulad_student_assessment;
INSERT INTO cleaned.oulad_student_assessment
SELECT id_assessment::integer, id_student::integer, date_submitted::smallint,
       is_banked::smallint, NULLIF(NULLIF(score, '?'), '')::numeric
FROM raw.oulad_student_assessment;

CREATE TABLE IF NOT EXISTS cleaned.oulad_student_vle (
    code_module       text,
    code_presentation text,
    id_student        integer,
    id_site           integer,
    activity_day      smallint,
    sum_click         integer
);
TRUNCATE TABLE cleaned.oulad_student_vle;
INSERT INTO cleaned.oulad_student_vle
SELECT code_module, code_presentation, id_student::integer, id_site::integer,
       "date"::smallint, sum_click::integer
FROM raw.oulad_student_vle;

-- ---------- Синтетические витрины ----------
CREATE TABLE IF NOT EXISTS cleaned.vs_programs (
    program_id  text NOT NULL,
    title       text,
    faculty     text,
    study_form  text,
    basis       text
);
TRUNCATE TABLE cleaned.vs_programs;
INSERT INTO cleaned.vs_programs SELECT program_id, title, faculty, study_form, basis FROM raw.vs_programs;

CREATE TABLE IF NOT EXISTS cleaned.vs_students (
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
TRUNCATE TABLE cleaned.vs_students;
INSERT INTO cleaned.vs_students SELECT student_id, program_id, group_code, gender, birth_date, region, hostel, privilege, admission_year, admission_date FROM raw.vs_students;

CREATE TABLE IF NOT EXISTS cleaned.vs_disciplines (
    discipline_id text NOT NULL,
    title         text,
    program_id    text,
    semester      integer,
    control_type  text,
    credits       numeric
);
TRUNCATE TABLE cleaned.vs_disciplines;
INSERT INTO cleaned.vs_disciplines SELECT discipline_id, title, program_id, semester, control_type, credits FROM raw.vs_disciplines;

CREATE TABLE IF NOT EXISTS cleaned.vs_admission (
    student_id         text NOT NULL,
    exam_score         numeric,
    previous_education text,
    priority           integer,
    medal              boolean
);
TRUNCATE TABLE cleaned.vs_admission;
INSERT INTO cleaned.vs_admission SELECT student_id, exam_score, previous_education, priority, medal FROM raw.vs_admission;

CREATE TABLE IF NOT EXISTS cleaned.vs_movements (
    student_id     text NOT NULL,
    order_type     text,
    reason         text,
    order_date     date,
    effective_date date,
    entered_at     date
);
TRUNCATE TABLE cleaned.vs_movements;
INSERT INTO cleaned.vs_movements SELECT student_id, order_type, reason, order_date, effective_date, entered_at FROM raw.vs_movements;

CREATE TABLE IF NOT EXISTS cleaned.vs_grades (
    student_id    text NOT NULL,
    discipline_id text,
    semester      integer,
    attempt_no    smallint,
    grade         text,
    exam_date     date,
    absent        boolean,
    entered_at    date,
    grade_numeric smallint,
    is_pass       boolean
);
TRUNCATE TABLE cleaned.vs_grades;
INSERT INTO cleaned.vs_grades
SELECT
    student_id, discipline_id, semester, attempt_no::smallint, grade, exam_date, absent, entered_at,
    CASE
        WHEN grade IN ('2', 'незачёт') THEN 2
        WHEN grade = '3' THEN 3
        WHEN grade = '4' OR grade = 'зачёт' THEN 4
        WHEN grade = '5' THEN 5
        ELSE NULL  -- неявка: попытка не состоялась
    END AS grade_numeric,
    CASE WHEN grade IN ('3', '4', '5', 'зачёт') THEN true ELSE false END AS is_pass
FROM raw.vs_grades;

CREATE TABLE IF NOT EXISTS cleaned.vs_debts (
    student_id    text NOT NULL,
    discipline_id text,
    semester      integer,
    arose_date    date,
    closed_date   date,
    entered_at    date,
    is_closed     boolean,
    days_open     integer
);
TRUNCATE TABLE cleaned.vs_debts;
INSERT INTO cleaned.vs_debts
SELECT student_id, discipline_id, semester, arose_date, closed_date, entered_at,
       closed_date IS NOT NULL AS is_closed,
       CASE WHEN closed_date IS NOT NULL THEN (closed_date - arose_date) END AS days_open
FROM raw.vs_debts;

CREATE TABLE IF NOT EXISTS cleaned.vs_finance (
    student_id   text NOT NULL,
    accrual_date date,
    amount       numeric,
    paid_date    date,
    overdue_days integer,
    entered_at   date,
    is_paid      boolean
);
TRUNCATE TABLE cleaned.vs_finance;
INSERT INTO cleaned.vs_finance
SELECT student_id, accrual_date, amount, paid_date, overdue_days::integer, entered_at,
       paid_date IS NOT NULL AS is_paid
FROM raw.vs_finance;

CREATE TABLE IF NOT EXISTS cleaned.vs_load (
    program_id        text NOT NULL,
    semester          integer,
    n_exams           integer,
    n_tests           integer,
    n_course_projects integer,
    credits           numeric
);
TRUNCATE TABLE cleaned.vs_load;
INSERT INTO cleaned.vs_load SELECT program_id, semester, n_exams, n_tests, n_course_projects, credits FROM raw.vs_load;

-- =====================================================================
-- Слой features: признаки для ML со срезом на дату наблюдения.
-- Наполняется src/data/features.py (следующий шаг): таблицы признаков
-- «студент, семестр» для синтетики и агрегаты для открытых датасетов.
-- =====================================================================
