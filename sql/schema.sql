-- Схема базы данных проекта. Заполняется в фазе 1 (блок 2 требований).
-- Пока созданы только логические схемы слоёв данных.

CREATE SCHEMA IF NOT EXISTS raw;       -- исходные данные, как в файлах датасетов
CREATE SCHEMA IF NOT EXISTS cleaned;   -- очищенные данные
CREATE SCHEMA IF NOT EXISTS features;  -- признаки для ML
CREATE SCHEMA IF NOT EXISTS enriched;  -- обогащённые данные (веб-скрейпинг, контекст направлений)
