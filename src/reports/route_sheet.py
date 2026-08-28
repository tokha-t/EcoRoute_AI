"""Printable bilingual route sheets for the V2 dispatcher plan."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
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
    }
    rows: list[dict] = []
    for route in plan.routes:
        cumulative = route.cumulative_distance_m
        rows.append(
            {
                "date": service_date.isoformat(),
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
                "cumulative_km": 0.0,
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
                }
            rows.append(
                {
                    "date": service_date.isoformat(),
                    "truck_id": route.truck_id,
                    "sequence": sequence,
                    "status": labels["planned"],
                    **values,
                    "cumulative_km": (
                        round(cumulative[sequence] / 1000, 2) if len(cumulative) > sequence else ""
                    ),
                }
            )
        return_sequence = len(route.ordered_stops) + 1
        rows.append(
            {
                "date": service_date.isoformat(),
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
                "cumulative_km": (
                    round(cumulative[-1] / 1000, 2) if cumulative else round(route.distance_m / 1000, 2)
                ),
            }
        )
    for decision in plan.skipped_yellow:
        site = by_id.loc[decision.site_id]
        rows.append(
            {
                "date": service_date.isoformat(),
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
                "cumulative_km": "",
            }
        )
    frame = pd.DataFrame(rows)
    title = "Маршрутный лист" if lang == "ru" else "Route sheet"
    signatures = (
        "Водитель: ____________________ &nbsp;&nbsp; Диспетчер: ____________________"
        if lang == "ru"
        else "Driver: ____________________ &nbsp;&nbsp; Dispatcher: ____________________"
    )
    column_labels = (
        {
            "date": "Дата",
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
            "cumulative_km": "Пробег, км",
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
