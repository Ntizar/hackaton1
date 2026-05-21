"""Carga GTFS Renfe y genera circulaciones mensuales con consumo estimado."""

from __future__ import annotations

import argparse
import math
import re
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path(__file__).resolve().parent
GTFS_ZIP = ROOT / "avegtfs.zip"
DATE_SUFFIX_RE = re.compile(r"(\d{4}-\d{2}-\d{2})$")


@dataclass(frozen=True)
class FactorConsumo:
    tipo: str
    kwh_km_bruto: float
    regeneracion_pct: float
    potencia_kw_ref: float


FACTORES_CONSUMO: dict[str, FactorConsumo] = {
    "AVE": FactorConsumo("Alta velocidad", 5.35, 28.0, 8800),
    "AVLO": FactorConsumo("Alta velocidad bajo coste", 5.05, 28.0, 8200),
    "ALVIA": FactorConsumo("Larga distancia ancho variable", 5.65, 25.0, 6000),
    "EUROMED": FactorConsumo("Larga distancia", 5.25, 25.0, 7000),
    "INTERCITY": FactorConsumo("Larga distancia", 4.85, 23.0, 5200),
    "AVANT": FactorConsumo("Media distancia alta velocidad", 4.65, 30.0, 5200),
    "MD": FactorConsumo("Media distancia", 3.45, 32.0, 3000),
    "REGIONAL": FactorConsumo("Regional", 3.05, 34.0, 2500),
    "CERCANIAS": FactorConsumo("Cercanias", 5.35, 36.0, 2400),
    "DEFAULT": FactorConsumo("Servicio electrico Renfe", 4.65, 28.0, 4200),
}


def cargar_trenes_gtfs(
    gtfs_zip: str | Path = GTFS_ZIP,
    mes: str = "2026-06",
    max_trenes: int | None = None,
) -> pd.DataFrame:
    """Convierte GTFS en un DataFrame mensual compatible con el optimizador."""
    ruta_gtfs = Path(gtfs_zip)
    mes_inicio, mes_fin = _rango_mes(mes)

    routes = _read_gtfs_csv(ruta_gtfs, "routes.txt")
    stops = _read_gtfs_csv(ruta_gtfs, "stops.txt")
    trips = _read_gtfs_csv(ruta_gtfs, "trips.txt")
    stop_times = _read_gtfs_csv(ruta_gtfs, "stop_times.txt")

    trips["fecha"] = trips["trip_id"].map(_fecha_desde_trip_id)
    trips = trips[(trips["fecha"] >= str(mes_inicio)) & (trips["fecha"] <= str(mes_fin))].copy()
    if max_trenes:
        trips = trips.head(max_trenes).copy()
    if trips.empty:
        return pd.DataFrame()

    routes_by_id = routes.set_index("route_id").to_dict("index")
    stops_by_id = stops.set_index("stop_id").to_dict("index")
    trip_ids = set(trips["trip_id"])
    stop_times = stop_times[stop_times["trip_id"].isin(trip_ids)].copy()

    filas: list[dict[str, Any]] = []
    trips_by_id = trips.set_index("trip_id").to_dict("index")
    for trip_id, trip_stops in stop_times.groupby("trip_id", sort=False):
        if len(trip_stops) < 2:
            continue
        trip = trips_by_id.get(trip_id, {})
        route = routes_by_id.get(trip.get("route_id", ""), {})
        fila = _construir_fila_tren(trip_id, trip, route, trip_stops, stops_by_id)
        if fila:
            filas.append(fila)

    df_trenes = pd.DataFrame(filas)
    if df_trenes.empty:
        return df_trenes

    df_trenes = df_trenes.sort_values(["fecha", "hora_salida", "tren_id"]).reset_index(drop=True)
    return df_trenes


def _read_gtfs_csv(gtfs_zip: Path, filename: str) -> pd.DataFrame:
    with zipfile.ZipFile(gtfs_zip) as archive:
        with archive.open(filename) as handle:
            df = pd.read_csv(handle, dtype=str, keep_default_na=False)
    df.columns = [columna.strip() for columna in df.columns]
    for columna in df.columns:
        df[columna] = df[columna].astype(str).str.strip()
    return df


def _rango_mes(mes: str) -> tuple[date, date]:
    inicio = pd.Timestamp(f"{mes}-01").date()
    fin = (pd.Timestamp(f"{mes}-01") + pd.offsets.MonthEnd(0)).date()
    return inicio, fin


def _fecha_desde_trip_id(trip_id: str) -> str:
    match = DATE_SUFFIX_RE.search(trip_id or "")
    return match.group(1) if match else ""


def _construir_fila_tren(
    trip_id: str,
    trip: dict[str, str],
    route: dict[str, str],
    trip_stops: pd.DataFrame,
    stops_by_id: dict[str, dict[str, str]],
) -> dict[str, Any] | None:
    trip_stops = trip_stops.copy()
    trip_stops["stop_sequence_int"] = pd.to_numeric(trip_stops["stop_sequence"], errors="coerce").fillna(0)
    trip_stops = trip_stops.sort_values("stop_sequence_int")

    first_stop_time = trip_stops.iloc[0].to_dict()
    last_stop_time = trip_stops.iloc[-1].to_dict()
    first_stop = stops_by_id.get(first_stop_time.get("stop_id", ""), {})
    last_stop = stops_by_id.get(last_stop_time.get("stop_id", ""), {})
    if not first_stop or not last_stop:
        return None

    salida_segundos = _parse_time_to_seconds(first_stop_time.get("departure_time") or first_stop_time.get("arrival_time"))
    llegada_segundos = _parse_time_to_seconds(last_stop_time.get("arrival_time") or last_stop_time.get("departure_time"))
    if llegada_segundos <= salida_segundos:
        llegada_segundos += 24 * 3600
    duracion_h = max((llegada_segundos - salida_segundos) / 3600, 0.25)

    distancia_km = _distancia_viaje_km(trip_stops, stops_by_id)
    factor = _factor_para_route(route.get("route_short_name", ""))
    consumo_bruto_kwh = distancia_km * factor.kwh_km_bruto
    recuperado_kwh = consumo_bruto_kwh * factor.regeneracion_pct / 100
    consumo_neto_kwh = max(consumo_bruto_kwh - recuperado_kwh, 0)

    origen = first_stop.get("stop_name", first_stop_time.get("stop_id", ""))
    destino = last_stop.get("stop_name", last_stop_time.get("stop_id", ""))
    origen_lat = _parse_float(first_stop.get("stop_lat"))
    origen_lon = _parse_float(first_stop.get("stop_lon"))
    destino_lat = _parse_float(last_stop.get("stop_lat"))
    destino_lon = _parse_float(last_stop.get("stop_lon"))

    fecha = trip.get("fecha") or _fecha_desde_trip_id(trip_id)
    hora_salida = round((salida_segundos % (24 * 3600)) / 3600, 2)
    hora_llegada = round((llegada_segundos % (24 * 3600)) / 3600, 2)
    trip_short_name = trip.get("trip_short_name", "") or trip_id
    route_short_name = route.get("route_short_name", "") or "RENFE"

    return {
        "tren_id": trip_id,
        "trip_id": trip_id,
        "servicio": trip_short_name,
        "linea": route_short_name,
        "tipo_servicio": factor.tipo,
        "corredor": f"{origen}-{destino}",
        "fecha": fecha,
        "hora_salida": hora_salida,
        "hora_llegada": hora_llegada,
        "hora_salida_texto": _seconds_to_hhmm(salida_segundos),
        "hora_llegada_texto": _seconds_to_hhmm(llegada_segundos),
        "duracion_h": round(duracion_h, 3),
        "origen": origen,
        "destino": destino,
        "origen_lat": origen_lat,
        "origen_lon": origen_lon,
        "destino_lat": destino_lat,
        "destino_lon": destino_lon,
        "mid_lat": round((origen_lat + destino_lat) / 2, 6),
        "mid_lon": round((origen_lon + destino_lon) / 2, 6),
        "paradas": int(len(trip_stops)),
        "distancia_km": round(distancia_km, 2),
        "kwh_km_bruto": factor.kwh_km_bruto,
        "regeneracion_pct": factor.regeneracion_pct,
        "potencia_kw_ref": factor.potencia_kw_ref,
        "consumo_bruto_kwh": round(consumo_bruto_kwh, 1),
        "recuperado_kwh": round(recuperado_kwh, 1),
        "consumo_kwh": round(consumo_neto_kwh, 1),
        "consumo_mwh": round(consumo_neto_kwh / 1000, 4),
        "potencia_media_kw": round(consumo_neto_kwh / duracion_h, 1),
    }


def _factor_para_route(route_short_name: str) -> FactorConsumo:
    clave = (route_short_name or "").upper().replace(" ", "")
    for token, factor in FACTORES_CONSUMO.items():
        if token != "DEFAULT" and token in clave:
            return factor
    return FACTORES_CONSUMO["DEFAULT"]


def _distancia_viaje_km(trip_stops: pd.DataFrame, stops_by_id: dict[str, dict[str, str]]) -> float:
    shape_values = pd.to_numeric(trip_stops.get("shape_dist_traveled", pd.Series(dtype=str)), errors="coerce").dropna()
    if len(shape_values) >= 2:
        distancia = float(shape_values.max() - shape_values.min())
        return distancia / 1000 if distancia > 10000 else distancia

    total_km = 0.0
    previous_stop: dict[str, str] | None = None
    for stop_time in trip_stops.to_dict("records"):
        current_stop = stops_by_id.get(stop_time.get("stop_id", ""))
        if not current_stop:
            continue
        if previous_stop:
            total_km += _haversine_km(
                _parse_float(previous_stop.get("stop_lat")),
                _parse_float(previous_stop.get("stop_lon")),
                _parse_float(current_stop.get("stop_lat")),
                _parse_float(current_stop.get("stop_lon")),
            )
        previous_stop = current_stop
    return max(total_km * 1.15, 0.1)


def _parse_time_to_seconds(value: str | None) -> int:
    parts = [int(part) for part in (value or "0:0:0").split(":")]
    if len(parts) == 2:
        hours, minutes = parts
        seconds = 0
    else:
        hours, minutes, seconds = parts[:3]
    return hours * 3600 + minutes * 60 + seconds


def _seconds_to_hhmm(total_seconds: int) -> str:
    seconds_day = total_seconds % (24 * 3600)
    hours = seconds_day // 3600
    minutes = (seconds_day % 3600) // 60
    return f"{hours:02d}:{minutes:02d}"


def _parse_float(value: str | None) -> float:
    try:
        return float(str(value or "0").replace(",", "."))
    except ValueError:
        return 0.0


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius_km = 6371.0088
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    haversine = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * radius_km * math.asin(math.sqrt(haversine))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Convierte GTFS Renfe en trenes mensuales con consumo estimado")
    parser.add_argument("--gtfs", type=Path, default=GTFS_ZIP, help="ZIP GTFS")
    parser.add_argument("--mes", default="2026-06", help="Mes YYYY-MM")
    parser.add_argument("--salida", type=Path, default=ROOT / "trenes_gtfs_mes.csv", help="CSV de salida")
    parser.add_argument("--max-trenes", type=int, default=None, help="Limita trenes para pruebas")
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    df = cargar_trenes_gtfs(args.gtfs, args.mes, args.max_trenes)
    df.to_csv(args.salida, index=False)
    print(f"Trenes generados: {len(df)}")
    print(f"Salida: {args.salida}")