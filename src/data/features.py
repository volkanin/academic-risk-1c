"""Слой features: признаки для ML со срезом на дату наблюдения.

Ключевая таблица: features.vs_student_semester, единица наблюдения
«студент, начало семестра». Правила среза (docs/experiment_plan.md, раздел 1):

1. Признак на дату D использует только события с датой события <= D и
   entered_at <= D (дата внесения записи в систему).
2. Текущий статус студента не используется: состояние восстанавливается
   из истории приказов (флаг on_leave считается по приказам с entered_at <= D).
3. Таргет: приказ об отчислении по целевым основаниям (академическая
   задолженность, неисполнение обязанностей по оплате) с датой вступления
   в силу в интервале (D, конец следующего семестра]. Нейтральные
   отчисления дают таргет 0.
4. Точка наблюдения не создаётся после выбытия студента (любое отчисление).

UCI и OULAD: для статического UCI признаки равны cleaned; для OULAD
агрегаты активности LMS на студенто-модуль (SQL в sql/features.sql).

Запуск: .venv/Scripts/python src/data/features.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd

from etl_pipeline import load_rows
from utils.calendar import semester_dates
from utils.db import connect, engine

ROOT = Path(__file__).resolve().parents[2]
TARGET_REASONS = ("академическая задолженность", "неисполнение обязанностей по оплате обучения")

HEADER = [
    "student_id", "program_id", "faculty", "group_code", "observation_date", "semester_no",
    "age", "gender", "region", "is_non_local", "hostel", "privilege", "study_form", "basis",
    "medal", "previous_education", "exam_score", "semesters_done", "gpa_mean", "gpa_last",
    "gpa_prev", "gpa_delta", "gpa_slope", "gpa_std", "worsening_streak", "first_attempt_share",
    "n_retries", "n_no_shows", "debts_open", "debts_closed", "debts_days_open",
    "overdue_max", "n_unpaid", "n_late_paid", "group_gpa_prev", "credits_planned",
    "n_exams_planned", "on_leave", "leaves_taken", "target", "dropout_reason",
]


def _num(x):
    """None вместо NaN: COPY пишет пустую строку как NULL."""
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return None
    return x


def _dates(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    """SQL date приходит из psycopg2 как datetime.date в object-колонках;
    для сравнений со срезом нужен datetime64."""
    for c in cols:
        df[c] = pd.to_datetime(df[c])
    return df


def build_synthetic(eng) -> pd.DataFrame:
    students = _dates(pd.read_sql("SELECT * FROM cleaned.vs_students", eng), ["birth_date", "admission_date"])
    programs = pd.read_sql("SELECT * FROM cleaned.vs_programs", eng)
    admission = pd.read_sql("SELECT * FROM cleaned.vs_admission", eng)
    movements = _dates(pd.read_sql("SELECT * FROM cleaned.vs_movements", eng), ["order_date", "effective_date", "entered_at"])
    grades = _dates(pd.read_sql("SELECT * FROM cleaned.vs_grades", eng), ["exam_date", "entered_at"])
    debts = _dates(pd.read_sql("SELECT * FROM cleaned.vs_debts", eng), ["arose_date", "closed_date", "entered_at"])
    finance = _dates(pd.read_sql("SELECT * FROM cleaned.vs_finance", eng), ["accrual_date", "paid_date", "entered_at"])
    load = pd.read_sql("SELECT * FROM cleaned.vs_load", eng)

    faculty = dict(zip(programs.program_id, programs.faculty))
    semesters_max = load.groupby("program_id")["semester"].max().to_dict()
    adm_orders = movements[movements.order_type == "зачисление"].set_index("student_id")
    exam_score = dict(zip(admission.student_id, admission.exam_score))
    prev_edu = dict(zip(admission.student_id, admission.previous_education))
    medal = dict(zip(admission.student_id, admission.medal))

    # Итоговая попытка по каждой дисциплине семестра
    grades = grades.sort_values(["student_id", "semester", "attempt_no"])
    final = grades.groupby(["student_id", "semester", "discipline_id"], as_index=False).tail(1)

    # Средний балл студента за семестр и средний балл группы за семестр
    student_sem_gpa = final.groupby(["student_id", "semester"])["grade_numeric"].mean().reset_index()
    student_sem_gpa = student_sem_gpa.merge(students[["student_id", "group_code"]], on="student_id")
    group_sem_gpa = student_sem_gpa.groupby(["group_code", "semester"])["grade_numeric"].mean()
    group_code_of = dict(zip(students.student_id, students.group_code))

    leaves = movements[movements.order_type == "академический отпуск"]
    restores = movements[movements.order_type == "восстановление"]
    drops = movements[movements.order_type == "отчисление"]

    load_idx = load.set_index(["program_id", "semester"])

    rows = []
    for stu in students.itertuples(index=False):
        sid = stu.student_id
        adm_year = int(stu.admission_year)
        sem_max = int(semesters_max[stu.program_id])
        adm = adm_orders.loc[sid]

        gr = final[final.student_id == sid]
        de = debts[debts.student_id == sid]
        fi = finance[finance.student_id == sid]
        lv = leaves[leaves.student_id == sid]
        rs = restores[restores.student_id == sid]
        dr = drops[drops.student_id == sid]

        exited_at = dr.effective_date.min() if len(dr) else None

        for s in range(1, sem_max + 1):
            obs = semester_dates(adm_year, s)[0]
            obs_ts = pd.Timestamp(obs)
            if pd.Timestamp(adm.order_date) > obs_ts:
                continue  # приказ о зачислении ещё не издан
            if exited_at is not None and exited_at <= obs_ts:
                break  # студент выбыл до точки наблюдения

            # Академическая история: завершённые семестры с учётом entered_at
            done = gr[(gr.entered_at <= obs_ts) & (gr.semester < s)]
            sem_gpa = done.groupby("semester")["grade_numeric"].mean().sort_index()
            values = sem_gpa.dropna().tolist()
            semesters_done = len(values)
            gpa_mean = float(np.mean(values)) if values else None
            gpa_last = values[-1] if values else None
            gpa_prev = values[-2] if len(values) >= 2 else None
            gpa_delta = (values[-1] - values[-2]) if len(values) >= 2 else None
            if len(values) >= 2:
                gpa_slope = float(np.polyfit(range(len(values)), values, 1)[0])
                gpa_std = float(np.std(values, ddof=1))
            else:
                gpa_slope, gpa_std = None, None
            worsening = 0
            for a, b in zip(reversed(values[1:]), reversed(values[:-1])):
                if b < a:
                    worsening += 1
                else:
                    break
            first_attempt_share = float((done["attempt_no"] == 1).mean()) if len(done) else None
            n_retries = int((done["attempt_no"] > 1).sum())
            n_no_shows = int((grades[(grades.student_id == sid) & (grades.entered_at <= obs_ts)]["grade"] == "неявка").sum())

            # Задолженности на дату наблюдения
            de_done = de[de.entered_at <= obs_ts]
            de_open = de_done[(de_done.arose_date <= obs_ts) & (de_done.closed_date.isna() | (de_done.closed_date > obs_ts))]
            de_closed = de_done[(de_done.closed_date.notna()) & (de_done.closed_date <= obs_ts)]
            debts_days = int(de_closed.days_open.sum()) if len(de_closed) else None

            # Финансовая история (договорники)
            fi_done = fi[fi.entered_at <= obs_ts]
            overdue_max = int(fi_done.overdue_days.max()) if len(fi_done) else None
            n_unpaid = int(fi_done.paid_date.isna().sum())
            n_late_paid = int(((fi_done.overdue_days > 0) & (fi_done.paid_date.notna())).sum())

            # Групповой контекст: средний балл группы за предыдущий семестр
            group_gpa_prev = group_sem_gpa.get((stu.group_code, s - 1), None)

            # Планируемая нагрузка семестра s известна заранее из учебного плана
            try:
                load_row = load_idx.loc[(stu.program_id, s)]
                credits_planned = float(load_row.credits)
                n_exams_planned = int(load_row.n_exams)
            except KeyError:
                credits_planned, n_exams_planned = None, None

            # Академический отпуск на дату наблюдения
            lv_done = lv[lv.entered_at <= obs_ts]
            rs_done = rs[rs.entered_at <= obs_ts]
            on_leave = len(lv_done) > len(rs_done)

            # Таргет: целевое отчисление в (obs, конец следующего семестра]
            window_end = semester_dates(adm_year, s + 1)[1]
            in_window = dr[(dr.effective_date > obs_ts) & (dr.effective_date <= pd.Timestamp(window_end))]
            target = int((in_window.reason.isin(TARGET_REASONS)).any())
            dropout_reason = in_window.reason.iloc[0] if len(in_window) else ""

            rows.append([
                sid, stu.program_id, faculty[stu.program_id], stu.group_code, obs.isoformat(), s,
                int(obs.year - stu.birth_date.year), stu.gender, stu.region, stu.region != "регион вуза",
                stu.hostel, stu.privilege, stu.study_form, stu.basis, medal.get(sid), prev_edu.get(sid),
                _num(exam_score.get(sid)), semesters_done, _num(gpa_mean), _num(gpa_last), _num(gpa_prev),
                _num(gpa_delta), _num(gpa_slope), _num(gpa_std), worsening, _num(first_attempt_share),
                n_retries, n_no_shows, len(de_open), len(de_closed), _num(debts_days),
                _num(overdue_max), n_unpaid, n_late_paid, _num(group_gpa_prev),
                _num(credits_planned), _num(n_exams_planned), on_leave, len(lv_done), target, dropout_reason,
            ])

    # dtype=object сохраняет исходные типы (int остаётся int, None остаётся None):
    # смешанные колонки иначе приводятся к float и COPY получает '0.0' вместо '0'.
    return rows, pd.DataFrame(rows, columns=HEADER, dtype=object)


def main() -> None:
    eng = engine()
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute((ROOT / "sql" / "features.sql").read_text(encoding="utf-8"))
        conn.commit()
        rows, df = build_synthetic(eng)
        n = load_rows(conn, "vs_student_semester", HEADER, rows, schema="features")
        print(f"features.vs_student_semester: {n} точек наблюдения")
        target = df["target"].astype(int)
        print(f"таргет = 1: {int(target.sum())} ({target.mean():.1%})")
        by_course = df.assign(target=target, course=(df.semester_no.astype(int) + 1) // 2).groupby("course")["target"].mean().round(3).to_dict()
        print("доля таргета по курсам:", by_course)
        with conn.cursor() as cur:
            for table in ("uci_dropout", "oulad_module"):
                cur.execute(f"SELECT count(*) FROM features.{table}")
                print(f"features.{table}: {cur.fetchone()[0]}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
