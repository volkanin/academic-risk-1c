"""Общее подключение к PostgreSQL проекта из .env в корне репозитория.

connect() даёт сырой psycopg2-коннект (нужен для COPY в ETL),
engine() даёт пул SQLAlchemy (для pandas.read_sql в ноутбуках).
"""

from __future__ import annotations

import os
from pathlib import Path

import psycopg2
from dotenv import load_dotenv
from sqlalchemy import create_engine

ROOT = Path(__file__).resolve().parents[2]


def connect():
    load_dotenv(ROOT / ".env")
    return psycopg2.connect(
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=os.getenv("POSTGRES_PORT", "5432"),
        dbname=os.getenv("POSTGRES_DB", "academic_risk"),
        user=os.getenv("POSTGRES_USER"),
        password=os.getenv("POSTGRES_PASSWORD"),
    )


def engine():
    load_dotenv(ROOT / ".env")
    url = (
        f"postgresql+psycopg2://{os.getenv('POSTGRES_USER')}:{os.getenv('POSTGRES_PASSWORD')}"
        f"@{os.getenv('POSTGRES_HOST', 'localhost')}:{os.getenv('POSTGRES_PORT', '5432')}"
        f"/{os.getenv('POSTGRES_DB', 'academic_risk')}"
    )
    return create_engine(url)
