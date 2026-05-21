"""
datos_energia.py - Descarga y normalizacion de datos energeticos ESIOS/REE.

El modulo devuelve una serie horaria lista para el modelo ferroviario:
demanda, eolica, solar, hidraulica, bombeo/baterias, precio, CO2 y porcentaje
renovable calculado de forma trazable.
"""

from __future__ import annotations

import argparse
import os
from datetime import datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd

try:
    import requests

    REQUESTS_OK = True
except ImportError:
    REQUESTS_OK = False


ESIOS_BASE = "https://api.esios.ree.es/indicators"
ESIOS_TOKEN = os.getenv("ESIOS_TOKEN")

# Indicadores verificados contra la API de ESIOS el 2026-05-21.
# Las magnitudes de potencia se tratan como MW medios horarios.
INDICADORES_ESIOS = {
    "demanda_mw": 2037,                # Demanda real nacional
    "eolica_mw": 2038,                 # Generacion T.Real eolica nacional
    "hidraulica_mw": 2042,             # Generacion T.Real hidraulica nacional
    "solar_fv_mw": 2044,               # Generacion T.Real solar fotovoltaica nacional
    "solar_termica_mw": 2045,          # Generacion T.Real solar termica nacional
    "termica_renovable_mw": 2046,      # Generacion T.Real termica renovable nacional
    "bombeo_consumo_mw": 2065,         # Consumo bombeo nacional (suele venir negativo)
    "bombeo_turbinacion_mw": 2066,     # Turbinacion bombeo nacional
    "co2_t_mwh": 10355,                # CO2 asociado generacion T.Real
    "precio_eur_mwh": 600,             # Precio mercado SPOT diario
}

COLUMNAS_BASE = [
    "datetime",
    "fecha",
    "hora",
    "demanda_mw",
    "eolica_mw",
    "solar_fv_mw",
    "solar_termica_mw",
    "hidraulica_mw",
    "termica_renovable_mw",
    "bombeo_carga_mw",
    "bombeo_turbinacion_mw",
    "renovable_mw",
    "renovable_pct",
    "precio_eur_mwh",
    "co2_g_kwh",
    "es_ventana_buena",
    "fuente_energia",
]


def cargar_energia(
    inicio: str | None = None,
    fin: str | None = None,
    mock: bool = False,
) -> pd.DataFrame:
    """
    Devuelve datos energeticos horarios para el rango indicado.

    Si ESIOS no esta disponible, no hay token o el rango no tiene datos,
    devuelve una serie simulada reproducible y marcada como `fuente_energia=mock`.
    """
    inicio = inicio or datetime.today().strftime("%Y-%m-%d")
    fin = fin or inicio

    if mock or not REQUESTS_OK or not ESIOS_TOKEN:
        if not mock:
            print("  ! ESIOS_TOKEN no configurado; usando energia simulada auditable.")
        return _generar_mock(inicio, fin)

    try:
        df_energia = _descargar_esios(inicio, fin)
    except Exception as exc:  # pragma: no cover - defensa ante cambios de API
        print(f"  ! No se pudo completar ESIOS ({exc}); usando energia simulada.")
        return _generar_mock(inicio, fin)

    columnas_esenciales = {"demanda_mw", "eolica_mw", "solar_fv_mw", "hidraulica_mw"}
    if df_energia.empty or not columnas_esenciales.issubset(df_energia.columns):
        print("  ! ESIOS sin datos esenciales; usando energia simulada.")
        return _generar_mock(inicio, fin)

    return df_energia


def _descargar_esios(inicio: str, fin: str) -> pd.DataFrame:
    headers = {
        "Accept": "application/json; application/vnd.esios-api-v1+json",
        "Content-Type": "application/json",
        "x-api-key": ESIOS_TOKEN,
    }
    params = {
        "start_date": f"{inicio}T00:00:00",
        "end_date": f"{fin}T23:59:59",
        "time_trunc": "hour",
        "time_agg": "average",
    }

    series: dict[str, pd.DataFrame] = {}
    for nombre_columna, indicador_id in INDICADORES_ESIOS.items():
        print(f"  -> ESIOS {nombre_columna} ({indicador_id})")
        url = f"{ESIOS_BASE}/{indicador_id}"
        try:
            response = requests.get(url, headers=headers, params=params, timeout=20)
            response.raise_for_status()
        except requests.exceptions.RequestException as exc:
            print(f"     fallo: {exc}")
            continue

        valores = response.json().get("indicator", {}).get("values", [])
        if not valores:
            print("     sin valores en el rango")
            continue

        df_indicador = _normalizar_valores_esios(valores, nombre_columna)
        if not df_indicador.empty:
            series[nombre_columna] = df_indicador

    if not series:
        return pd.DataFrame()

    df_energia = _fusionar_series(series)
    df_energia = _anadir_derivadas(df_energia, fuente="ESIOS")
    df_energia = _filtrar_rango_local(df_energia, inicio, fin)

    print(f"  OK ESIOS: {len(df_energia)} horas normalizadas ({inicio} -> {fin})")
    return df_energia[COLUMNAS_BASE]


def _normalizar_valores_esios(valores: list[dict[str, Any]], columna: str) -> pd.DataFrame:
    df_valores = pd.DataFrame(valores)
    if "value" not in df_valores.columns:
        return pd.DataFrame()

    columna_fecha = "datetime_utc" if "datetime_utc" in df_valores.columns else "datetime"
    df_valores["datetime"] = (
        pd.to_datetime(df_valores[columna_fecha], utc=True, errors="coerce")
        .dt.tz_convert("Europe/Madrid")
        .dt.floor("h")
    )
    df_valores[columna] = pd.to_numeric(df_valores["value"], errors="coerce")
    df_valores = df_valores.dropna(subset=["datetime", columna])
    return df_valores.groupby("datetime", as_index=False)[columna].mean()


def _fusionar_series(series: dict[str, pd.DataFrame]) -> pd.DataFrame:
    dataframes = list(series.values())
    df_energia = dataframes[0]
    for df_siguiente in dataframes[1:]:
        df_energia = pd.merge(df_energia, df_siguiente, on="datetime", how="outer")
    return df_energia.sort_values("datetime").reset_index(drop=True)


def _filtrar_rango_local(df: pd.DataFrame, inicio: str, fin: str) -> pd.DataFrame:
    fecha_inicio = pd.Timestamp(inicio, tz="Europe/Madrid")
    fecha_fin = pd.Timestamp(fin, tz="Europe/Madrid") + pd.Timedelta(days=1)
    return df[(df["datetime"] >= fecha_inicio) & (df["datetime"] < fecha_fin)].copy()


def _anadir_derivadas(df: pd.DataFrame, fuente: str) -> pd.DataFrame:
    df_energia = df.copy()

    for columna in INDICADORES_ESIOS:
        if columna not in df_energia.columns:
            df_energia[columna] = np.nan

    columnas_potencia = [
        "demanda_mw",
        "eolica_mw",
        "hidraulica_mw",
        "solar_fv_mw",
        "solar_termica_mw",
        "termica_renovable_mw",
        "bombeo_consumo_mw",
        "bombeo_turbinacion_mw",
    ]
    for columna in columnas_potencia:
        df_energia[columna] = pd.to_numeric(df_energia[columna], errors="coerce").interpolate().ffill().bfill()

    df_energia["hidraulica_mw"] = df_energia["hidraulica_mw"].clip(lower=0)
    df_energia["bombeo_carga_mw"] = df_energia["bombeo_consumo_mw"].clip(upper=0).abs()
    df_energia["bombeo_turbinacion_mw"] = df_energia["bombeo_turbinacion_mw"].clip(lower=0)

    df_energia["renovable_mw"] = (
        df_energia["eolica_mw"].clip(lower=0)
        + df_energia["solar_fv_mw"].clip(lower=0)
        + df_energia["solar_termica_mw"].clip(lower=0)
        + df_energia["hidraulica_mw"].clip(lower=0)
        + df_energia["termica_renovable_mw"].clip(lower=0)
        + df_energia["bombeo_turbinacion_mw"].clip(lower=0)
    )
    demanda_segura = df_energia["demanda_mw"].replace(0, np.nan)
    df_energia["renovable_pct"] = (100 * df_energia["renovable_mw"] / demanda_segura).clip(0, 100)

    if "co2_t_mwh" in df_energia.columns and not df_energia["co2_t_mwh"].isna().all():
        df_energia["co2_g_kwh"] = (df_energia["co2_t_mwh"] * 1000).clip(lower=0)
    else:
        df_energia["co2_g_kwh"] = (300 - 2.8 * df_energia["renovable_pct"]).clip(lower=10)

    df_energia["precio_eur_mwh"] = pd.to_numeric(df_energia["precio_eur_mwh"], errors="coerce")
    df_energia["precio_eur_mwh"] = df_energia["precio_eur_mwh"].interpolate().ffill().bfill()

    precio_umbral = df_energia["precio_eur_mwh"].quantile(0.35)
    df_energia["es_ventana_buena"] = (df_energia["renovable_pct"] >= 65) & (
        df_energia["precio_eur_mwh"] <= precio_umbral
    )
    df_energia["fecha"] = df_energia["datetime"].dt.date.astype(str)
    df_energia["hora"] = df_energia["datetime"].dt.hour
    df_energia["fuente_energia"] = fuente

    return df_energia


def _generar_mock(inicio: str, fin: str) -> pd.DataFrame:
    start = datetime.strptime(inicio, "%Y-%m-%d")
    end = datetime.strptime(fin, "%Y-%m-%d") + timedelta(days=1)
    horas = pd.date_range(start, end, freq="h", inclusive="left", tz="Europe/Madrid")
    semilla = int(start.strftime("%Y%m%d")) + int((end - timedelta(days=1)).strftime("%Y%m%d"))
    generador = np.random.default_rng(semilla)

    filas: list[dict[str, float | str | pd.Timestamp]] = []
    for instante in horas:
        hora = instante.hour
        solar_curve = max(0.0, np.sin(((hora - 6) / 12) * np.pi))
        demanda = 24500 + 5500 * np.sin(((hora - 7) / 24) * 2 * np.pi) ** 2
        demanda += 2600 if 18 <= hora <= 21 else 0
        eolica = 5200 + (2600 if hora >= 22 or hora <= 6 else 900) + generador.normal(0, 280)
        solar_fv = 23500 * solar_curve + generador.normal(0, 420)
        solar_termica = 1500 * max(0.0, np.sin(((hora - 8) / 11) * np.pi))
        hidraulica = 5200 + 2300 * np.sin(((hora - 5) / 24) * 2 * np.pi) ** 2
        termica_renovable = 380 + generador.normal(0, 25)
        bombeo_carga = 1500 if hora <= 6 or hora >= 23 else 250
        bombeo_turbinacion = 1700 if 19 <= hora <= 22 else 150
        precio = 88 - solar_curve * 38 - (18 if hora <= 6 else 0) + generador.normal(0, 3)

        renovable = max(eolica, 0) + max(solar_fv, 0) + solar_termica + hidraulica + termica_renovable + bombeo_turbinacion
        renovable_pct = min(100, max(0, 100 * renovable / max(demanda, 1)))
        co2 = max(18, 285 - 2.55 * renovable_pct + generador.normal(0, 4))

        filas.append(
            {
                "datetime": instante,
                "demanda_mw": round(demanda, 2),
                "eolica_mw": round(max(eolica, 0), 2),
                "hidraulica_mw": round(max(hidraulica, 0), 2),
                "solar_fv_mw": round(max(solar_fv, 0), 2),
                "solar_termica_mw": round(max(solar_termica, 0), 2),
                "termica_renovable_mw": round(max(termica_renovable, 0), 2),
                "bombeo_consumo_mw": -round(bombeo_carga, 2),
                "bombeo_turbinacion_mw": round(bombeo_turbinacion, 2),
                "precio_eur_mwh": round(max(precio, 0), 2),
                "co2_t_mwh": round(co2 / 1000, 5),
            }
        )

    df_mock = pd.DataFrame(filas)
    df_mock = _anadir_derivadas(df_mock, fuente="mock")
    print(f"  OK mock energia: {len(df_mock)} horas simuladas ({inicio} -> {fin})")
    return df_mock[COLUMNAS_BASE]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Descarga/normaliza datos energeticos horarios ESIOS")
    parser.add_argument("--inicio", "-i", default=None, help="Fecha de inicio YYYY-MM-DD")
    parser.add_argument("--fin", "-f", default=None, help="Fecha de fin YYYY-MM-DD")
    parser.add_argument("--mock", "-m", action="store_true", help="Usar datos simulados reproducibles")
    parser.add_argument("--csv", default=None, help="Guardar resultado en CSV")
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    print("=" * 64)
    print("DATOS ENERGETICOS - ESIOS / REE")
    print("=" * 64)
    df = cargar_energia(inicio=args.inicio, fin=args.fin, mock=args.mock)
    print(df.to_string(index=False))
    print()
    print(f"Horas buenas: {int(df['es_ventana_buena'].sum())} de {len(df)}")
    if args.csv:
        df.to_csv(args.csv, index=False)
        print(f"Guardado en {args.csv}")