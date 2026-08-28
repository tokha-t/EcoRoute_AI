"""Tiny dependency-free RU/EN translations for the V2 dispatcher screen."""

from __future__ import annotations

TRANSLATIONS = {
    "title": {"ru": "EcoRoute AI — симуляция вывоза", "en": "EcoRoute AI — collection simulation"},
    "banner": {
        "ru": "СИМУЛЯЦИЯ — реальные координаты, синтетическое накопление отходов",
        "en": "SIMULATION — real coordinates, synthetic waste accumulation",
    },
    "subtitle": {
        "ru": "Суточный цикл решений для района Байқоңыр, Астана",
        "en": "Daily decision loop for Baikonur district, Astana",
    },
    "policy": {"ru": "Политика", "en": "Policy"},
    "predictive": {"ru": "A · RED + попутные YELLOW", "en": "A · RED + opportunistic YELLOW"},
    "reds_only": {"ru": "B · только RED (анализ)", "en": "B · RED only (analysis)"},
    "yellow_threshold": {"ru": "Порог YELLOW (%)", "en": "YELLOW threshold (%)"},
    "red_threshold": {"ru": "Порог RED (%)", "en": "RED threshold (%)"},
    "horizon": {"ru": "Горизонт прогноза (дни)", "en": "Planning horizon (days)"},
    "max_interval": {"ru": "Максимальный интервал (дни)", "en": "Maximum interval (days)"},
    "yellow_tolerance": {"ru": "Готовность собирать YELLOW", "en": "YELLOW collection tolerance"},
    "day": {"ru": "День симуляции", "en": "Simulation day"},
    "next_day": {"ru": "Следующий день", "en": "Next day"},
    "run_30": {"ru": "Прогон 30 дней", "en": "Run 30 days"},
    "sites": {"ru": "Площадки", "en": "Sites"},
    "red": {"ru": "RED · обязательно", "en": "RED · mandatory"},
    "yellow_served": {"ru": "YELLOW собрано", "en": "YELLOW served"},
    "distance": {"ru": "Пробег", "en": "Distance"},
    "truck": {"ru": "Машина", "en": "Truck"},
    "stops": {"ru": "остановок", "en": "stops"},
    "load": {"ru": "макс. загрузка", "en": "max load"},
    "duration": {"ru": "длительность", "en": "duration"},
    "dumps": {"ru": "рейсов на полигон", "en": "landfill dumps"},
    "route_source_osrm": {"ru": "маршрут по дорогам OSRM", "en": "OSRM road geometry"},
    "route_source_straight": {
        "ru": "прямые линии — OSRM недоступен",
        "en": "straight lines — OSRM unavailable",
    },
    "world_legend": {
        "ru": "Координаты: {real} реальных OSM, {synthetic} синтезировано на реальных улицах.",
        "en": "Coordinates: {real} real OSM, {synthetic} synthesized on real streets.",
    },
    "infeasible": {"ru": "План невыполним", "en": "Plan is infeasible"},
    "fix": {
        "ru": "Решение: добавьте машину, увеличьте смену/вместимость или снизьте порог.",
        "en": "Fix: add a truck, extend the shift/capacity, or lower the threshold.",
    },
    "comparison": {"ru": "Сравнение политик за 30 дней", "en": "30-day policy comparison"},
    "download_report": {"ru": "Скачать отчёт Markdown", "en": "Download Markdown report"},
    "download_csv": {"ru": "Скачать суточные KPI CSV", "en": "Download daily KPI CSV"},
}


def t(key: str, lang: str = "ru", **values: object) -> str:
    try:
        text = TRANSLATIONS[key][lang]
    except KeyError as exc:
        raise KeyError(f"missing translation {key!r} for {lang!r}") from exc
    return text.format(**values)
