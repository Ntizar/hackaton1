"""
ver_resultados.py — Muestra los resultados de optimizacion para un dia concreto

Uso:
    python ver_resultados.py --dia 2026-08-01
    python ver_resultados.py --dia 2026-08-01 --csv resultados.csv
"""

import argparse
import pandas as pd


def ver_dia(csv_path: str, dia: str):

    df = pd.read_csv(csv_path)

    if "fecha" not in df.columns:
        print("El CSV no tiene columna 'fecha'.")
        return

    df_dia = df[df["fecha"] == dia]

    if df_dia.empty:
        print(f"No hay resultados para {dia}.")
        print(f"Fechas disponibles: {sorted(df['fecha'].unique())[:10]} ...")
        return

    n = len(df_dia)
    desplazados = df_dia[df_dia["desplazamiento_h"].abs() > 0.01]

    # --- KPIs globales del día ---
    print("=" * 65)
    print(f"RESULTADOS — {dia}  ({n} trenes)")
    print("=" * 65)

    co2_total   = df_dia["ahorro_co2_kg"].sum()
    eur_total   = df_dia["ahorro_eur"].sum()
    renov_antes = df_dia["renovable_pct_actual"].mean()
    renov_desp  = df_dia["renovable_pct_optima"].mean()
    mejora_renov = df_dia["mejora_renovable_pct"].mean()

    print(f"\n  Trenes desplazados     : {len(desplazados)} / {n}")
    print(f"  CO2 ahorrado           : {co2_total:,.0f} kg")
    print(f"  Ahorro economico       : {eur_total:,.0f} EUR")
    print(f"  Renovable antes        : {renov_antes:.1f}%")
    print(f"  Renovable despues      : {renov_desp:.1f}%")
    print(f"  Mejora renovable media : +{mejora_renov:.1f}%")

    # --- Detalle de desplazamientos ---
    desp_medio = desplazados["desplazamiento_h"].abs().mean() if len(desplazados) else 0
    print(f"  Desplazamiento medio   : {desp_medio:.2f} h ({desp_medio*60:.0f} min)")

    # --- Corredores más beneficiados ---
    print(f"\n{'─'*65}")
    print("AHORRO POR CORREDOR")
    print(f"{'─'*65}")
    por_corredor = df_dia.groupby("corredor").agg(
        trenes=("tren_id", "count"),
        co2_kg=("ahorro_co2_kg", "sum"),
        eur=("ahorro_eur", "sum"),
        mejora_renovable=("mejora_renovable_pct", "mean"),
    ).sort_values("co2_kg", ascending=False)
    print(por_corredor.to_string())


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dia",  required=True, help="Fecha YYYY-MM-DD")
    parser.add_argument("--csv",  default="resultados.csv")
    args = parser.parse_args()
    ver_dia(args.csv, args.dia)
