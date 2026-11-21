-- Слой features: подготовленные признаки для ML (блок 2.2, Features Layer,
-- 5 баллов). Наполнение:
-- 1. features.vs_student_semester строит src/data/features.py: таблица
--    «студент, дата наблюдения» с признаками строго по сведениям, известным
--    на дату наблюдения (фильтры по датам событий и entered_at), таргет по
--    правилам docs/experiment_plan.md, раздел 1.
-- 2. features.uci_dropout и features.oulad_module наполняются здесь же:
--    для статического UCI это приведённая копия cleaned, для OULAD агрегаты
--    активности за весь период просмотра LMS.

-- ---------- Признаки синтетического контингента (студент, дата наблюдения) ----------
CREATE TABLE IF NOT EXISTS features.vs_student_semester (
    student_id          text NOT NULL,
    program_id          text,
    faculty             text,
    group_code          text,
    observation_date    date,
    semester_no         smallint,
    age                 smallint,
    gender              text,
    region              text,
    is_non_local        boolean,
    hostel              boolean,
    privilege           boolean,
    study_form          text,
    basis               text,
    medal               boolean,
    previous_education  text,
    exam_score          numeric,
    semesters_done      smallint,
    gpa_mean            numeric(5,2),
    gpa_last            numeric(5,2),
    gpa_prev            numeric(5,2),
    gpa_delta           numeric(5,2),
    gpa_slope           numeric(6,3),
    gpa_std             numeric(5,2),
    worsening_streak    smallint,
    first_attempt_share numeric(5,3),
    n_retries           smallint,
    n_no_shows          smallint,
    debts_open          smallint,
    debts_closed        smallint,
    debts_days_open     integer,
    overdue_max         smallint,
    n_unpaid            smallint,
    n_late_paid         smallint,
    group_gpa_prev      numeric(5,2),
    credits_planned     numeric(5,1),
    n_exams_planned     smallint,
    on_leave            boolean,
    leaves_taken        smallint,
    target              smallint,
    dropout_reason      text
);

-- ---------- UCI: статические признаки равны cleaned ----------
CREATE TABLE IF NOT EXISTS features.uci_dropout (LIKE cleaned.uci_dropout);
TRUNCATE TABLE features.uci_dropout;
INSERT INTO features.uci_dropout SELECT * FROM cleaned.uci_dropout;

-- ---------- OULAD: агрегаты активности LMS на студенто-модуль ----------
CREATE TABLE IF NOT EXISTS features.oulad_module (
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
    final_result         text,
    total_clicks         integer,
    active_days          integer,
    first_activity       smallint,
    last_activity        smallint
);
TRUNCATE TABLE features.oulad_module;
INSERT INTO features.oulad_module
SELECT si.code_module, si.code_presentation, si.id_student, si.gender, si.region,
       si.highest_education, si.imd_band, si.age_band, si.num_of_prev_attempts,
       si.studied_credits, si.disability, si.final_result,
       v.total_clicks, v.active_days, v.first_day, v.last_day
FROM cleaned.oulad_student_info si
JOIN (
    SELECT id_student, code_module, code_presentation,
           sum(sum_click) AS total_clicks,
           count(DISTINCT activity_day) AS active_days,
           min(activity_day) AS first_day,
           max(activity_day) AS last_day
    FROM cleaned.oulad_student_vle
    GROUP BY id_student, code_module, code_presentation
) v USING (id_student, code_module, code_presentation);
