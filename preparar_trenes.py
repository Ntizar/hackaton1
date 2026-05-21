"""
preparar_trenes.py — Convierte resumen_trayectos_final.csv al formato que necesita main.py

Uso:
    python preparar_trenes.py
    python preparar_trenes.py --mes 2026-06
    python preparar_trenes.py --entrada otro.csv --salida trenes.csv
"""

import argparse
from datetime import date, timedelta

import pandas as pd


# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------

def _parsear_rango_fechas(texto: str):
    """'20260521 - 20260804' → (date(2026,5,21), date(2026,8,4))"""
    partes = texto.strip().split(" - ")
    inicio = pd.to_datetime(partes[0].strip(), format="%Y%m%d").date()
    fin    = pd.to_datetime(partes[1].strip(), format="%Y%m%d").date()
    return inicio, fin


def _parsear_hora(texto: str) -> float:
    """'8:30:00' → 8.5   |   '23:45:00' → 23.75"""
    h, m, _ = texto.strip().split(":")
    return int(h) + int(m) / 60


def _parsear_duracion(texto: str) -> float:
    """'0 days 05:08:00' → 5.133"""
    # formato pandas timedelta string
    td = pd.to_timedelta(texto.strip())
    return round(td.total_seconds() / 3600, 3)


def _parsear_consumo_mwh(texto: str) -> float:
    """'14784.0 kWh' → 14.784"""
    valor = float(str(texto).replace("kWh", "").replace(",", ".").strip())
    return round(valor / 1000, 3)


def _simplificar_estacion(nombre: str) -> str:
    """Acorta nombres de estacion para que el corredor sea legible."""
    reemplazos = {
        "Madrid-Chamartín-Clara Campoamor": "Madrid",
        "Madrid-Chamartín-Clara Campoamor": "Madrid",
        "Bilbao-Intermod. Abando Indalecio Prieto": "Bilbao",
        "Porto Campanha - O Porto Campaña": "Porto",
        "Porto Campanha - O Porto Campaña": "Porto",
        "San Sebastián-Donostia": "San Sebastian",
        "San Sebastián-Donostia": "San Sebastian",
        "Vitoria-Gasteiz": "Vitoria",
        "Vigo-Guixar": "Vigo",
        "Linares-Baeza": "Linares",
    }
    return reemplazos.get(nombre.strip(), nombre.strip())


# ---------------------------------------------------------------------------
# CONVERSION PRINCIPAL
# ---------------------------------------------------------------------------

def preparar(csv_entrada: str, csv_salida: str, mes: str):
    """
    Lee resumen_trayectos_final.csv, expande rangos de fechas y
    genera trenes.csv filtrado por el mes indicado.
    """

    df_raw = pd.read_csv(csv_entrada, encoding="utf-8-sig")
    df_raw.columns = [c.strip() for c in df_raw.columns]

    # Rango del mes objetivo
    mes_inicio = pd.to_datetime(mes + "-01").date()
    mes_fin    = (pd.to_datetime(mes + "-01") + pd.offsets.MonthEnd(0)).date()
    print(f"  Mes objetivo : {mes_inicio} → {mes_fin}")

    filas = []
    contador_id = 1

    for _, fila in df_raw.iterrows():
        try:
            rango_inicio, rango_fin = _parsear_rango_fechas(str(fila.iloc[0]))
        except Exception:
            continue

        # Interseccion con el mes objetivo
        dia_inicio = max(rango_inicio, mes_inicio)
        dia_fin    = min(rango_fin,    mes_fin)

        if dia_inicio > dia_fin:
            continue  # este servicio no opera en el mes objetivo

        try:
            hora_salida  = _parsear_hora(str(fila.iloc[1]))
            duracion_h   = _parsear_duracion(str(fila.iloc[2]))
            origen       = _simplificar_estacion(str(fila.iloc[3]))
            destino      = _simplificar_estacion(str(fila.iloc[4]))
            consumo_mwh  = _parsear_consumo_mwh(str(fila.iloc[5]))
            corredor     = f"{origen}-{destino}"
        except Exception:
            continue

        # Expandir cada dia dentro del rango
        dia = dia_inicio
        while dia <= dia_fin:
            filas.append({
                "tren_id":     f"T{contador_id:05d}",
                "corredor":    corredor,
                "fecha":       str(dia),
                "hora_salida": hora_salida,
                "duracion_h":  duracion_h,
                "consumo_mwh": consumo_mwh,
            })
            contador_id += 1
            dia += timedelta(days=1)

    df = pd.DataFrame(filas)
    df.to_csv(csv_salida, index=False)

    print(f"  Filas generadas : {len(df)}")
    print(f"  Corredores      : {df['corredor'].nunique()}")
    print(f"  Guardado en     : {csv_salida}")
    return df


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Convierte resumen_trayectos_final.csv al formato de main.py"
    )
    parser.add_argument("--entrada", default="resumen_trayectos_final.csv")
    parser.add_argument("--salida",  default="trenes.csv")
    parser.add_argument("--mes",     default="2026-06",
                        help="Mes a extraer en formato YYYY-MM (default: 2026-06)")
    args = parser.parse_args()

    print("=" * 55)
    print("PREPARACION DE DATOS DE TRENES")
    print("=" * 55)
    preparar(args.entrada, args.salida, args.mes)
    print("\nListo. Ejecuta ahora: python main.py --trenes trenes.csv")
