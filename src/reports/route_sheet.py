"""Printable bilingual route sheets for the V2 dispatcher plan."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from html import escape

import pandas as pd

from src.optimize.solver import Plan


@dataclass(frozen=True)
class RouteSheet:
    rows: pd.DataFrame
    html: str

    def to_csv(self) -> bytes:
        return self.rows.to_csv(index=False).encode("utf-8-sig")


def _site_lookup(sites_df: pd.DataFrame) -> pd.DataFrame:
    key = "site_id" if "site_id" in sites_df.columns else "bin_id"
    return sites_df.assign(_route_sheet_id=sites_df[key].astype(str)).set_index("_route_sheet_id")


def build_route_sheet(
    plan: Plan,
    sites_df: pd.DataFrame,
    lang: str = "ru",
    *,
    service_date: date | None = None,
    truck_id: str | None = None,
    simulation_day: int | None = None,
) -> RouteSheet:
    """Build ordered truck stops, skipped-YELLOW explanations, CSV, and printable HTML."""
    if lang not in {"ru", "en"}:
        raise ValueError("lang must be 'ru' or 'en'")
    service_date = service_date or date.today()
    by_id = _site_lookup(sites_df)
    labels = {
        "depot_start": "Депо — выезд" if lang == "ru" else "Depot — start",
        "depot_return": "Депо — возврат" if lang == "ru" else "Depot — return",
        "landfill": "Полигон — разгрузка" if lang == "ru" else "Landfill — unload",
        "planned": "в маршруте" if lang == "ru" else "planned",
        "skipped": "пропущен" if lang == "ru" else "skipped",
        "manual_exclude": "исключён диспетчером" if lang == "ru" else "dispatcher excluded",
        "manual_include": "включён диспетчером" if lang == "ru" else "dispatcher included",
    }
    rows: list[dict] = []
    shift_start = datetime.combine(service_date, time(hour=8))
    routes = [route for route in plan.routes if truck_id is None or route.truck_id == truck_id]
    for route in routes:
        cumulative = route.cumulative_distance_m
        cumulative_duration = route.cumulative_duration_s
        cumulative_load = route.cumulative_load_kg
        rows.append(
            {
                "date": service_date.isoformat(),
                "simulation_day": simulation_day if simulation_day is not None else "",
                "truck_id": route.truck_id,
                "sequence": 0,
                "status": labels["planned"],
                "stop_type": "DEPOT",
                "site_id": "",
                "address": labels["depot_start"],
                "containers": "",
                "fill_pct": "",
                "klass": "",
                "reason": "",
                "manual_override": "",
                "eta": shift_start.strftime("%H:%M"),
                "leg_km": 0.0,
                "cumulative_km": 0.0,
                "cumulative_load_kg": 0.0,
            }
        )
        for sequence, stop in enumerate(route.ordered_stops, start=1):
            if stop == "LANDFILL":
                values = {
                    "stop_type": "LANDFILL",
                    "site_id": "",
                    "address": labels["landfill"],
                    "containers": "",
                    "fill_pct": "",
                    "klass": "",
                    "reason": "",
                    "manual_override": "",
                }
            else:
                site = by_id.loc[str(stop)]
                values = {
                    "stop_type": "SITE",
                    "site_id": str(stop),
                    "address": str(site.get("address", "")),
                    "containers": site.get("containers", ""),
                    "fill_pct": round(float(site.get("fill_pct", 0.0)), 1),
                    "klass": str(site.get("klass", site.get("priority", ""))),
                    "reason": str(site.get("reason", "")),
                    "manual_override": str(site.get("manual_override", "")),
                }
            elapsed = cumulative_duration[sequence] if len(cumulative_duration) > sequence else 0.0
            cumulative_km = cumulative[sequence] if len(cumulative) > sequence else 0.0
            previous_km = cumulative[sequence - 1] if len(cumulative) >= sequence else 0.0
            rows.append(
                {
                    "date": service_date.isoformat(),
                    "simulation_day": simulation_day if simulation_day is not None else "",
                    "truck_id": route.truck_id,
                    "sequence": sequence,
                    "status": labels["planned"],
                    **values,
                    "eta": (shift_start + timedelta(seconds=elapsed)).strftime("%H:%M"),
                    "leg_km": round((cumulative_km - previous_km) / 1000, 2),
                    "cumulative_km": round(cumulative_km / 1000, 2),
                    "cumulative_load_kg": (
                        round(cumulative_load[sequence], 1) if len(cumulative_load) > sequence else ""
                    ),
                }
            )
        return_sequence = len(route.ordered_stops) + 1
        final_elapsed = cumulative_duration[-1] if cumulative_duration else route.duration_s
        final_km = cumulative[-1] if cumulative else route.distance_m
        prior_km = cumulative[-2] if len(cumulative) > 1 else 0.0
        rows.append(
            {
                "date": service_date.isoformat(),
                "simulation_day": simulation_day if simulation_day is not None else "",
                "truck_id": route.truck_id,
                "sequence": return_sequence,
                "status": labels["planned"],
                "stop_type": "DEPOT",
                "site_id": "",
                "address": labels["depot_return"],
                "containers": "",
                "fill_pct": "",
                "klass": "",
                "reason": "",
                "manual_override": "",
                "eta": (shift_start + timedelta(seconds=final_elapsed)).strftime("%H:%M"),
                "leg_km": round((final_km - prior_km) / 1000, 2),
                "cumulative_km": round(final_km / 1000, 2),
                "cumulative_load_kg": round(cumulative_load[-1], 1) if cumulative_load else 0.0,
            }
        )
    for decision in plan.skipped_yellow:
        site = by_id.loc[decision.site_id]
        rows.append(
            {
                "date": service_date.isoformat(),
                "simulation_day": simulation_day if simulation_day is not None else "",
                "truck_id": "",
                "sequence": "",
                "status": labels["skipped"],
                "stop_type": "SITE",
                "site_id": decision.site_id,
                "address": str(site.get("address", "")),
                "containers": site.get("containers", ""),
                "fill_pct": round(float(site.get("fill_pct", 0.0)), 1),
                "klass": "YELLOW",
                "reason": decision.explanation_ru,
                "manual_override": str(site.get("manual_override", "")),
                "eta": "",
                "leg_km": "",
                "cumulative_km": "",
                "cumulative_load_kg": "",
            }
        )
    represented = {str(row["site_id"]) for row in rows if row["site_id"]}
    for site_id, action in plan.manual_overrides.items():
        if site_id in represented:
            continue
        site = by_id.loc[site_id]
        rows.append(
            {
                "date": service_date.isoformat(),
                "simulation_day": simulation_day if simulation_day is not None else "",
                "truck_id": "",
                "sequence": "",
                "status": labels[f"manual_{action}"],
                "stop_type": "SITE",
                "site_id": site_id,
                "address": str(site.get("address", "")),
                "containers": site.get("containers", ""),
                "fill_pct": round(float(site.get("fill_pct", 0.0)), 1),
                "klass": str(site.get("klass", "")),
                "reason": f"manual_{action}",
                "manual_override": action,
                "eta": "",
                "leg_km": "",
                "cumulative_km": "",
                "cumulative_load_kg": "",
            }
        )
    frame = pd.DataFrame(rows)
    for column in (
        "simulation_day",
        "sequence",
        "containers",
        "fill_pct",
        "leg_km",
        "cumulative_km",
        "cumulative_load_kg",
    ):
        if column in frame:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    title = "Маршрутный лист" if lang == "ru" else "Route sheet"
    signatures = (
        "Водитель: ____________________ &nbsp;&nbsp; Диспетчер: ____________________"
        if lang == "ru"
        else "Driver: ____________________ &nbsp;&nbsp; Dispatcher: ____________________"
    )
    column_labels = (
        {
            "date": "Дата",
            "simulation_day": "День",
            "truck_id": "Машина",
            "sequence": "Порядок",
            "status": "Статус",
            "stop_type": "Тип остановки",
            "site_id": "Площадка",
            "address": "Адрес",
            "containers": "Контейнеры",
            "fill_pct": "Заполнение, %",
            "klass": "Класс",
            "reason": "Причина",
            "manual_override": "Ручная правка",
            "eta": "ETA",
            "leg_km": "Участок, км",
            "cumulative_km": "Пробег, км",
            "cumulative_load_kg": "Загрузка, кг",
        }
        if lang == "ru"
        else {}
    )
    printable_frame = frame.rename(columns=column_labels)
    html = (
        "<!doctype html><html><head><meta charset='utf-8'><title>"
        + escape(title)
        + "</title><style>body{font-family:Arial,sans-serif;margin:24px}"
        "table{border-collapse:collapse;width:100%;font-size:12px}"
        "th,td{border:1px solid #bbb;padding:5px;text-align:left}"
        "h1{font-size:22px}.signatures{margin-top:32px}</style></head><body>"
        f"<h1>{escape(title)} — {service_date.isoformat()}</h1>"
        + printable_frame.to_html(index=False, escape=True)
        + f"<div class='signatures'>{signatures}</div></body></html>"
    )
    return RouteSheet(frame, html)
