"""Генератор синтетического контингента вуза в формате витрин 1С:Университет.

Зачем нужен: реальные данные студентов недоступны для исследования (см.
docs/requirements.md, NFR-2), интеграционный контур демонстрируется на
демонстрационной базе с условным контингентом. Генератор выгружает те же
предметные витрины, которые в промышленной эксплуатации отдаёт
интеграционный слой 1С (см. docs/TZ_1C.md).

Учебный план моделируется двумя профилями. Творческий профиль построен по
образцу реальных учебных планов УрГАХУ (07.03.01 Архитектура, 10 семестров;
54.03.01 Дизайн и 54.03.02 ДПИ, 8 семестров): 6-9 дисциплин в семестре,
1-2 экзамена, преобладают зачёт и зачёт с оценкой, со второго семестра
курсовой проект, 26-34 ЗЕТ. Технический профиль соответствует типовым
планам инженерных и экономических направлений: 5-9 дисциплин, 2-4 экзамена.

Принципы, которые нельзя нарушать при доработке:
1. Целевое событие не задаётся детерминированной формулой от признаков.
   Риск отчисления определяется скрытой моделью (способность, мотивация,
   задолженности, просрочки оплаты) плюс ненаблюдаемый фактор и случайный шум.
   Ненаблюдаемый фактор в витрины не попадает.
2. У каждой записи фактов есть entered_at (дата внесения в систему), обычно
   отстоящая от даты события на несколько дней. Это позволяет проверять
   срез на дату наблюдения и утечку (docs/experiment_plan.md, раздел 1).
3. Есть нейтральные виды отчислений (по собственному желанию, перевод в
   другой вуз), статистически не связанные с успеваемостью: они не входят
   в положительный класс и проверяют честность классификации оснований.

Числовые параметры (доля отчислений, распределение по курсам, соотношение
оснований) обоснованы публичной статистикой и зафиксированы в
docs/data_generation.md; там же фактические значения последней генерации.

Запуск: .venv/Scripts/python src/data/generator.py
Выгрузка: data/raw/synthetic/*.csv (в репозиторий не попадает) и meta.json.
"""

from __future__ import annotations

import csv
import json
import math
import random
import sys
import uuid
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from utils.calendar import semester_dates, session_window

ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "data" / "raw" / "synthetic"

SEED = 20261120
ADMISSION_YEARS = [2019, 2020, 2021, 2022, 2023]
STUDENTS_PER_YEAR = 720
MAX_SEMESTERS = 10

# (факультет, направление, сложность, профиль плана, число семестров)
PROGRAMS = [
    ("Институт компьютерных технологий", "09.03.01 Информатика и вычислительная техника", 0.15, "технический", 8),
    ("Институт компьютерных технологий", "09.03.04 Программная инженерия", 0.10, "технический", 8),
    ("Институт экономики и управления", "38.03.01 Экономика", -0.10, "технический", 8),
    ("Институт экономики и управления", "38.03.03 Управление персоналом", -0.20, "технический", 8),
    ("Институт энергетики", "13.03.02 Электроэнергетика и электротехника", 0.15, "технический", 8),
    ("Институт энергетики", "15.03.04 Автоматизация технологических процессов", 0.05, "технический", 8),
    ("Институт архитектуры и дизайна", "07.03.01 Архитектура", 0.25, "творческий", 10),
    ("Институт архитектуры и дизайна", "54.03.01 Дизайн", 0.10, "творческий", 8),
    ("Институт архитектуры и дизайна", "54.03.02 Декоративно-прикладное искусство и народные промыслы", 0.10, "творческий", 8),
]

# Профили учебного плана. Творческий по образцу планов УрГАХУ: мало экзаменов,
# зачёт с оценкой, курсовой проект со 2 семестра. Технический: типовые планы
# инженерных и экономических направлений.
PROFILE_CONTROL = {
    "технический": {"n_exams": (2, 4), "n_tests": (3, 5), "graded_tests_share": 0.35, "course_project": False},
    "творческий": {"n_exams": (1, 2), "n_tests": (5, 7), "graded_tests_share": 0.6, "course_project": True},
}

REGIONS = ["регион вуза", "другой регион", "зарубежье"]
PREV_EDU = ["школа", "колледж", "после вуза"]
DISCIPLINE_TOPICS = {
    "ИКТ": ["Математический анализ", "Алгебра и геометрия", "Программирование", "Дискретная математика", "Архитектура ЭВМ", "Базы данных", "Операционные системы", "Теория вероятностей", "Численные методы", "Сетевые технологии", "Алгоритмы и структуры данных", "Компьютерная графика"],
    "ЭиУ": ["Микроэкономика", "Макроэкономика", "Бухгалтерский учёт", "Статистика", "Финансы предприятия", "Правоведение", "Маркетинг", "Управление проектами", "Экономический анализ", "Деловые коммуникации"],
    "ЭН": ["Физика", "Теоретическая механика", "Электротехника", "Электроника", "Сопротивление материалов", "Метрология", "Теплотехника", "Материаловедение", "Надёжность оборудования", "Охрана труда"],
    "АиД": ["Академический рисунок", "Академическая живопись", "Проектирование", "История искусств", "Пропедевтика", "Материаловедение", "Композиция", "Макетирование", "Скульптура и пластическое моделирование", "Профессиональные коммуникации"],
}


def sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def clamp(x: float, low: float, high: float) -> float:
    return max(low, min(high, x))


def grade_pass_score(rng: random.Random, ability: float) -> int:
    """Оценка за сдаваемый экзамен: 3..5 в зависимости от способности."""
    x = rng.random() + 0.25 * ability
    if x < 0.45:
        return 3
    if x < 0.8:
        return 4
    return 5


def main() -> None:
    rng = random.Random(SEED)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # Справочники
    programs = []
    for i, (faculty, title, difficulty, profile, semesters) in enumerate(PROGRAMS, 1):
        programs.append({
            "program_id": str(uuid.UUID(int=rng.getrandbits(128), version=4)),
            "title": title,
            "faculty": faculty,
            "difficulty": difficulty,
            "profile": profile,
            "semesters": semesters,
            "study_form": "очная",
            "basis": "бюджет",
        })

    # Дисциплины и нагрузка по семестрам
    disciplines = []
    load_rows = []
    for p in programs:
        fc = p["faculty"].split()[1][:3].upper()
        topics = DISCIPLINE_TOPICS["ИКТ" if "компьютерных" in p["faculty"] else ("ЭиУ" if "экономики" in p["faculty"] else ("АиД" if "архитектуры" in p["faculty"] else "ЭН"))]
        profile = PROFILE_CONTROL[p["profile"]]
        for semester in range(1, p["semesters"] + 1):
            n_exams = rng.randint(*profile["n_exams"])
            n_tests = rng.randint(*profile["n_tests"])
            n_graded = round(n_tests * profile["graded_tests_share"])
            load_rows.append({
                "program_id": p["program_id"],
                "semester": semester,
                "n_exams": n_exams,
                "n_tests": n_tests,
                "n_course_projects": 1 if (profile["course_project"] and semester >= 2) else 0,
                "credits": 0.0,  # заполняется суммой ЗЕТ дисциплин ниже
            })
            n_disc = n_exams + n_tests + (1 if load_rows[-1]["n_course_projects"] else 0)
            for k in range(n_disc):
                if k < n_exams:
                    control_type, base_fail_shift, credits = "экзамен", 0.0, round(rng.uniform(3, 5), 1)
                elif k < n_exams + n_tests - n_graded:
                    control_type, base_fail_shift, credits = "зачёт", -0.25, round(rng.uniform(2, 4), 1)
                elif k < n_exams + n_tests:
                    control_type, base_fail_shift, credits = "зачёт с оценкой", -0.15, round(rng.uniform(2, 4), 1)
                else:
                    control_type, base_fail_shift, credits = "курсовой проект", -0.5, round(rng.uniform(4, 6), 1)
                base = topics[(semester * n_disc + k) % len(topics)]
                disciplines.append({
                    "discipline_id": str(uuid.UUID(int=rng.getrandbits(128), version=4)),
                    "title": f"{base} {semester} сем." if control_type != "курсовой проект" else f"Курсовой проект по профильному проектированию {semester} сем.",
                    "program_id": p["program_id"],
                    "semester": semester,
                    "control_type": control_type,
                    "credits": credits,
                })
            load_rows[-1]["credits"] = round(sum(d["credits"] for d in disciplines[-n_disc:]), 1)
    disc_by_program_sem = {}
    for d in disciplines:
        disc_by_program_sem.setdefault((d["program_id"], d["semester"]), []).append(d)

    students = []
    admissions = []
    movements = []
    grades = []
    debts = []
    finance = []

    stats = {"academic_dropout": 0, "finance_dropout": 0, "own_dropout": 0, "transfer_dropout": 0, "survived": 0, "academic_leave": 0}

    for year in ADMISSION_YEARS:
        for n in range(STUDENTS_PER_YEAR):
            p = rng.choice(programs)
            basis = "договор" if rng.random() > (0.45 if p["difficulty"] > 0 else 0.35) else "бюджет"
            study_form = "заочная" if rng.random() < 0.18 else "очная"
            region = rng.choices(REGIONS, weights=[0.55, 0.35, 0.10])[0]
            student_id = str(uuid.UUID(int=rng.getrandbits(128), version=4))
            group_code = f"{p['faculty'].split()[1][:4].upper()}-{year % 100}-{rng.randint(1, 4)}"
            admission_date = date(year, 9, 1)
            birth_year = year - rng.randint(17, 24)

            ability = rng.gauss(0, 1)
            motivation = 0.3 * ability + rng.gauss(0, 1)
            unobserved = rng.gauss(0, 1)  # в витрины не попадает
            exam_score = round(clamp(65 + 8 * ability + rng.gauss(0, 6), 45, 100), 1)

            students.append({
                "student_id": student_id,
                "program_id": p["program_id"],
                "group_code": group_code,
                "gender": "муж" if rng.random() < 0.47 else "жен",
                "birth_date": date(birth_year, rng.randint(1, 12), rng.randint(1, 28)).isoformat(),
                "region": region,
                "hostel": "true" if (region != "регион вуза" and rng.random() < 0.6) else "false",
                "privilege": "true" if rng.random() < 0.08 else "false",
                "study_form": study_form,
                "basis": basis,
                "admission_year": year,
                "admission_date": admission_date.isoformat(),
            })
            admissions.append({
                "student_id": student_id,
                "exam_score": exam_score,
                "previous_education": rng.choices(PREV_EDU, weights=[0.72, 0.25, 0.03])[0],
                "priority": rng.randint(1, 3),
                "medal": "true" if rng.random() < 0.05 else "false",
            })
            movements.append({
                "student_id": student_id,
                "order_type": "зачисление",
                "reason": "результаты вступительных испытаний",
                "order_date": date(year, 8, rng.randint(15, 25)).isoformat(),
                "effective_date": admission_date.isoformat(),
                "entered_at": (admission_date + timedelta(days=rng.randint(0, 4))).isoformat(),
            })

            # Платёжная дисциплина договорников
            pay_discipline = sigmoid(0.6 + 0.5 * motivation) if basis == "договор" else None

            active_debts = 0
            on_leave = False
            leave_left = 0
            alive = True
            sem_i = 1
            while sem_i <= p["semesters"] and alive:
                if on_leave:
                    leave_left -= 1
                    if leave_left == 0:
                        on_leave = False
                        movements.append({
                            "student_id": student_id,
                            "order_type": "восстановление",
                            "reason": "окончание академического отпуска",
                            "order_date": semester_dates(year, sem_i)[0].isoformat(),
                            "effective_date": semester_dates(year, sem_i)[0].isoformat(),
                            "entered_at": (semester_dates(year, sem_i)[0] + timedelta(days=rng.randint(0, 5))).isoformat(),
                        })
                    else:
                        sem_i += 1
                        continue

                sem_start, sem_end = semester_dates(year, sem_i)
                exam_from, exam_to = session_window(sem_end)

                # Академический отпуск
                if rng.random() < 0.008:
                    movements.append({
                        "student_id": student_id,
                        "order_type": "академический отпуск",
                        "reason": "по семейным обстоятельствам" if rng.random() < 0.5 else "по состоянию здоровья",
                        "order_date": (sem_start + timedelta(days=rng.randint(5, 30))).isoformat(),
                        "effective_date": (sem_start + timedelta(days=rng.randint(30, 60))).isoformat(),
                        "entered_at": (sem_start + timedelta(days=rng.randint(30, 65))).isoformat(),
                    })
                    stats["academic_leave"] += 1
                    on_leave = True
                    leave_left = rng.choice([1, 2])
                    sem_i += 1
                    continue

                # Оплата семестра (только договор)
                overdue_days = 0
                if basis == "договор":
                    amount = round(rng.uniform(55_000, 120_000), 2)
                    due = sem_start + timedelta(days=15)
                    if rng.random() < sigmoid(-2.2 - 0.5 * motivation - 0.4 * unobserved):
                        paid_date = None
                        overdue_days = rng.randint(60, 180)
                    elif rng.random() < pay_discipline:
                        paid_date = due - timedelta(days=rng.randint(0, 14))
                        overdue_days = 0
                    else:
                        overdue_days = rng.randint(3, 45)
                        paid_date = due + timedelta(days=overdue_days)
                    finance.append({
                        "student_id": student_id,
                        "accrual_date": due.isoformat(),
                        "amount": amount,
                        "paid_date": paid_date.isoformat() if paid_date else "",
                        "overdue_days": overdue_days,
                        "entered_at": (due + timedelta(days=rng.randint(0, 3))).isoformat(),
                    })

                # Сессия
                sem_disc = disc_by_program_sem[(p["program_id"], sem_i)]
                new_debts = 0
                overdue_now = 1 if (basis == "договор" and overdue_days > 30) else 0
                for d in sem_disc:
                    exam_date = exam_from + timedelta(days=rng.randint(0, (exam_to - exam_from).days))
                    p_fail = sigmoid(-1.9 - 0.85 * ability - 0.45 * motivation + p["difficulty"] + rng.gauss(0, 0.35))
                    if d["control_type"] == "зачёт":
                        p_fail -= 0.25
                    elif d["control_type"] == "зачёт с оценкой":
                        p_fail -= 0.15
                    elif d["control_type"] == "курсовой проект":
                        p_fail -= 0.5
                    numeric = d["control_type"] in ("экзамен", "зачёт с оценкой", "курсовой проект")
                    passed = False
                    attempt = 1
                    max_attempts = 2 if d["control_type"] == "курсовой проект" else 3
                    while attempt <= max_attempts and not passed:
                        if attempt == 1:
                            p_pass = 1 - p_fail
                        else:
                            p_pass = sigmoid(-0.3 - 0.8 * ability + 0.6 * motivation + rng.gauss(0, 0.2))
                        absent = attempt == 1 and rng.random() < 0.02
                        fail_date = exam_date + timedelta(days=(attempt - 1) * rng.randint(7, 14))
                        if absent:
                            grades.append({
                                "student_id": student_id,
                                "discipline_id": d["discipline_id"],
                                "semester": sem_i,
                                "attempt_no": attempt,
                                "grade": "неявка",
                                "exam_date": exam_date.isoformat(),
                                "absent": "true",
                                "entered_at": (exam_date + timedelta(days=rng.randint(1, 3))).isoformat(),
                            })
                        elif rng.random() < p_pass:
                            value = grade_pass_score(rng, ability) if numeric else "зачёт"
                            grades.append({
                                "student_id": student_id,
                                "discipline_id": d["discipline_id"],
                                "semester": sem_i,
                                "attempt_no": attempt,
                                "grade": str(value) if isinstance(value, int) else value,
                                "exam_date": fail_date.isoformat(),
                                "absent": "false",
                                "entered_at": (fail_date + timedelta(days=rng.randint(1, 5))).isoformat(),
                            })
                            passed = True
                        else:
                            grades.append({
                                "student_id": student_id,
                                "discipline_id": d["discipline_id"],
                                "semester": sem_i,
                                "attempt_no": attempt,
                                "grade": "2" if numeric else "незачёт",
                                "exam_date": fail_date.isoformat(),
                                "absent": "false",
                                "entered_at": (fail_date + timedelta(days=rng.randint(1, 5))).isoformat(),
                            })
                        attempt += 1
                    if not passed:
                        new_debts += 1
                        arose = exam_to + timedelta(days=rng.randint(1, 5))
                        # Задолженность закрывается в следующем семестре с вероятностью,
                        # зависящей от способности, иначе тянется дальше.
                        p_close = sigmoid(-0.6 - 0.8 * ability + 0.4 * motivation)
                        if rng.random() < p_close:
                            closed = semester_dates(year, min(sem_i + 1, MAX_SEMESTERS))[1] + timedelta(days=rng.randint(2, 20))
                            debts.append({
                                "student_id": student_id,
                                "discipline_id": d["discipline_id"],
                                "semester": sem_i,
                                "arose_date": arose.isoformat(),
                                "closed_date": closed.isoformat(),
                                "entered_at": (arose + timedelta(days=rng.randint(1, 7))).isoformat(),
                            })
                        else:
                            debts.append({
                                "student_id": student_id,
                                "discipline_id": d["discipline_id"],
                                "semester": sem_i,
                                "arose_date": arose.isoformat(),
                                "closed_date": "",
                                "entered_at": (arose + timedelta(days=rng.randint(1, 7))).isoformat(),
                            })
                active_debts = max(0, active_debts - rng.randint(0, 2)) + new_debts

                # Нейтральные отчисления: независимый поток, не связан с успеваемостью
                if rng.random() < 0.012:
                    movements.append({
                        "student_id": student_id,
                        "order_type": "отчисление",
                        "reason": "по собственному желанию",
                        "order_date": (sem_end + timedelta(days=rng.randint(-5, 10))).isoformat(),
                        "effective_date": (sem_end + timedelta(days=rng.randint(10, 20))).isoformat(),
                        "entered_at": (sem_end + timedelta(days=rng.randint(10, 25))).isoformat(),
                    })
                    stats["own_dropout"] += 1
                    alive = False
                    break
                if rng.random() < 0.003:
                    movements.append({
                        "student_id": student_id,
                        "order_type": "отчисление",
                        "reason": "перевод в другой вуз",
                        "order_date": (sem_end + timedelta(days=rng.randint(-5, 10))).isoformat(),
                        "effective_date": (sem_end + timedelta(days=rng.randint(10, 20))).isoformat(),
                        "entered_at": (sem_end + timedelta(days=rng.randint(10, 25))).isoformat(),
                    })
                    stats["transfer_dropout"] += 1
                    alive = False
                    break

                # Риск академического/финансового отчисления: скрытая модель + шум.
                # Ранние семестры дают дополнительный фактор отсева: в российских
                # вузах максимум отчислений приходится на первый курс
                # (см. docs/data_generation.md, раздел «Обоснование параметров»).
                sem_factor = 1.15 if sem_i == 1 else (0.75 if sem_i == 2 else (0.35 if sem_i <= 4 else 0.05))
                hazard = sigmoid(
                    -4.7
                    + 0.75 * active_debts
                    + 0.7 * overdue_now
                    + 0.45 * max(0.0, -ability)
                    + 0.7 * unobserved
                    + sem_factor
                    + rng.gauss(0, 0.3)
                )
                if rng.random() < hazard:
                    # Основание: при задолженности по оплате у договорников чаще
                    # финансовое отчисление, иначе академическое (распределение
                    # оснований обосновано в docs/data_generation.md).
                    if basis == "договор" and overdue_now and rng.random() < 0.75:
                        reason = "неисполнение обязанностей по оплате обучения"
                        stats["finance_dropout"] += 1
                    else:
                        reason = "академическая задолженность"
                        stats["academic_dropout"] += 1
                    movements.append({
                        "student_id": student_id,
                        "order_type": "отчисление",
                        "reason": reason,
                        "order_date": (sem_end + timedelta(days=rng.randint(0, 12))).isoformat(),
                        "effective_date": (sem_end + timedelta(days=rng.randint(12, 25))).isoformat(),
                        "entered_at": (sem_end + timedelta(days=rng.randint(12, 30))).isoformat(),
                    })
                    alive = False
                    break

                sem_i += 1

            if alive:
                stats["survived"] += 1

    tables = {
        "vs_programs": [{k: v for k, v in p.items() if k in ("program_id", "title", "faculty", "study_form", "basis")} for p in programs],
        "vs_students": students,
        "vs_disciplines": disciplines,
        "vs_admission": admissions,
        "vs_movements": movements,
        "vs_grades": grades,
        "vs_debts": debts,
        "vs_finance": finance,
        "vs_load": load_rows,
    }
    headers = {
        "vs_programs": ["program_id", "title", "faculty", "study_form", "basis"],
        "vs_students": ["student_id", "program_id", "group_code", "gender", "birth_date", "region", "hostel", "privilege", "study_form", "basis", "admission_year", "admission_date"],
        "vs_disciplines": ["discipline_id", "title", "program_id", "semester", "control_type", "credits"],
        "vs_admission": ["student_id", "exam_score", "previous_education", "priority", "medal"],
        "vs_movements": ["student_id", "order_type", "reason", "order_date", "effective_date", "entered_at"],
        "vs_grades": ["student_id", "discipline_id", "semester", "attempt_no", "grade", "exam_date", "absent", "entered_at"],
        "vs_debts": ["student_id", "discipline_id", "semester", "arose_date", "closed_date", "entered_at"],
        "vs_finance": ["student_id", "accrual_date", "amount", "paid_date", "overdue_days", "entered_at"],
        "vs_load": ["program_id", "semester", "n_exams", "n_tests", "n_course_projects", "credits"],
    }
    for name, rows in tables.items():
        with (OUT_DIR / f"{name}.csv").open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=headers[name])
            w.writeheader()
            w.writerows(rows)
        print(f"{name}: {len(rows)} строк")

    meta = {
        "seed": SEED,
        "admission_years": ADMISSION_YEARS,
        "students": len(students),
        "profiles": {p["title"]: {"profile": p["profile"], "semesters": p["semesters"]} for p in programs},
        "stats": stats,
        "note": "Синтетический контингент для демонстрации интеграции и конвейера; качество моделей оценивается на открытых данных (docs/requirements.md, ограничения).",
    }
    (OUT_DIR / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(meta["stats"], ensure_ascii=False))


if __name__ == "__main__":
    main()
