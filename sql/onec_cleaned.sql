-- Слой cleaned: реальные витрины 1С вместо синтетики.
-- Исполнитель: src/onec_client/load.py, вызывается после загрузки raw
-- (sql/onec_raw.sql). Таблицы vs_* те же, что для витрин генератора:
-- контракты витрин расширения писались по полям этого слоя
-- (docs/1c_enhancements_tz.md). Синтетические данные замещаются и при
-- необходимости воспроизводятся генератором src/data/generator.py и
-- sql/schema.sql + sql/layers.sql. UCI и OULAD не затрагиваются.
--
-- Отличия реальных данных от синтетики в cleaned:
-- birth_date пустое, возраст в колонке age (ПДн, дата рождения в витрину
-- не попадает); book_hash псевдоним зачетной книжки (соединяется с
-- username_hash Moodle); admission_date выводится из приказов зачисления;
-- в движениях добавлены order_kind, analytics и order_class (класс приказа
-- по утвержденному делению); в оценках program_id; отметки стобалльной
-- шкалы не переводятся в пятибалльную: порог неудовлетворительной отметки
-- 40 баллов (решение заказчика 28.11.2026), is_pass для чисел это отметка
-- 40 и выше; витрина admission пустая (вступительные испытания в
-- источниках витриной не отдаются); финансы в этой базе отсутствуют,
-- источник будет в базе бухгалтерии.

ALTER TABLE cleaned.vs_programs ADD COLUMN IF NOT EXISTS year text;
ALTER TABLE cleaned.vs_programs ADD COLUMN IF NOT EXISTS hours_per_credit integer;
ALTER TABLE cleaned.vs_students ADD COLUMN IF NOT EXISTS faculty text;
ALTER TABLE cleaned.vs_students ADD COLUMN IF NOT EXISTS state text;
ALTER TABLE cleaned.vs_students ADD COLUMN IF NOT EXISTS book_hash text;
ALTER TABLE cleaned.vs_students ADD COLUMN IF NOT EXISTS age integer;
ALTER TABLE cleaned.vs_movements ADD COLUMN IF NOT EXISTS order_kind text;
ALTER TABLE cleaned.vs_movements ADD COLUMN IF NOT EXISTS analytics text;
ALTER TABLE cleaned.vs_movements ADD COLUMN IF NOT EXISTS order_class text;
ALTER TABLE cleaned.vs_grades ADD COLUMN IF NOT EXISTS program_id text;
ALTER TABLE cleaned.vs_grades ADD COLUMN IF NOT EXISTS control_type text;
ALTER TABLE cleaned.vs_debts ADD COLUMN IF NOT EXISTS program_id text;
ALTER TABLE cleaned.vs_load ADD COLUMN IF NOT EXISTS n_disciplines integer;
ALTER TABLE cleaned.vs_load ADD COLUMN IF NOT EXISTS hours integer;
ALTER TABLE cleaned.vs_load ADD COLUMN IF NOT EXISTS hours_per_credit integer;
ALTER TABLE cleaned.vs_load ADD COLUMN IF NOT EXISTS credits_source text;

TRUNCATE TABLE cleaned.vs_programs;
INSERT INTO cleaned.vs_programs
    (program_id, title, faculty, study_form, basis, year, hours_per_credit)
SELECT program_id, title, faculty, study_form, NULL, year, hours_per_credit
FROM raw.onec_programs;

TRUNCATE TABLE cleaned.vs_students;
INSERT INTO cleaned.vs_students
    (student_id, program_id, group_code, faculty, gender, birth_date, region, hostel, privilege,
     study_form, basis, admission_year, admission_date, book_hash, age, state)
SELECT s.student_id, s.program_id, s.group_code, s.faculty, s.gender, NULL, s.region, s.hostel,
       s.privilege, s.study_form, s.basis,
       EXTRACT(YEAR FROM adm.admission_date)::int, adm.admission_date, s.book_hash, s.age, s.state
FROM raw.onec_students s
LEFT JOIN (
    SELECT student_id, MIN(effective_date) AS admission_date
    FROM raw.onec_movements
    WHERE lower(order_type) LIKE 'зачислен%'
    GROUP BY student_id
) adm ON adm.student_id = s.student_id;

TRUNCATE TABLE cleaned.vs_disciplines;
INSERT INTO cleaned.vs_disciplines
    (discipline_id, title, program_id, semester, control_type, credits)
SELECT discipline_id, discipline_title, program_id, semester_num, load_type, credits
FROM raw.onec_disciplines;

TRUNCATE TABLE cleaned.vs_movements;
INSERT INTO cleaned.vs_movements
    (student_id, order_type, reason, order_date, effective_date, entered_at,
     order_kind, analytics, order_class)
SELECT student_id, order_type, reason, order_date, effective_date, NULL,
       order_kind, analytics, order_class
FROM raw.onec_movements;

TRUNCATE TABLE cleaned.vs_grades;
INSERT INTO cleaned.vs_grades
    (student_id, discipline_id, program_id, semester, attempt_no, grade, exam_date,
     absent, entered_at, grade_numeric, is_pass, control_type)
SELECT student_id, discipline_id, program_id, semester_num, attempt_no::smallint, grade, exam_date,
       absent, NULL,
       CASE
           WHEN grade = 'Отлично' THEN 5
           WHEN grade = 'Хорошо' THEN 4
           WHEN grade = 'Удовлетворительно' THEN 3
           WHEN grade = 'Зачтено' THEN 4
           WHEN grade IN ('Неудовлетворительно', 'Не зачтено') THEN 2
           WHEN grade ~ '^[0-9]+$' AND grade::int = 2 THEN 2
           ELSE NULL
       END AS grade_numeric,
       CASE
           WHEN grade IN ('Отлично', 'Хорошо', 'Удовлетворительно', 'Зачтено') THEN true
           WHEN grade ~ '^[0-9]+$' THEN grade::int >= 40
           ELSE false
       END AS is_pass,
       control_type
FROM raw.onec_grades;

TRUNCATE TABLE cleaned.vs_debts;
INSERT INTO cleaned.vs_debts
    (student_id, discipline_id, program_id, semester, arose_date, closed_date,
     entered_at, is_closed, days_open)
SELECT student_id, discipline_id, program_id, semester_num, arose_date, closed_date,
       NULL,
       closed_date IS NOT NULL,
       CASE WHEN closed_date IS NOT NULL THEN (closed_date - arose_date) END
FROM raw.onec_debts;

TRUNCATE TABLE cleaned.vs_finance;
TRUNCATE TABLE cleaned.vs_admission;

TRUNCATE TABLE cleaned.vs_load;
INSERT INTO cleaned.vs_load
    (program_id, semester, n_exams, n_tests, n_course_projects, credits,
     n_disciplines, hours, hours_per_credit, credits_source)
SELECT program_id, semester_num, n_exams, n_tests, n_course_works, credits,
       n_disciplines, hours, hours_per_credit, credits_source
FROM raw.onec_load;
