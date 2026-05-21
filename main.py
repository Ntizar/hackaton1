"""
main.py — Orquestador del optimizador de ventanas horarias
Hackathon MOV.IA by INECO — Reto: Soberania Tecnologica

Conecta datos de energia + trenes + modelo y genera el plan semanal optimizado.

Uso:
    python main.py --trenes trenes.csv
    python main.py --trenes trenes.csv --salida resultados.csv
    python main.py --trenes trenes.csv --mock     # sin API ESIOS
"""

import argparse
from datetime import date, timedelta

import pandas as pd

from datos_energia import cargar_energia
from modelo import optimizar_trenes, resumen



# ---------------------------------------------------------------------------
# CARGA DEL CSV DE TRENES
# ---------------------------------------------------------------------------

def cargar_trenes_csv(path: str) -> pd.DataFrame:
    """
    Carga el CSV de circulaciones de Adif.

    Columnas esperadas:
        tren_id, corredor, fecha (YYYY-MM-DD), hora_salida (float),
        duracion_h (float), consumo_mwh (float)
    """
    df = pd.read_csv(path)
    df["fecha"] = pd.to_datetime(df["fecha"]).dt.date
    df["hora_salida"] = df["hora_salida"].astype(float)
    return df


# ---------------------------------------------------------------------------
# ENERGIA PARA UN DIA CONCRETO
# ---------------------------------------------------------------------------

def _energia_para_dia(dia: date, mock: bool) -> pd.DataFrame:
    """
    Devuelve el perfil energetico para un dia concreto usando como proxy
    el mismo dia de la semana del ano anterior (364 dias = 52 semanas exactas).
    Asi se preserva tanto la estacionalidad (mismo mes) como el patron
    semanal (mismo dia de la semana).
    """
    dia_proxy = dia - timedelta(days=364)
    return cargar_energia(
        inicio=str(dia_proxy),
        fin=str(dia_proxy),
        mock=mock,
    )


# ---------------------------------------------------------------------------
# OPTIMIZACION DE UNA SEMANA
# ---------------------------------------------------------------------------

def optimizar_semana(
    df_trenes: pd.DataFrame,
    semana_inicio: date,
    mock: bool = False,
) -> pd.DataFrame:
    """
    Optimiza todos los trenes de los 7 dias a partir de semana_inicio.
    Devuelve DataFrame con todos los resultados de la semana.
    """
    resultados = []

    for i in range(7):
        dia = semana_inicio + timedelta(days=i)
        df_dia = df_trenes[df_trenes["fecha"] == dia].copy()

        if df_dia.empty:
            continue

        print(f"    {dia} — {len(df_dia)} trenes", end="")

        df_energia_dia = _energia_para_dia(dia, mock=mock)
        df_resultado   = optimizar_trenes(df_energia_dia, df_dia)
        df_resultado.insert(0, "fecha", str(dia))

        resultados.append(df_resultado)

        stats = resumen(df_resultado)
        print(f"  |  CO2: {stats['ahorro_co2_kg_total']:+.0f} kg  "
              f"Ahorro: {stats['ahorro_eur_total']:+.0f} EUR")

    if not resultados:
        return pd.DataFrame()

    return pd.concat(resultados).reset_index(drop=True)


# ---------------------------------------------------------------------------
# FUNCION PRINCIPAL
# ---------------------------------------------------------------------------

def main(csv_trenes: str, csv_salida: str, mock: bool):

    print("=" * 65)
    print("TrEnergIA — OPTIMIZADOR DE VENTANAS HORARIAS")
    print("Hackathon MOV.IA by INECO 2026")
    print("=" * 65)
    print(f"  Trenes  : {csv_trenes}")
    print(f"  Salida  : {csv_salida}")
    print(f"  Energia : ESIOS (mismo dia de semana, ano anterior)")
    print(f"  Modo    : {'simulado' if mock else 'real'}")
    print()

    # Carga el CSV de trenes
    df_trenes = cargar_trenes_csv(csv_trenes)
    fechas    = sorted(df_trenes["fecha"].unique())
    print(f"  {len(df_trenes)} circulaciones cargadas  "
          f"({fechas[0]} → {fechas[-1]})\n")

    # Itera semana a semana
    todos = []
    semana = fechas[0]

    while semana <= fechas[-1]:
        semana_fin = semana + timedelta(days=6)
        print(f"  Semana {semana} → {semana_fin}")

        df_sem = optimizar_semana(df_trenes, semana, mock=mock)

        if not df_sem.empty:
            todos.append(df_sem)
            stats = resumen(df_sem)
            print(f"    -> Semana: CO2 {stats['ahorro_co2_kg_total']:+,.0f} kg  "
                  f"| EUR {stats['ahorro_eur_total']:+,.0f}\n")

        semana = semana + timedelta(weeks=1)

    if not todos:
        print("Sin resultados. Comprueba el CSV de trenes.")
        return

    # Resultado final
    df_final = pd.concat(todos).reset_index(drop=True)
    df_final.to_csv(csv_salida, index=False)

    # Resumen global
    stats = resumen(df_final)
    dias_cubiertos = (fechas[-1] - fechas[0]).days + 1
    factor_anual   = 365 / dias_cubiertos

    print("=" * 65)
    print("RESUMEN GLOBAL")
    print("=" * 65)
    print(f"  Trenes optimizados   : {stats['trenes_optimizados']}")
    print(f"  Trenes desplazados   : {stats['trenes_desplazados']}")
    print(f"  CO2 ahorrado         : {stats['ahorro_co2_kg_total']:,.0f} kg")
    print(f"  Ahorro economico     : {stats['ahorro_eur_total']:,.0f} EUR")
    print(f"  Renovable medio      : {stats['renovable_pct_media']:.1f}%")
    print(f"  Desplazamiento medio : {stats['desplazamiento_medio_h']:.2f} h")
    print()
    print(f"  Proyeccion anual estimada ({dias_cubiertos} dias -> 365 dias):")
    print(f"    CO2  : {stats['ahorro_co2_kg_total'] * factor_anual / 1000:,.1f} toneladas")
    print(f"    EUR  : {stats['ahorro_eur_total'] * factor_anual:,.0f} EUR")
    print()
    print(f"  Resultados guardados en: {csv_salida}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Optimizador de ventanas horarias para trenes de mercancias"
    )
    parser.add_argument(
        "--trenes", "-t",
        type=str,
        default="trenes.csv",
        help="CSV con circulaciones de Adif (default: trenes.csv)"
    )
    parser.add_argument(
        "--salida", "-s",
        type=str,
        default="resultados.csv",
        help="CSV de salida con el plan optimizado (default: resultados.csv)"
    )
    parser.add_argument(
        "--mock", "-m",
        action="store_true",
        help="Usar datos energeticos simulados sin llamar a ESIOS"
    )
    args = parser.parse_args()
    main(args.trenes, args.salida, args.mock)
