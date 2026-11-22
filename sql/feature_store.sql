-- Offline Feature Store (блок 4.1): SQL-функции исторического заполнения
-- со срезом на дату. Правила среза те же, что в src/data/features.py:
-- 1. Признак на дату D использует только события с датой события <= D и
--    entered_at <= D.
-- 2. Статус студента восстанавливается из истории приказов, текущее
--    состояние не используется.
-- 3. Таргет: целевое отчисление в интервале (D, конец следующего семестра].
-- 4. Точка не создаётся до издания приказа о зачислении и после выбытия.
-- Отличие от витрины features.vs_student_semester: дата наблюдения не
-- привязана к началу семестра, функция строит срез на произвольную дату.
-- Для даты, равной началу семестра, значения признаков совпадают с витриной
-- (проверка согласованности: src/features/verify_offline.py).
-- Для скоринга текущего дня поле target не используется (для даты из
-- будущего оно считалось бы по ещё не случившимся событиям).

-- ---------- Календарь семестров: зеркало src/utils/calendar.py ----------
CREATE OR REPLACE FUNCTION features.semester_dates(p_admission_year integer, p_semester integer)
RETURNS TABLE(start_date date, end_date date)
LANGUAGE sql IMMUTABLE AS $$
    SELECT
        CASE WHEN p_semester % 2 = 1
             THEN make_date(p_admission_year + (p_semester - 1) / 2, 9, 1)
             ELSE make_date(p_admission_year + (p_semester - 1) / 2 + 1, 2, 5) END,
        CASE WHEN p_semester % 2 = 1
             THEN make_date(p_admission_year + (p_semester - 1) / 2, 12, 28)
             ELSE make_date(p_admission_year + (p_semester - 1) / 2 + 1, 6, 15) END
$$;

-- ---------- Offline-таблица признаков: срезы по датам наблюдения ----------
-- Состав колонок следует за витриной features.vs_student_semester.
CREATE TABLE IF NOT EXISTS features.fs_students (
    as_of date NOT NULL,
    LIKE features.vs_student_semester,
    PRIMARY KEY (as_of, student_id)
);

-- Реестр срезов: когда построен, сколько точек и сколько целевых событий.
CREATE TABLE IF NOT EXISTS features.fs_snapshots (
    as_of     date PRIMARY KEY,
    built_at  timestamptz NOT NULL DEFAULT now(),
    n_points  integer NOT NULL,
    n_target  integer NOT NULL
);

-- ---------- Построение одного среза на дату ----------
-- Идемпотентно: старые строки даты удаляются, реестр обновляется.
-- Возвращает число точек среза.
CREATE OR REPLACE FUNCTION features.build_snapshot(p_as_of date) RETURNS integer
LANGUAGE plpgsql AS $$
DECLARE
    v_points integer;
    v_target integer;
BEGIN
    DELETE FROM features.fs_students WHERE as_of = p_as_of;

    WITH movements AS (
        SELECT student_id, order_type, reason, order_date, effective_date, entered_at
        FROM cleaned.vs_movements
    ),
    admission_order AS (
        SELECT student_id, min(order_date) AS order_date
        FROM movements
        WHERE order_type = 'зачисление'
        GROUP BY student_id
    ),
    exits AS (
        SELECT student_id, min(effective_date) AS exited_at
        FROM movements
        WHERE order_type = 'отчисление'
        GROUP BY student_id
    ),

    -- Точки наблюдения: студент и семестр, актуальный на дату p_as_of.
    -- Базовый семестр: наибольший номер с начавшимся семестром. Если дата
    -- уже после конца этого семестра (послесессионный период), студент
    -- считается перешедшим в следующий семестр: его сессия внесена и
    -- входит в академическую историю. После конца последнего семестра
    -- программы точек нет. До зачисления и после выбытия точек нет.
    points AS (
        SELECT st.student_id, st.program_id, st.group_code, st.birth_date,
               st.gender, st.region, st.hostel, st.privilege, st.study_form,
               st.basis, st.admission_year,
               CASE WHEN p_as_of > cur.end_current AND cur.sem < cur.sem_max
                    THEN cur.sem + 1 ELSE cur.sem END AS s_current
        FROM cleaned.vs_students st
        JOIN admission_order adm USING (student_id)
        JOIN LATERAL (
            SELECT b.sem, b.ed AS end_current, b.sem_max
            FROM (
                SELECT g.sem,
                       (features.semester_dates(st.admission_year, g.sem)).start_date AS sd,
                       (features.semester_dates(st.admission_year, g.sem)).end_date AS ed,
                       (SELECT max(l.semester) FROM cleaned.vs_load l
                        WHERE l.program_id = st.program_id) AS sem_max
                FROM generate_series(
                         1,
                         (SELECT max(l.semester) FROM cleaned.vs_load l
                          WHERE l.program_id = st.program_id)
                     ) AS g(sem)
            ) b
            WHERE adm.order_date <= p_as_of AND b.sd <= p_as_of
            ORDER BY b.sem DESC
            LIMIT 1
        ) cur ON true
        WHERE adm.order_date <= p_as_of
          AND NOT (p_as_of > cur.end_current AND cur.sem >= cur.sem_max)
          AND NOT EXISTS (
              SELECT 1 FROM exits e
              WHERE e.student_id = st.student_id AND e.exited_at <= p_as_of
          )
    ),

    -- Академическая история: итоговая попытка по каждой дисциплине
    -- семестра (последняя по номеру попытки), внесённая на дату среза.
    grades_ranked AS (
        SELECT g.student_id, g.semester, g.discipline_id, g.attempt_no,
               g.grade, g.grade_numeric, g.entered_at,
               row_number() OVER (
                   PARTITION BY g.student_id, g.semester, g.discipline_id
                   ORDER BY g.attempt_no DESC
               ) AS rn
        FROM cleaned.vs_grades g
    ),
    final_all AS (
        SELECT student_id, semester, discipline_id, attempt_no, grade_numeric, entered_at
        FROM grades_ranked WHERE rn = 1
    ),
    final_done AS (
        SELECT f.student_id, f.semester, f.attempt_no, f.grade_numeric
        FROM final_all f
        JOIN points p ON p.student_id = f.student_id
        WHERE f.entered_at <= p_as_of AND f.semester < p.s_current
    ),

    -- Средний балл по завершённым семестрам и производные величины.
    -- Наклон: линейный коэффициент по номерам значений 0..n-1,
    -- как np.polyfit в исходном построителе.
    sem_gpa AS (
        SELECT student_id, semester, avg(grade_numeric) AS gpa
        FROM final_done
        GROUP BY student_id, semester
        HAVING avg(grade_numeric) IS NOT NULL
    ),
    sv AS (
        SELECT student_id, semester, gpa,
               row_number() OVER (PARTITION BY student_id ORDER BY semester) - 1 AS x
        FROM sem_gpa
    ),
    acad AS (
        SELECT student_id,
               count(*) AS semesters_done,
               avg(gpa) AS gpa_mean,
               (array_agg(gpa ORDER BY semester DESC))[1] AS gpa_last,
               (array_agg(gpa ORDER BY semester DESC))[2] AS gpa_prev,
               CASE WHEN count(*) >= 2 THEN
                    (count(*) * sum(x * gpa) - sum(x) * sum(gpa))
                    / NULLIF(count(*) * sum(x * x) - sum(x) * sum(x), 0)
               END AS gpa_slope,
               CASE WHEN count(*) >= 2 THEN stddev_samp(gpa) END AS gpa_std
        FROM sv
        GROUP BY student_id
    ),
    pairs AS (
        SELECT student_id, semester, gpa, lag(gpa) OVER (PARTITION BY student_id ORDER BY semester) AS prev_gpa
        FROM sem_gpa
    ),
    worsening AS (
        SELECT student_id,
               coalesce(min(rn_desc) FILTER (WHERE NOT is_worse), max(npairs) + 1) - 1 AS streak
        FROM (
            -- Как в построителе витрины: серия значений, где предыдущий
            -- балл ниже текущего, отсчитывается с конца.
            SELECT student_id, prev_gpa < gpa AS is_worse,
                   row_number() OVER (PARTITION BY student_id ORDER BY semester DESC) AS rn_desc,
                   count(*) OVER (PARTITION BY student_id) AS npairs
            FROM pairs
            WHERE prev_gpa IS NOT NULL
        ) w
        GROUP BY student_id
    ),
    attempts AS (
        SELECT student_id,
               avg(CASE WHEN attempt_no = 1 THEN 1 ELSE 0 END) AS first_attempt_share,
               sum(CASE WHEN attempt_no > 1 THEN 1 ELSE 0 END) AS n_retries
        FROM final_done
        GROUP BY student_id
    ),
    no_shows AS (
        SELECT student_id, count(*) AS n_no_shows
        FROM cleaned.vs_grades
        WHERE entered_at <= p_as_of AND grade = 'неявка'
        GROUP BY student_id
    ),

    -- Задолженности и финансы на дату среза.
    debts AS (
        SELECT student_id,
               count(*) FILTER (WHERE arose_date <= p_as_of
                                 AND (closed_date IS NULL OR closed_date > p_as_of)) AS debts_open,
               count(*) FILTER (WHERE closed_date IS NOT NULL AND closed_date <= p_as_of) AS debts_closed,
               sum(days_open) FILTER (WHERE closed_date IS NOT NULL AND closed_date <= p_as_of) AS debts_days_open
        FROM cleaned.vs_debts
        WHERE entered_at <= p_as_of
        GROUP BY student_id
    ),
    finance AS (
        SELECT student_id,
               max(overdue_days) AS overdue_max,
               count(*) FILTER (WHERE paid_date IS NULL) AS n_unpaid,
               count(*) FILTER (WHERE overdue_days > 0 AND paid_date IS NOT NULL) AS n_late_paid
        FROM cleaned.vs_finance
        WHERE entered_at <= p_as_of
        GROUP BY student_id
    ),

    -- Групповой контекст: средний балл группы за предыдущий семестр
    -- (как в исходном построителе, по итоговым попыткам без фильтра
    -- entered_at).
    student_sem_all AS (
        SELECT f.student_id, st.group_code, f.semester, avg(f.grade_numeric) AS sem_gpa
        FROM final_all f
        JOIN cleaned.vs_students st ON st.student_id = f.student_id
        GROUP BY f.student_id, st.group_code, f.semester
    ),
    group_gpa AS (
        SELECT group_code, semester, avg(sem_gpa) AS gpa
        FROM student_sem_all
        GROUP BY group_code, semester
    ),

    -- Академические отпуска по истории приказов.
    leaves AS (
        SELECT student_id,
               count(*) FILTER (WHERE order_type = 'академический отпуск') AS leaves_taken,
               count(*) FILTER (WHERE order_type = 'восстановление') AS restores
        FROM movements
        WHERE entered_at <= p_as_of
        GROUP BY student_id
    ),

    -- Таргет: целевое отчисление в (p_as_of, конец следующего семестра].
    targets AS (
        SELECT p.student_id,
               bool_or(m.reason IN ('академическая задолженность',
                                    'неисполнение обязанностей по оплате обучения')) AS target,
               (array_agg(m.reason ORDER BY m.effective_date, m.order_date))[1] AS dropout_reason
        FROM points p
        JOIN movements m ON m.student_id = p.student_id
            AND m.order_type = 'отчисление'
            AND m.effective_date > p_as_of
        JOIN LATERAL (
            SELECT (features.semester_dates(p.admission_year, p.s_current + 1)).end_date AS window_end
        ) wnd ON m.effective_date <= wnd.window_end
        GROUP BY p.student_id
    )

    INSERT INTO features.fs_students (
        as_of, student_id, program_id, faculty, group_code, observation_date, semester_no,
        age, gender, region, is_non_local, hostel, privilege, study_form, basis,
        medal, previous_education, exam_score, semesters_done,
        gpa_mean, gpa_last, gpa_prev, gpa_delta, gpa_slope, gpa_std,
        worsening_streak, first_attempt_share, n_retries, n_no_shows,
        debts_open, debts_closed, debts_days_open,
        overdue_max, n_unpaid, n_late_paid,
        group_gpa_prev, credits_planned, n_exams_planned,
        on_leave, leaves_taken, target, dropout_reason
    )
    SELECT
        p_as_of,
        p.student_id,
        p.program_id,
        pr.faculty,
        p.group_code,
        p_as_of,
        p.s_current,
        (date_part('year', p_as_of) - date_part('year', p.birth_date))::smallint,
        p.gender,
        p.region,
        p.region <> 'регион вуза',
        p.hostel,
        p.privilege,
        p.study_form,
        p.basis,
        adm_s.medal,
        adm_s.previous_education,
        adm_s.exam_score,
        coalesce(a.semesters_done, 0),
        a.gpa_mean,
        a.gpa_last,
        a.gpa_prev,
        a.gpa_last - a.gpa_prev,
        a.gpa_slope,
        a.gpa_std,
        coalesce(w.streak, 0),
        at_s.first_attempt_share,
        coalesce(at_s.n_retries, 0),
        coalesce(ns.n_no_shows, 0),
        coalesce(d.debts_open, 0),
        coalesce(d.debts_closed, 0),
        d.debts_days_open,
        f.overdue_max,
        coalesce(f.n_unpaid, 0),
        coalesce(f.n_late_paid, 0),
        gg.gpa,
        l.credits,
        l.n_exams,
        coalesce(lv.leaves_taken > lv.restores, false),
        coalesce(lv.leaves_taken, 0),
        coalesce(t.target, false)::int::smallint,
        t.dropout_reason
    FROM points p
    LEFT JOIN cleaned.vs_programs pr ON pr.program_id = p.program_id
    LEFT JOIN cleaned.vs_admission adm_s ON adm_s.student_id = p.student_id
    LEFT JOIN acad a ON a.student_id = p.student_id
    LEFT JOIN worsening w ON w.student_id = p.student_id
    LEFT JOIN attempts at_s ON at_s.student_id = p.student_id
    LEFT JOIN no_shows ns ON ns.student_id = p.student_id
    LEFT JOIN debts d ON d.student_id = p.student_id
    LEFT JOIN finance f ON f.student_id = p.student_id
    LEFT JOIN group_gpa gg ON gg.group_code = p.group_code AND gg.semester = p.s_current - 1
    LEFT JOIN cleaned.vs_load l ON l.program_id = p.program_id AND l.semester = p.s_current
    LEFT JOIN leaves lv ON lv.student_id = p.student_id
    LEFT JOIN targets t ON t.student_id = p.student_id;

    GET DIAGNOSTICS v_points = ROW_COUNT;
    SELECT count(*) INTO v_target FROM features.fs_students
    WHERE as_of = p_as_of AND target = 1;

    INSERT INTO features.fs_snapshots (as_of, built_at, n_points, n_target)
    VALUES (p_as_of, now(), v_points, v_target)
    ON CONFLICT (as_of) DO UPDATE
        SET built_at = EXCLUDED.built_at,
            n_points = EXCLUDED.n_points,
            n_target = EXCLUDED.n_target;

    RETURN v_points;
END $$;

-- ---------- Историческое заполнение по списку дат наблюдения ----------
-- Даты задаёт вызывающая сторона (обычно послесессионные даты наблюдения
-- или начала семестров для сверки с витриной).
CREATE OR REPLACE FUNCTION features.backfill(p_dates date[])
RETURNS TABLE(as_of date, n_points integer, n_target integer)
LANGUAGE plpgsql AS $$
DECLARE
    d date;
BEGIN
    FOR d IN SELECT unnest(p_dates) ORDER BY 1 LOOP
        PERFORM features.build_snapshot(d);
        RETURN QUERY SELECT s.as_of, s.n_points, s.n_target
                     FROM features.fs_snapshots s
                     WHERE s.as_of = d;
    END LOOP;
END $$;

-- ---------- Удобные чтения ----------
-- Дата последнего среза.
CREATE OR REPLACE FUNCTION features.latest_snapshot() RETURNS date
LANGUAGE sql STABLE AS $$
    SELECT max(as_of) FROM features.fs_students
$$;

-- Актуальный срез для материализации в online-хранилище.
CREATE OR REPLACE VIEW features.fs_latest AS
SELECT s.*
FROM features.fs_students s
WHERE s.as_of = (SELECT max(as_of) FROM features.fs_students);
