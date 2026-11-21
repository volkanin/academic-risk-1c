"""Календарь учебного года: границы семестров и сессий.

Используется генератором витрин и построителем признаков, чтобы оба
опирались на один календарь.
"""

from __future__ import annotations

from datetime import date, timedelta


def semester_dates(admission_year: int, semester_no: int) -> tuple[date, date]:
    """Границы семестра: нечётные осень, чётные весна."""
    y = admission_year + (semester_no - 1) // 2
    if semester_no % 2 == 1:
        return date(y, 9, 1), date(y, 12, 28)
    return date(y + 1, 2, 5), date(y + 1, 6, 15)


def session_window(end: date) -> tuple[date, date]:
    """Окно сессии: последние три недели семестра."""
    return end - timedelta(days=20), end - timedelta(days=2)
