"""Генератор архитектурной диаграммы: docs/architecture.png.

Цвета по брендбуку Контур Университет: синий 1E3CD4 основной, красный FC0F3C акцент,
тёмно-серый 25282B для текста. Заливки: светлые примеси фирменного синего с белым,
красный только для акцентных элементов и особых потоков.

Перегенерация: .venv/Scripts/python docs/make_architecture.py
"""

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch

plt.rcParams["font.family"] = "DejaVu Sans"

# Фирменные цвета (логобук Контур Университет, страница 10)
BLUE = "#1E3CD4"
RED = "#FC0F3C"
DARK = "#25282B"


def mix_with_white(color, t):
    """Светлый оттенок фирменного цвета: примесь белого в доле t."""
    r, g, b = (int(color[i:i + 2], 16) for i in (1, 3, 5))
    lighten = lambda c: round(c + (255 - c) * t)
    return "#{:02X}{:02X}{:02X}".format(lighten(r), lighten(g), lighten(b))


BLUE_T1 = mix_with_white(BLUE, 0.93)   # очень светлый синий: заливка рядовых блоков
BLUE_T2 = mix_with_white(BLUE, 0.84)   # светлый синий: заливка ETL и хранилищ признаков
RED_T1 = mix_with_white(RED, 0.91)     # светлый красный: акцент потоковой обработки
DARK_T1 = mix_with_white(DARK, 0.93)   # светлый серый: мониторинг и контейнер PostgreSQL
SUB = mix_with_white(DARK, 0.40)       # серый подписей под заголовками

FIG_W, FIG_H = 16.0, 12.8
fig, ax = plt.subplots(figsize=(FIG_W, FIG_H))
ax.set_xlim(0, 16)
ax.set_ylim(0, 12.8)
ax.axis("off")


def box(x, y, w, h, title, sub="", fc="#ffffff", ec=BLUE, title_color=DARK):
    ax.add_patch(
        FancyBboxPatch(
            (x, y), w, h,
            boxstyle="round,pad=0.02,rounding_size=0.12",
            linewidth=1.2, edgecolor=ec, facecolor=fc,
        )
    )
    ax.text(x + w / 2, y + h / 2 + (0.16 if sub else 0), title,
            ha="center", va="center", fontsize=11, fontweight="bold", color=title_color)
    if sub:
        ax.text(x + w / 2, y + h / 2 - 0.22, sub,
                ha="center", va="center", fontsize=8.2, color=SUB)


def arrow(p1, p2, style="-", color=DARK, label=None, lx=None, ly=None, fs=8.0, rot=0):
    ax.add_patch(FancyArrowPatch(p1, p2, arrowstyle="-|>", mutation_scale=14,
                                 linestyle=style, color=color,
                                 linewidth=1.4, shrinkA=0, shrinkB=0))
    if label:
        ax.text(lx if lx is not None else (p1[0] + p2[0]) / 2,
                ly if ly is not None else (p1[1] + p2[1]) / 2,
                label, fontsize=fs, color=DARK, ha="center", va="center", rotation=rot,
                bbox=dict(boxstyle="round,pad=0.15", fc="white", ec="none", alpha=0.9))


def polyline(points, style="-", color=DARK, label=None, lx=None, ly=None, fs=8.0, rot=0):
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    ax.plot(xs[:-1], ys[:-1], linestyle=style, color=color, linewidth=1.4, zorder=1)
    ax.add_patch(FancyArrowPatch(points[-2], points[-1], arrowstyle="-|>", mutation_scale=14,
                                 linestyle=style, color=color,
                                 linewidth=1.4, shrinkA=0, shrinkB=0))
    if label:
        ax.text(lx, ly, label, fontsize=fs, color=DARK, ha="center", va="center",
                rotation=rot,
                bbox=dict(boxstyle="round,pad=0.15", fc="white", ec="none", alpha=0.9))


# Заголовок и подсказка по стилям линий
ax.text(8, 12.3, "Архитектура сервиса раннего предупреждения об академическом риске студентов",
        ha="center", va="center", fontsize=14, fontweight="bold", color=BLUE)
ax.text(8, 0.45, "сплошные стрелки: потоки данных; пунктир: события, возврат результата, телеметрия",
        ha="center", va="center", fontsize=9, color=SUB)

# Ряд 1: источники данных
box(0.6, 10.7, 4.2, 0.9, "WebScraping", "открытые данные о направлениях подготовки", BLUE_T1)
box(5.9, 10.7, 4.2, 0.9, "Открытые датасеты", "UCI dropout, OULAD", BLUE_T1)
box(10.6, 10.7, 4.8, 0.9, "1С:Университет", "HTTP-сервисы витрин: контингент,\nуспеваемость, приказы, оплаты", BLUE_T1)

# Ряд 2: ETL
box(5.0, 9.4, 6.0, 0.8, "ETL-конвейер", "файлы → raw, очистка → cleaned, признаки → features (срез на дату)", BLUE_T2)

# Ряд 3: PostgreSQL со слоями данных
ax.add_patch(FancyBboxPatch((0.6, 7.3), 14.8, 1.7,
                            boxstyle="round,pad=0.02,rounding_size=0.12",
                            linewidth=1.6, edgecolor=BLUE, facecolor=DARK_T1))
ax.text(8, 8.62, "PostgreSQL: логические слои данных", ha="center", va="center",
        fontsize=10.5, fontweight="bold", color=BLUE)
box(0.9, 7.42, 2.9, 0.95, "raw", "исходные данные", "#ffffff")
box(4.1, 7.42, 2.9, 0.95, "cleaned", "очищенные данные", "#ffffff")
box(7.3, 7.42, 2.9, 0.95, "features", "признаки для ML", "#ffffff")
box(10.5, 7.42, 4.6, 0.95, "enriched", "обогащённые данные", "#ffffff")

# Ряд 4: хранилища признаков
box(2.6, 5.8, 5.4, 1.0, "Offline-хранилище признаков", "PostgreSQL: схемы признаков, SQL-функции исторических срезов", BLUE_T2)
box(9.2, 5.8, 5.0, 1.0, "Online-хранилище признаков", "Redis: свежие значения, чтение из API", BLUE_T2)

# Ряд 5: потоковая обработка
box(0.6, 4.2, 3.6, 0.9, "Producer", "события успеваемости и оплат", RED_T1, RED)
box(5.0, 4.2, 3.2, 0.9, "Kafka", "топики событий", RED_T1, RED)
box(9.6, 4.2, 4.0, 0.9, "Consumer group", "2 экземпляра; смещение после записи", RED_T1, RED)

# Ряд 6: ML и API
box(0.6, 2.7, 4.2, 1.0, "Обучение и эксперименты", "ноутбуки, Optuna, кросс-валидация по когортам", BLUE_T1)
box(5.2, 2.7, 3.2, 1.0, "Артефакт модели", "joblib + загрузчик с проверкой", BLUE_T1)
box(8.8, 2.7, 5.2, 1.0, "REST API (FastAPI)", "скоринг и объяснение; пакетный пересчёт контингента", BLUE_T1)

# Ряд 7: интерфейс и мониторинг
box(0.6, 1.2, 6.2, 0.9, "Интерфейс (Streamlit)", "список риска, карточка студента, сводные показатели", BLUE_T1)
box(8.0, 1.2, 7.4, 0.9, "Мониторинг (Prometheus + Grafana)", "время ответа, доля ошибок, дрейф распределений (PSI), отложенная оценка качества", DARK_T1, DARK)

# Потоки данных
arrow((2.7, 10.7), (6.3, 10.2))
arrow((8.0, 10.7), (8.0, 10.2))
arrow((13.3, 10.7), (9.7, 10.2))
arrow((8.0, 9.4), (8.0, 9.0))
arrow((3.8, 7.9), (4.1, 7.9), color=BLUE)
arrow((7.0, 7.9), (7.3, 7.9), color=BLUE)
arrow((10.2, 7.9), (10.5, 7.9), color=BLUE)
arrow((8.75, 7.42), (5.3, 6.8), label="признаки", lx=7.0, ly=7.08)
arrow((8.0, 6.3), (9.2, 6.3), label="материализация", lx=8.6, ly=6.52, fs=6.5)
arrow((2.0, 7.3), (2.4, 5.1), style="--", color=RED, label="события для потока", lx=1.5, ly=6.2, rot=90)
arrow((4.2, 4.65), (5.0, 4.65))
arrow((8.2, 4.65), (9.6, 4.65))
arrow((11.6, 5.1), (11.6, 5.8), label="обновление признаков", lx=13.0, ly=5.45)
arrow((13.9, 5.8), (13.9, 3.7), label="online-признаки", lx=14.25, ly=4.7, rot=90, fs=7.5)
arrow((4.8, 3.2), (5.2, 3.2))
arrow((8.4, 3.2), (8.8, 3.2))
arrow((11.4, 2.7), (5.0, 2.1), label="списки риска", lx=8.3, ly=2.42)
arrow((13.0, 2.7), (13.0, 2.1), style="--", label="метрики", lx=13.9, ly=2.4)

# Возврат оценок в 1С
polyline([(14.0, 3.15), (15.6, 3.15), (15.6, 11.15), (15.4, 11.15)], style="--", color=RED,
         label="оценки риска в 1С (идемпотентный приём)", lx=15.82, ly=7.0, rot=90)

fig.savefig("docs/architecture.png", dpi=150, facecolor="white")
print("saved docs/architecture.png")
