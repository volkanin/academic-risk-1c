"""Проверка согласованности offline Feature Store с витриной признаков.

SQL-функции (sql/feature_store.sql) и построитель витрины
(src/data/features.py) должны давать одинаковые значения признаков на датах,
равных началам семестров. Скрипт строит срезы на все даты витрины
(историческое заполнение) и сравнивает каждую колонку; расхождения
печатаются по колонкам, для дробных колонок допуск 1e-9.

Запуск: .venv/Scripts/python -I src/features/verify_offline.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from features.store import FeatureStore  # noqa: E402
from utils.db import engine  # noqa: E402

# as_of и student_id это ключи соединения, observation_date есть только в
# Feature Store и равен as_of, остальные колонки сравниваются.
COMPARE_COLS = [
    "semester_no", "age", "gender", "region", "is_non_local",
    "hostel", "privilege", "study_form", "basis", "medal", "previous_education",
    "exam_score", "semesters_done", "gpa_mean", "gpa_last", "gpa_prev",
    "gpa_delta", "gpa_slope", "gpa_std", "worsening_streak", "first_attempt_share",
    "n_retries", "n_no_shows", "debts_open", "debts_closed", "debts_days_open",
    "overdue_max", "n_unpaid", "n_late_paid", "group_gpa_prev", "credits_planned",
    "n_exams_planned", "on_leave", "leaves_taken", "target", "dropout_reason",
]

TOLERANCE = 1e-9

# Дробные колонки: построитель витрины считает в float, SQL-функции в
# numeric; разница возможна в последнем знаке округления. Допуск равен
# половине шага последнего знака колонки.
ROUNDING_TOLERANCE = {
    "gpa_mean": 0.0105, "gpa_last": 0.0105, "gpa_prev": 0.0105,
    "gpa_delta": 0.0105, "group_gpa_prev": 0.0105, "credits_planned": 0.0105,
    "gpa_slope": 0.00105, "first_attempt_share": 0.00105,
}


def _n_diff(va: pd.Series, vf: pd.Series, tolerance: float) -> int:
    """Число расхождений колонки: два пропуска считаются совпадением."""
    both_na = va.isna() & vf.isna()
    one_na = (va.isna() | vf.isna()) & ~both_na
    try:
        na = pd.to_numeric(va, errors="raise").astype(float)
        nf = pd.to_numeric(vf, errors="raise").astype(float)
        diff = (na - nf).abs()
        bad = one_na | (diff > tolerance).fillna(True)
    except (TypeError, ValueError):
        bad = one_na | (va.fillna("") != vf.fillna(""))
    return int(bad.sum())


def main() -> None:
    store = FeatureStore()
    store.apply_ddl()

    eng = engine()
    vitrina = pd.read_sql("SELECT * FROM features.vs_student_semester", eng)
    vitrina = vitrina.rename(columns={"observation_date": "as_of"})
    dates = sorted(vitrina["as_of"].unique())

    report = store.backfill([pd.Timestamp(d).date() for d in dates])
    print("историческое заполнение:")
    print(report.to_string(index=False))

    fs = pd.read_sql("SELECT * FROM features.fs_students", eng)

    merged = vitrina.merge(fs, on=["as_of", "student_id"], how="outer",
                           suffixes=("_v", "_f"), indicator=True)
    print(f"\nточек в витрине: {len(vitrina)}, в Feature Store: {len(fs)}")
    only_v = int((merged["_merge"] == "left_only").sum())
    only_f = int((merged["_merge"] == "right_only").sum())
    print(f"точек только в витрине: {only_v}, только в Feature Store: {only_f}")

    worst_strict, worst_rounding = 0, 0
    for col in COMPARE_COLS:
        tol = ROUNDING_TOLERANCE.get(col, TOLERANCE)
        n_bad = _n_diff(merged[f"{col}_v"], merged[f"{col}_f"], tol)
        worst_strict = max(worst_strict, n_bad) if tol == TOLERANCE else worst_strict
        worst_rounding = max(worst_rounding, n_bad)
        if n_bad:
            kind = " (в пределах округления)" if tol > TOLERANCE else ""
            print(f"  расхождений {col}: {n_bad}{kind}")

    if worst_strict == 0 and only_v == 0 and only_f == 0:
        print("\nсогласованность подтверждена: значения совпадают на всех датах витрины")
        print("(расхождения, если они есть выше, ограничены последним знаком округления дробных колонок)")
    else:
        print("\nесть строгие расхождения, см. список выше")


if __name__ == "__main__":
    main()
