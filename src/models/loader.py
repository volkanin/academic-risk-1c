"""Загрузка финальной модели с проверкой окружения и состава признаков.

Контракт (блок 3.3 требований: код загрузки с проверкой):
1. Вместе с файлом модели хранится model_metadata.json: версии python и
   библиотек, список признаков, параметры обучения, описание выборок.
2. При загрузке версии сверяются с текущим окружением; расхождение
   прерывает загрузку с явным сообщением (модель обучена на других версиях,
   поведение непредсказуемо).
3. Функция predict проверяет состав колонок перед предсказанием и работает
   только с ожидаемым порядком признаков.

Использование:
    from src.models.loader import load_model
    predict, meta = load_model()
    proba = predict(X)  # X: DataFrame с колонками из meta['features']
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import joblib

MODEL_DIR = Path(__file__).resolve().parents[2] / "models"


def load_model(path: str | Path | None = None):
    """Загружает модель и метаданные, возвращает (predict, meta)."""
    model_path = Path(path) if path else MODEL_DIR / "final_model.joblib"
    meta_path = MODEL_DIR / "model_metadata.json"

    model = joblib.load(model_path)
    meta = json.loads(meta_path.read_text(encoding="utf-8"))

    import catboost
    import numpy
    import pandas
    import sklearn

    current = {
        "python": ".".join(str(v) for v in sys.version_info[:3]),
        "scikit-learn": sklearn.__version__,
        "catboost": catboost.__version__,
        "pandas": pandas.__version__,
        "numpy": numpy.__version__,
    }
    mismatch = {
        k: {"в метаданных": v, "в окружении": current.get(k)}
        for k, v in meta["versions"].items()
        if current.get(k) != v
    }
    if mismatch:
        raise RuntimeError(
            "Версии окружения не совпадают с версиями обучения модели: "
            f"{mismatch}. Установите версии из {meta_path.name} перед предсказанием."
        )

    expected = list(meta["features"])

    def predict(X):
        missing = [c for c in expected if c not in X.columns]
        if missing:
            raise ValueError(f"Отсутствуют обязательные признаки: {missing}")
        return model.predict_proba(X[expected])[:, 1]

    return predict, meta
