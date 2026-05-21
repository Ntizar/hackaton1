"""
modelo.py — Optimizador de ventanas horarias para trenes de mercancías
Hackathon MOV.IA by INECO — Reto: Soberanía Tecnológica

Lógica: dado un conjunto de trenes con hora de salida planificada,
busca la ventana horaria óptima (máx. renovable, mín. CO2 y precio)
dentro de un margen de desplazamiento configurable.

ENTRADA:
  - df_energia  : DataFrame con mix energético hora a hora (de ESIOS/REE)
  - df_trenes   : DataFrame con circulaciones planificadas (de Adif)

SALIDA:
  - DataFrame con hora óptima por tren + ahorros estimados de CO2 y coste
"""

import pandas as pd
import numpy as np


# ---------------------------------------------------------------------------
# CONFIGURACIÓN
# ---------------------------------------------------------------------------

MAX_DESPLAZAMIENTO_H = 3   # horas máx que se puede mover un tren
PESO_RENOVABLE       = 0.5  # peso del % renovable en el score
PESO_CO2             = 0.3  # peso del CO2 en el score
PESO_PRECIO          = 0.2  # peso del precio en el score


# ---------------------------------------------------------------------------
# FUNCIÓN PRINCIPAL
# ---------------------------------------------------------------------------

def optimizar_trenes(
    df_energia: pd.DataFrame,
    df_trenes: pd.DataFrame,
    max_desplazamiento_h: int = MAX_DESPLAZAMIENTO_H
) -> pd.DataFrame:
    """
    Para cada tren, evalúa todas las ventanas horarias candidatas
    y selecciona la que maximiza el score energético.

    Parámetros
    ----------
    df_energia : DataFrame con columnas:
        datetime (pd.Timestamp), renovable_pct (float),
        precio_eur_mwh (float), co2_g_kwh (float)

    df_trenes : DataFrame con columnas:
        tren_id (str), corredor (str), hora_salida (float, ej. 22.25 = 22:15),
        duracion_h (float), consumo_mwh (float)

    max_desplazamiento_h : int
        Margen máximo de adelanto/retraso en horas

    Devuelve
    --------
    DataFrame con columnas:
        tren_id, corredor, hora_actual, hora_optima, desplazamiento_h,
        renovable_pct_optima, co2_g_kwh_optima, precio_eur_mwh_optima,
        ahorro_co2_kg, ahorro_eur, score
    """

    _validar_inputs(df_energia, df_trenes)

    # Normalizar columnas para el scoring
    df_e = df_energia.copy()
    df_e['hora'] = pd.to_datetime(df_e['datetime'], errors='coerce').dt.hour
    df_e = df_e.groupby('hora').mean(numeric_only=True).reset_index()
    df_e[['renovable_pct', 'precio_eur_mwh', 'co2_g_kwh']] = (
        df_e[['renovable_pct', 'precio_eur_mwh', 'co2_g_kwh']]
        .interpolate()
        .ffill()
        .bfill()
    )

    precio_max = df_e['precio_eur_mwh'].max(skipna=True) or 1
    co2_max    = df_e['co2_g_kwh'].max(skipna=True) or 1

    # --- Paso 1: calcular ahorro potencial sin restricciones para priorizar ---
    # Los trenes que más se benefician del desplazamiento eligen primero
    def _ahorro_potencial(tren):
        hora_actual = float(tren['hora_salida'])
        duracion    = float(tren['duracion_h'])
        m_actual    = _metricas_ventana(df_e, hora_actual, duracion)
        score_actual = (
            PESO_RENOVABLE * m_actual['renovable_pct'] / 100
            - PESO_CO2     * m_actual['co2_g_kwh']     / co2_max
            - PESO_PRECIO  * m_actual['precio_eur_mwh'] / precio_max
        )
        score_optimo = max(
            (
                PESO_RENOVABLE * m['renovable_pct'] / 100
                - PESO_CO2     * m['co2_g_kwh']     / co2_max
                - PESO_PRECIO  * m['precio_eur_mwh'] / precio_max
            )
            for h in range(int(hora_actual) - max_desplazamiento_h,
                           int(hora_actual) + max_desplazamiento_h + 1)
            for m in [_metricas_ventana(df_e, h, duracion)]
        )
        return score_optimo - score_actual

    df_trenes_ord = df_trenes.copy()
    df_trenes_ord['_prioridad'] = df_trenes_ord.apply(_ahorro_potencial, axis=1)
    df_trenes_ord = df_trenes_ord.sort_values('_prioridad', ascending=False).reset_index(drop=True)

    # --- Paso 2: asignación greedy con slots de 15 minutos por corredor ---
    slots_ocupados = {}   # {corredor: set(slots de 15 minutos ocupados)}
    resultados = []

    for _, tren in df_trenes_ord.iterrows():

        hora_actual = float(tren['hora_salida'])
        duracion    = float(tren['duracion_h'])
        consumo     = float(tren['consumo_mwh'])
        corredor    = tren['corredor']

        ref = _metricas_ventana(df_e, hora_actual, duracion)

        # Generar candidatos cada 15 min dentro del margen ±max_desplazamiento_h
        # El score energético se calcula a nivel horario (resolución de ESIOS)
        # Guardamos desp (offset real) y h_norm (hora 0-23.75) por separado
        # para evitar errores de wrap-around al cruzar medianoche
        candidatos = []
        h = hora_actual - max_desplazamiento_h
        while h <= hora_actual + max_desplazamiento_h + 0.01:
            h_norm = round(h % 24, 2)
            desp   = round(h - hora_actual, 2)
            m = _metricas_ventana(df_e, h_norm, duracion)
            score = (
                PESO_RENOVABLE * m['renovable_pct'] / 100
                - PESO_CO2     * m['co2_g_kwh']     / co2_max
                - PESO_PRECIO  * m['precio_eur_mwh'] / precio_max
            )
            candidatos.append((score, h_norm, desp, m))
            h = round(h + 0.25, 2)
        candidatos.sort(reverse=True)

        # Elegir el mejor intervalo libre del corredor, no solo la hora de salida.
        ocupados = slots_ocupados.setdefault(corredor, set())
        mejor_score, mejor_hora, mejor_desp, mejor_metricas = candidatos[0]   # fallback
        for score, h, desp, m in candidatos:
            slots_candidato = _slots_intervalo(h, duracion)
            if not slots_candidato.intersection(ocupados):
                mejor_score, mejor_hora, mejor_desp, mejor_metricas = score, h, desp, m
                break

        ocupados.update(_slots_intervalo(mejor_hora, duracion))

        ahorro_co2 = consumo * (ref['co2_g_kwh'] - mejor_metricas['co2_g_kwh'])
        ahorro_eur = consumo * (ref['precio_eur_mwh'] - mejor_metricas['precio_eur_mwh'])
        consumo_kwh = consumo * 1000
        no_renovable_actual_kwh = consumo_kwh * (100 - ref['renovable_pct']) / 100
        no_renovable_optima_kwh = consumo_kwh * (100 - mejor_metricas['renovable_pct']) / 100
        ahorro_kwh_no_renovable = no_renovable_actual_kwh - no_renovable_optima_kwh
        desp_h     = mejor_desp

        resultados.append({
            'tren_id':               tren['tren_id'],
            'corredor':              corredor,
            'hora_actual':           _format_hora(hora_actual),
            'hora_optima':           _format_hora(mejor_hora),
            'desplazamiento_h':      desp_h,
            # métricas originales (para mostrar la mejora)
            'renovable_pct_actual':  round(ref['renovable_pct'], 1),
            'co2_g_kwh_actual':      round(ref['co2_g_kwh'], 1),
            'precio_eur_mwh_actual': round(ref['precio_eur_mwh'], 2),
            # métricas óptimas
            'renovable_pct_optima':  round(mejor_metricas['renovable_pct'], 1),
            'co2_g_kwh_optima':      round(mejor_metricas['co2_g_kwh'], 1),
            'precio_eur_mwh_optima': round(mejor_metricas['precio_eur_mwh'], 2),
            # mejoras
            'mejora_renovable_pct':  round(mejor_metricas['renovable_pct'] - ref['renovable_pct'], 1),
            'consumo_kwh':            round(consumo_kwh, 1),
            'demanda_media_kw':       round(consumo_kwh / max(duracion, 0.25), 1),
            'no_renovable_actual_kwh': round(no_renovable_actual_kwh, 1),
            'no_renovable_optima_kwh': round(no_renovable_optima_kwh, 1),
            'ahorro_kwh_no_renovable': round(ahorro_kwh_no_renovable, 1),
            'ahorro_co2_kg':         round(ahorro_co2, 1),
            'ahorro_eur':            round(ahorro_eur, 2),
            'score':                 round(mejor_score, 4),
            'motivo_cambio':          _motivo_cambio(ref, mejor_metricas, desp_h),
        })

    df_resultado = pd.DataFrame(resultados)
    return df_resultado.sort_values('ahorro_co2_kg', ascending=False).reset_index(drop=True)


# ---------------------------------------------------------------------------
# RESUMEN AGREGADO
# ---------------------------------------------------------------------------

def resumen(df_resultado: pd.DataFrame) -> dict:
    """
    Devuelve métricas globales para mostrar en el dashboard.

    Parámetros
    ----------
    df_resultado : salida de optimizar_trenes()

    Devuelve
    --------
    dict con:
        trenes_optimizados, trenes_desplazados,
        ahorro_co2_kg_total, ahorro_eur_total,
        renovable_pct_media, desplazamiento_medio_h
    """
    desplazados = df_resultado[df_resultado['desplazamiento_h'].abs() > 0.01]

    return {
        'trenes_optimizados':     len(df_resultado),
        'trenes_desplazados':     len(desplazados),
        'consumo_kwh_total':      round(df_resultado.get('consumo_kwh', pd.Series(dtype=float)).sum(), 1),
        'ahorro_kwh_no_renovable_total': round(df_resultado.get('ahorro_kwh_no_renovable', pd.Series(dtype=float)).sum(), 1),
        'ahorro_co2_kg_total':    round(df_resultado['ahorro_co2_kg'].sum(), 1),
        'ahorro_eur_total':       round(df_resultado['ahorro_eur'].sum(), 2),
        'renovable_pct_media':    round(df_resultado['renovable_pct_optima'].mean(), 1),
        'mejora_renovable_media': round(df_resultado['mejora_renovable_pct'].mean(), 1),
        'desplazamiento_medio_h': round(desplazados['desplazamiento_h'].abs().mean(), 1)
                                   if len(desplazados) else 0.0,
    }


# ---------------------------------------------------------------------------
# HELPERS INTERNOS
# ---------------------------------------------------------------------------

def _format_hora(h: float) -> str:
    """Convierte hora decimal a string 'HH:MM'. Ej: 22.25 → '22:15'"""
    total_minutos = int(round((h % 24) * 60)) % (24 * 60)
    hh = total_minutos // 60
    mm = total_minutos % 60
    return f"{hh:02d}:{mm:02d}"


def _metricas_ventana(df_e: pd.DataFrame, hora_inicio: float, duracion: float) -> dict:
    """Media de métricas energéticas durante las horas del trayecto."""
    horas = [(int(hora_inicio) + i) % 24 for i in range(max(1, int(np.ceil(duracion))))]
    ventana = df_e[df_e['hora'].isin(horas)]

    if ventana.empty:
        return {'renovable_pct': 0.0, 'co2_g_kwh': 999.0, 'precio_eur_mwh': 999.0}

    return {
        'renovable_pct':  ventana['renovable_pct'].mean(),
        'co2_g_kwh':      ventana['co2_g_kwh'].mean(),
        'precio_eur_mwh': ventana['precio_eur_mwh'].mean(),
    }


def _slots_intervalo(hora_inicio: float, duracion: float) -> set[int]:
    """Slots de 15 minutos ocupados por un tren durante todo el trayecto."""
    slot_inicio = int(round((hora_inicio % 24) * 4)) % 96
    total_slots = max(1, int(np.ceil(duracion * 4)))
    return {(slot_inicio + offset) % 96 for offset in range(total_slots)}


def _motivo_cambio(ref: dict, mejor_metricas: dict, desplazamiento_h: float) -> str:
    if abs(desplazamiento_h) <= 0.01:
        return 'Se mantiene: no hay mejora material dentro de la ventana'
    mejoras = []
    if mejor_metricas['renovable_pct'] > ref['renovable_pct']:
        mejoras.append('mas renovable')
    if mejor_metricas['co2_g_kwh'] < ref['co2_g_kwh']:
        mejoras.append('menos CO2')
    if mejor_metricas['precio_eur_mwh'] < ref['precio_eur_mwh']:
        mejoras.append('menor precio')
    return ', '.join(mejoras) if mejoras else 'mejor equilibrio operativo'


def _validar_inputs(df_energia: pd.DataFrame, df_trenes: pd.DataFrame):
    cols_energia = {'datetime', 'renovable_pct', 'precio_eur_mwh', 'co2_g_kwh'}
    cols_trenes  = {'tren_id', 'corredor', 'hora_salida', 'duracion_h', 'consumo_mwh'}

    for col in cols_energia:
        if col not in df_energia.columns:
            raise ValueError(f"df_energia debe tener la columna '{col}'")
    for col in cols_trenes:
        if col not in df_trenes.columns:
            raise ValueError(f"df_trenes debe tener la columna '{col}'")


# ---------------------------------------------------------------------------
# TEST LOCAL — ejecuta `python modelo.py` para probar sin datos reales
# ---------------------------------------------------------------------------

if __name__ == "__main__":

    # Simula un día típico: mucha renovable de madrugada (eólica) y al mediodía (solar)
    horas = pd.date_range('2026-05-21', periods=24, freq='h')
    df_energia = pd.DataFrame({
        'datetime':       horas,
        'renovable_pct':  [55, 60, 68, 75, 82, 88, 91, 89, 78, 65, 58, 52,
                           48, 45, 50, 60, 72, 85, 93, 95, 90, 80, 70, 62],
        'precio_eur_mwh': [70, 65, 58, 48, 35, 22, 15, 18, 32, 48, 62, 72,
                           78, 80, 75, 62, 48, 30, 16, 12, 20, 35, 52, 65],
        'co2_g_kwh':      [110,105, 95, 82, 65, 48, 38, 42, 60, 82,100,112,
                           118,120,115,100, 82, 58, 36, 30, 40, 62, 88,105],
    })

    df_trenes = pd.DataFrame({
        'tren_id':     ['M001', 'M002', 'M003', 'M004', 'M005'],
        'corredor':    ['Madrid-Zaragoza', 'Madrid-Valencia',
                        'Madrid-Bilbao',   'Madrid-Sevilla', 'Madrid-Barcelona'],
        'hora_salida': [2.0, 3.25, 22.5, 1.0, 23.75],   # float: 3.25 = 03:15
        'duracion_h':  [3.5, 4.0, 5.0, 2.5, 6.0],
        'consumo_mwh': [8.0, 9.5, 11.0, 6.0, 13.0],
    })

    print("=" * 60)
    print("OPTIMIZACIÓN DE VENTANAS HORARIAS — TRENES DE MERCANCÍAS")
    print("=" * 60)

    resultado = optimizar_trenes(df_energia, df_trenes)
    print("\n📋 RESULTADO POR TREN:")
    print(resultado.to_string(index=False))

    stats = resumen(resultado)
    print("\n📊 RESUMEN GLOBAL:")
    for k, v in stats.items():
        print(f"  {k:<30} {v}")

    print("\n✅ Modelo listo. Sustituye df_energia y df_trenes por los datos reales de ESIOS y Adif.")