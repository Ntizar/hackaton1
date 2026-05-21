"""Pipeline mensual auditable y dashboard HTML para soberania energetica ferroviaria."""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from datos_energia import cargar_energia
from gtfs_trenes import GTFS_ZIP, cargar_trenes_gtfs
from modelo import optimizar_trenes


ROOT = Path(__file__).resolve().parent
DEFAULT_OUTPUT_DIR = ROOT / "salidas"


def ejecutar_pipeline(
    mes: str,
    gtfs_zip: Path,
    salida_dir: Path,
    mock_energia: bool,
    max_trenes: int | None,
) -> dict[str, Path]:
    mes_inicio, mes_fin = _rango_mes(mes)
    carpeta_mes = salida_dir / f"mes_{mes}"
    carpeta_mes.mkdir(parents=True, exist_ok=True)

    print("=" * 72)
    print("PIPELINE MENSUAL - SOBERANIA ENERGETICA FERROVIARIA")
    print("=" * 72)
    print(f"Mes: {mes} ({mes_inicio} -> {mes_fin})")
    print(f"GTFS: {gtfs_zip}")
    print()

    print("[1/5] Cargando GTFS y estimando consumo por viaje...")
    df_trenes = cargar_trenes_gtfs(gtfs_zip, mes=mes, max_trenes=max_trenes)
    if df_trenes.empty:
        raise RuntimeError("No se generaron trenes desde el GTFS para el mes indicado.")
    print(f"      trenes: {len(df_trenes):,} | consumo: {df_trenes['consumo_kwh'].sum():,.0f} kWh")

    print("[2/5] Cargando energia horaria ESIOS/mock...")
    df_energia = cargar_energia(str(mes_inicio), str(mes_fin), mock=mock_energia)
    print(f"      horas energia: {len(df_energia):,} | fuente: {_fuente_energia(df_energia)}")

    print("[3/5] Optimizando ventanas por dia y corredor...")
    df_resultados = _optimizar_mes(df_trenes, df_energia)
    if df_resultados.empty:
        raise RuntimeError("El optimizador no produjo resultados.")
    modificados = int((df_resultados["desplazamiento_h"].abs() > 0.01).sum())
    print(f"      resultados: {len(df_resultados):,} | trenes modificados: {modificados:,}")

    print("[4/5] Agregando KPIs, balance horario y trazabilidad...")
    resumen_mensual = _resumen_mensual(df_resultados, df_trenes, df_energia, mes)
    datos_dashboard = _datos_dashboard(df_resultados, df_trenes, df_energia, resumen_mensual)
    auditoria = _auditoria(df_resultados, df_trenes, df_energia, resumen_mensual, mock_energia)

    rutas = {
        "trenes": carpeta_mes / "trenes_gtfs_mes.csv",
        "energia": carpeta_mes / "energia_mes.csv",
        "resultados": carpeta_mes / "resultados_optimizacion_mes.csv",
        "auditoria": carpeta_mes / "auditoria.json",
        "dashboard": carpeta_mes / "dashboard.html",
    }

    print("[5/5] Escribiendo CSV, auditoria y dashboard...")
    df_trenes.to_csv(rutas["trenes"], index=False)
    df_energia.to_csv(rutas["energia"], index=False)
    df_resultados.to_csv(rutas["resultados"], index=False)
    rutas["auditoria"].write_text(json.dumps(auditoria, ensure_ascii=False, indent=2), encoding="utf-8")
    rutas["dashboard"].write_text(_render_dashboard(datos_dashboard), encoding="utf-8")

    print()
    print("Listo.")
    for nombre, ruta in rutas.items():
        print(f"  {nombre:<10} {ruta}")
    return rutas


def _optimizar_mes(df_trenes: pd.DataFrame, df_energia: pd.DataFrame) -> pd.DataFrame:
    resultados: list[pd.DataFrame] = []
    columnas_detalle = [
        columna
        for columna in df_trenes.columns
        if columna not in {"fecha", "hora_salida", "duracion_h", "consumo_mwh", "consumo_kwh"}
    ]
    detalles = df_trenes[columnas_detalle].copy()

    for fecha, df_dia in df_trenes.groupby("fecha", sort=True):
        energia_dia = df_energia[df_energia["fecha"] == fecha].copy()
        if energia_dia.empty:
            energia_dia = df_energia.copy()
        df_resultado_dia = optimizar_trenes(energia_dia, df_dia)
        df_resultado_dia.insert(0, "fecha", fecha)
        df_resultado_dia = df_resultado_dia.merge(detalles, on=["tren_id", "corredor"], how="left")
        resultados.append(df_resultado_dia)

    if not resultados:
        return pd.DataFrame()
    return pd.concat(resultados, ignore_index=True)


def _resumen_mensual(
    df_resultados: pd.DataFrame,
    df_trenes: pd.DataFrame,
    df_energia: pd.DataFrame,
    mes: str,
) -> dict[str, Any]:
    desplazados = df_resultados[df_resultados["desplazamiento_h"].abs() > 0.01]
    ahorro_co2 = float(df_resultados["ahorro_co2_kg"].sum())
    ahorro_eur = float(df_resultados["ahorro_eur"].sum())
    ahorro_kwh = float(df_resultados["ahorro_kwh_no_renovable"].sum())
    consumo_total = float(df_resultados["consumo_kwh"].sum())

    return {
        "mes": mes,
        "fuente_energia": _fuente_energia(df_energia),
        "trenes_totales": int(len(df_resultados)),
        "trenes_modificados": int(len(desplazados)),
        "porcentaje_modificados": round(100 * len(desplazados) / max(len(df_resultados), 1), 1),
        "consumo_total_kwh": round(consumo_total, 1),
        "ahorro_kwh_no_renovable": round(ahorro_kwh, 1),
        "ahorro_co2_kg": round(ahorro_co2, 1),
        "ahorro_co2_t": round(ahorro_co2 / 1000, 3),
        "ahorro_eur": round(ahorro_eur, 2),
        "renovable_media_actual_pct": round(float(df_resultados["renovable_pct_actual"].mean()), 1),
        "renovable_media_optima_pct": round(float(df_resultados["renovable_pct_optima"].mean()), 1),
        "desplazamiento_medio_h": round(float(desplazados["desplazamiento_h"].abs().mean()) if len(desplazados) else 0, 2),
        "corredores": int(df_trenes["corredor"].nunique()),
        "dias": int(df_trenes["fecha"].nunique()),
    }


def _datos_dashboard(
    df_resultados: pd.DataFrame,
    df_trenes: pd.DataFrame,
    df_energia: pd.DataFrame,
    resumen_mensual: dict[str, Any],
) -> dict[str, Any]:
    return {
        "summary": resumen_mensual,
        "daily": _serie_diaria(df_resultados),
        "hourlyBalance": _balance_horario(df_resultados, df_energia, resumen_mensual["dias"]),
        "corridors": _resumen_corredores(df_resultados),
        "mapRoutes": _rutas_mapa(df_resultados),
        "auditSteps": _pasos_auditoria(df_resultados, df_trenes, df_energia, resumen_mensual),
    }


def _serie_diaria(df_resultados: pd.DataFrame) -> list[dict[str, Any]]:
    df_diario = (
        df_resultados.assign(modificado=df_resultados["desplazamiento_h"].abs() > 0.01)
        .groupby("fecha", as_index=False)
        .agg(
            ahorro_co2_kg=("ahorro_co2_kg", "sum"),
            ahorro_eur=("ahorro_eur", "sum"),
            ahorro_kwh=("ahorro_kwh_no_renovable", "sum"),
            modificados=("modificado", "sum"),
        )
    )
    return _records_redondeados(df_diario, 2)


def _balance_horario(df_resultados: pd.DataFrame, df_energia: pd.DataFrame, dias: int) -> list[dict[str, Any]]:
    df_balance = df_resultados.copy()
    df_balance["hora_actual_int"] = df_balance["hora_actual"].map(_hora_desde_texto)
    df_balance["hora_optima_int"] = df_balance["hora_optima"].map(_hora_desde_texto)
    demanda_actual = df_balance.groupby("hora_actual_int")["consumo_kwh"].sum() / max(dias, 1) / 1000
    demanda_optima = df_balance.groupby("hora_optima_int")["consumo_kwh"].sum() / max(dias, 1) / 1000
    energia_media = df_energia.groupby("hora", as_index=False).mean(numeric_only=True)
    energia_by_hour = energia_media.set_index("hora").to_dict("index")

    filas: list[dict[str, Any]] = []
    for hora in range(24):
        energia = energia_by_hour.get(hora, {})
        filas.append(
            {
                "hora": f"{hora:02d}:00",
                "demandaActualMw": round(float(demanda_actual.get(hora, 0)), 3),
                "demandaOptimizadaMw": round(float(demanda_optima.get(hora, 0)), 3),
                "eolicaMw": round(float(energia.get("eolica_mw", 0)), 2),
                "solarMw": round(float(energia.get("solar_fv_mw", 0)) + float(energia.get("solar_termica_mw", 0)), 2),
                "hidraulicaMw": round(float(energia.get("hidraulica_mw", 0)), 2),
                "bateriaMw": round(float(energia.get("bombeo_turbinacion_mw", 0)) - float(energia.get("bombeo_carga_mw", 0)), 2),
                "renovablePct": round(float(energia.get("renovable_pct", 0)), 1),
            }
        )
    return filas


def _resumen_corredores(df_resultados: pd.DataFrame) -> list[dict[str, Any]]:
    df_corredores = (
        df_resultados.assign(modificado=df_resultados["desplazamiento_h"].abs() > 0.01)
        .groupby("corredor", as_index=False)
        .agg(
            trenes=("tren_id", "count"),
            modificados=("modificado", "sum"),
            ahorro_co2_kg=("ahorro_co2_kg", "sum"),
            ahorro_kwh=("ahorro_kwh_no_renovable", "sum"),
            ahorro_eur=("ahorro_eur", "sum"),
        )
        .sort_values("ahorro_co2_kg", ascending=False)
        .head(12)
    )
    return _records_redondeados(df_corredores, 2)


def _rutas_mapa(df_resultados: pd.DataFrame) -> list[dict[str, Any]]:
    columnas_coord = ["origen_lat", "origen_lon", "destino_lat", "destino_lon", "mid_lat", "mid_lon"]
    df_mapa = df_resultados.dropna(subset=columnas_coord).copy()
    df_mapa = df_mapa[(df_mapa["origen_lat"] != 0) & (df_mapa["destino_lat"] != 0)]
    df_mapa = df_mapa[df_mapa["desplazamiento_h"].abs() > 0.01]
    if df_mapa.empty:
        df_mapa = df_resultados.dropna(subset=columnas_coord).copy()
    df_mapa = df_mapa.sort_values("ahorro_co2_kg", ascending=False).head(80)

    rutas: list[dict[str, Any]] = []
    for fila in df_mapa.to_dict("records"):
        rutas.append(
            {
                "trenId": fila.get("tren_id", ""),
                "linea": fila.get("linea", "RENFE"),
                "origen": fila.get("origen", ""),
                "destino": fila.get("destino", ""),
                "actual": fila.get("hora_actual", ""),
                "optima": fila.get("hora_optima", ""),
                "desplazamiento": round(float(fila.get("desplazamiento_h", 0)), 2),
                "ahorroCo2": round(float(fila.get("ahorro_co2_kg", 0)), 1),
                "ahorroKwh": round(float(fila.get("ahorro_kwh_no_renovable", 0)), 1),
                "coords": [
                    [float(fila.get("origen_lat", 0)), float(fila.get("origen_lon", 0))],
                    [float(fila.get("destino_lat", 0)), float(fila.get("destino_lon", 0))],
                ],
                "mid": [float(fila.get("mid_lat", 0)), float(fila.get("mid_lon", 0))],
            }
        )
    return rutas


def _auditoria(
    df_resultados: pd.DataFrame,
    df_trenes: pd.DataFrame,
    df_energia: pd.DataFrame,
    resumen_mensual: dict[str, Any],
    mock_energia: bool,
) -> dict[str, Any]:
    return {
        "resumen": resumen_mensual,
        "fuentes": {
            "gtfs": "avegtfs.zip",
            "energia": _fuente_energia(df_energia),
            "energia_mock_forzada": mock_energia,
        },
        "modelo": {
            "ventana_horas": "+/- 3",
            "resolucion_slots_min": 15,
            "score": "0.5 renovable - 0.3 CO2 - 0.2 precio",
            "ahorro_kwh": "kWh no renovables evitados al mover consumo a horas con mayor renovable_pct",
            "restriccion_corredor": "sin solape de intervalos por corredor en slots de 15 minutos",
        },
        "conteos": {
            "trenes_gtfs": int(len(df_trenes)),
            "resultados": int(len(df_resultados)),
            "horas_energia": int(len(df_energia)),
        },
        "pasos": _pasos_auditoria(df_resultados, df_trenes, df_energia, resumen_mensual),
    }


def _pasos_auditoria(
    df_resultados: pd.DataFrame,
    df_trenes: pd.DataFrame,
    df_energia: pd.DataFrame,
    resumen_mensual: dict[str, Any],
) -> list[dict[str, str]]:
    return [
        {
            "titulo": "1. GTFS a viajes",
            "detalle": f"{len(df_trenes):,} circulaciones del mes, con origen/destino, duracion, distancia y consumo neto estimado.",
        },
        {
            "titulo": "2. Energia horaria",
            "detalle": f"{len(df_energia):,} horas desde {_fuente_energia(df_energia)} con demanda, eolica, solar, hidraulica, bombeo, CO2 y precio.",
        },
        {
            "titulo": "3. Ventanas candidatas",
            "detalle": "Cada tren prueba salidas cada 15 minutos dentro de +/- 3 horas respecto a su horario GTFS.",
        },
        {
            "titulo": "4. Restricciones",
            "detalle": "El corredor reserva todo el intervalo del trayecto para evitar solapes simples de capacidad.",
        },
        {
            "titulo": "5. Resultado mensual",
            "detalle": f"{resumen_mensual['trenes_modificados']:,} trenes modificados, {resumen_mensual['ahorro_co2_t']:,.2f} tCO2 y {resumen_mensual['ahorro_kwh_no_renovable']:,.0f} kWh no renovables evitados.",
        },
    ]


def _render_dashboard(datos: dict[str, Any]) -> str:
    datos_json = json.dumps(datos, ensure_ascii=False)
    return HTML_TEMPLATE.replace("__DASHBOARD_DATA__", datos_json)


def _records_redondeados(df: pd.DataFrame, decimals: int) -> list[dict[str, Any]]:
  records: list[dict[str, Any]] = []
  for record in df.to_dict("records"):
    clean_record: dict[str, Any] = {}
    for key, value in record.items():
      key_text = str(key)
      if isinstance(value, float):
        clean_record[key_text] = round(value, decimals)
      elif hasattr(value, "item"):
        clean_record[key_text] = value.item()
      else:
        clean_record[key_text] = value
    records.append(clean_record)
  return records


def _hora_desde_texto(valor: str) -> int:
    try:
        return int(str(valor).split(":")[0])
    except (ValueError, IndexError):
        return 0


def _fuente_energia(df_energia: pd.DataFrame) -> str:
    if "fuente_energia" not in df_energia.columns or df_energia.empty:
        return "desconocida"
    return str(df_energia["fuente_energia"].mode().iloc[0])


def _rango_mes(mes: str) -> tuple[date, date]:
    inicio = pd.Timestamp(f"{mes}-01").date()
    fin = (pd.Timestamp(f"{mes}-01") + pd.offsets.MonthEnd(0)).date()
    return inicio, fin


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Genera resultados mensuales y dashboard auditable")
    parser.add_argument("--mes", default="2026-06", help="Mes a calcular en formato YYYY-MM")
    parser.add_argument("--gtfs", type=Path, default=GTFS_ZIP, help="ZIP GTFS de Renfe")
    parser.add_argument("--salida-dir", type=Path, default=DEFAULT_OUTPUT_DIR, help="Directorio de salidas")
    parser.add_argument("--mock-energia", action="store_true", help="Fuerza energia simulada reproducible")
    parser.add_argument("--max-trenes", type=int, default=None, help="Limita trenes para pruebas rapidas")
    return parser.parse_args()


HTML_TEMPLATE = r'''<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>TrEnergIA | Dashboard mensual</title>
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
  <style>
    :root {
      color-scheme: light;
      --ink: #172033;
      --muted: #627084;
      --line: #d9e1ea;
      --panel: #ffffff;
      --bg: #eef3f7;
      --navy: #18283d;
      --green: #138a63;
      --blue: #2476b8;
      --amber: #b7791f;
      --red: #b54545;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--ink);
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    .app { min-height: 100vh; display: flex; flex-direction: column; }
    header {
      background: var(--navy);
      color: #f7fbff;
      padding: 18px 24px;
      display: flex;
      justify-content: space-between;
      gap: 20px;
      align-items: center;
      border-bottom: 4px solid #5fc4a1;
    }
    h1, h2, h3, p { margin: 0; }
    h1 { font-size: 22px; font-weight: 750; }
    .subtitle { margin-top: 4px; color: #b9c7d7; font-size: 13px; }
    .source-pill {
      display: inline-flex;
      align-items: center;
      border: 1px solid rgba(255,255,255,0.25);
      border-radius: 999px;
      padding: 8px 12px;
      font-size: 12px;
      background: rgba(255,255,255,0.08);
      white-space: nowrap;
    }
    main { width: min(1480px, 100%); margin: 0 auto; padding: 18px; display: grid; gap: 16px; }
    .kpis { display: grid; grid-template-columns: repeat(5, minmax(160px, 1fr)); gap: 12px; }
    .kpi, section {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: 0 10px 28px rgba(24, 40, 61, 0.08);
    }
    .kpi { padding: 14px; min-height: 104px; display: flex; flex-direction: column; justify-content: space-between; }
    .kpi span { color: var(--muted); font-size: 12px; font-weight: 650; text-transform: uppercase; }
    .kpi strong { display: block; margin-top: 8px; font-size: clamp(20px, 2.2vw, 30px); line-height: 1.05; }
    .kpi small { color: var(--muted); font-size: 12px; }
    .grid-main { display: grid; grid-template-columns: minmax(0, 1.35fr) minmax(360px, 0.65fr); gap: 16px; align-items: stretch; }
    section { overflow: hidden; }
    .section-head { padding: 14px 16px; border-bottom: 1px solid var(--line); display: flex; justify-content: space-between; gap: 16px; align-items: start; }
    .section-head h2 { font-size: 16px; }
    .section-head p { margin-top: 4px; color: var(--muted); font-size: 12px; }
    .section-body { padding: 14px 16px; }
    .chart-box { height: 310px; width: 100%; }
    .chart-box.tall { height: 380px; }
    #map { height: 515px; width: 100%; background: #dbe7ef; }
    .audit-list { display: grid; gap: 10px; }
    .audit-step { display: grid; grid-template-columns: 26px minmax(0, 1fr); gap: 10px; align-items: start; }
    .audit-index { width: 26px; height: 26px; border-radius: 50%; background: #e1f3ec; color: var(--green); display: grid; place-items: center; font-weight: 800; font-size: 12px; }
    .audit-step strong { display: block; font-size: 13px; }
    .audit-step p { color: var(--muted); font-size: 12px; line-height: 1.45; margin-top: 2px; }
    .split { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
    table { width: 100%; border-collapse: collapse; font-size: 12px; }
    th, td { padding: 9px 8px; border-bottom: 1px solid var(--line); text-align: right; }
    th:first-child, td:first-child { text-align: left; }
    th { color: var(--muted); font-size: 11px; text-transform: uppercase; }
    .positive { color: var(--green); font-weight: 750; }
    .warn { color: var(--amber); font-weight: 750; }
    footer { color: var(--muted); font-size: 12px; padding: 0 18px 18px; width: min(1480px, 100%); margin: 0 auto; }
    @media (max-width: 1100px) {
      .kpis { grid-template-columns: repeat(2, 1fr); }
      .grid-main, .split { grid-template-columns: 1fr; }
      header { align-items: flex-start; flex-direction: column; }
    }
    @media (max-width: 640px) {
      main { padding: 10px; }
      .kpis { grid-template-columns: 1fr; }
      .chart-box, .chart-box.tall, #map { height: 330px; }
    }
  </style>
</head>
<body>
  <div class="app">
    <header>
      <div>
        <h1>TrEnergIA · Soberania electrica ferroviaria</h1>
        <p class="subtitle" id="subtitle"></p>
      </div>
      <div class="source-pill" id="sourcePill"></div>
    </header>

    <main>
      <div class="kpis" id="kpis"></div>

      <div class="grid-main">
        <section>
          <div class="section-head">
            <div>
              <h2>Mapa de trenes modificados</h2>
              <p>Rutas GTFS con mayor ahorro de CO2 en el mes.</p>
            </div>
          </div>
          <div id="map"></div>
        </section>

        <section>
          <div class="section-head">
            <div>
              <h2>Trazabilidad del modelo</h2>
              <p>Cadena de calculo registrada para auditoria.</p>
            </div>
          </div>
          <div class="section-body audit-list" id="auditList"></div>
        </section>
      </div>

      <div class="split">
        <section>
          <div class="section-head">
            <div>
              <h2>Ahorro diario</h2>
              <p>CO2, kWh no renovables evitados y ahorro economico.</p>
            </div>
          </div>
          <div class="section-body"><canvas id="dailyChart" class="chart-box"></canvas></div>
        </section>

        <section>
          <div class="section-head">
            <div>
              <h2>Balance horario medio</h2>
              <p>Demanda ferroviaria optimizada frente a renovables disponibles.</p>
            </div>
          </div>
          <div class="section-body"><canvas id="hourlyChart" class="chart-box"></canvas></div>
        </section>
      </div>

      <section>
        <div class="section-head">
          <div>
            <h2>Corredores con mayor impacto</h2>
            <p>Agrupacion mensual por corredor operativo.</p>
          </div>
        </div>
        <div class="section-body"><div id="corridorTable"></div></div>
      </section>
    </main>

    <footer>Modelo auditable: GTFS local + energia ESIOS/mock + optimizacion por ventanas de 15 minutos. Los kWh ahorrados representan energia no renovable evitada, no reduccion fisica de traccion.</footer>
  </div>

  <script>
    const data = __DASHBOARD_DATA__;
    const nf = new Intl.NumberFormat('es-ES');
    const money = new Intl.NumberFormat('es-ES', { style: 'currency', currency: 'EUR', maximumFractionDigits: 0 });
    const summary = data.summary;

    document.getElementById('subtitle').textContent = `Mes ${summary.mes} · ${summary.dias} dias · ${summary.corredores} corredores`;
    document.getElementById('sourcePill').textContent = `Energia: ${summary.fuente_energia}`;

    const kpis = [
      ['Trenes del mes', nf.format(summary.trenes_totales), `${nf.format(summary.trenes_modificados)} modificados (${summary.porcentaje_modificados}%)`],
      ['CO2 evitado', `${nf.format(summary.ahorro_co2_t)} t`, `${nf.format(summary.ahorro_co2_kg)} kg`],
      ['kWh no renovables evitados', `${nf.format(summary.ahorro_kwh_no_renovable)} kWh`, `${nf.format(summary.consumo_total_kwh)} kWh consumidos`],
      ['Ahorro economico', money.format(summary.ahorro_eur), `desplazamiento medio ${summary.desplazamiento_medio_h} h`],
      ['Renovable media', `${summary.renovable_media_actual_pct}% -> ${summary.renovable_media_optima_pct}%`, 'antes y despues de optimizar'],
    ];
    document.getElementById('kpis').innerHTML = kpis.map(([label, value, sub]) => `
      <div class="kpi"><span>${label}</span><strong>${value}</strong><small>${sub}</small></div>
    `).join('');

    document.getElementById('auditList').innerHTML = data.auditSteps.map((step, index) => `
      <div class="audit-step"><div class="audit-index">${index + 1}</div><div><strong>${step.titulo}</strong><p>${step.detalle}</p></div></div>
    `).join('');

    const dailyLabels = data.daily.map(item => item.fecha.slice(8));
    new Chart(document.getElementById('dailyChart'), {
      data: {
        labels: dailyLabels,
        datasets: [
          { type: 'bar', label: 'CO2 evitado kg', data: data.daily.map(item => item.ahorro_co2_kg), backgroundColor: 'rgba(19, 138, 99, 0.72)', borderRadius: 4, yAxisID: 'y' },
          { type: 'bar', label: 'kWh no renovables', data: data.daily.map(item => item.ahorro_kwh), backgroundColor: 'rgba(36, 118, 184, 0.42)', borderRadius: 4, yAxisID: 'y' },
          { type: 'line', label: 'EUR', data: data.daily.map(item => item.ahorro_eur), borderColor: '#b7791f', backgroundColor: '#b7791f', tension: 0.25, pointRadius: 2, yAxisID: 'y1' },
        ]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: 'index', intersect: false },
        plugins: { legend: { position: 'bottom' } },
        scales: { y: { beginAtZero: true }, y1: { beginAtZero: true, position: 'right', grid: { drawOnChartArea: false } } }
      }
    });

    new Chart(document.getElementById('hourlyChart'), {
      type: 'line',
      data: {
        labels: data.hourlyBalance.map(item => item.hora),
        datasets: [
          { label: 'Demanda optimizada MW', data: data.hourlyBalance.map(item => item.demandaOptimizadaMw), borderColor: '#b54545', backgroundColor: 'rgba(181,69,69,0.15)', fill: true, tension: 0.2 },
          { label: 'Eolica MW', data: data.hourlyBalance.map(item => item.eolicaMw), borderColor: '#2476b8', tension: 0.2 },
          { label: 'Solar MW', data: data.hourlyBalance.map(item => item.solarMw), borderColor: '#d99a28', tension: 0.2 },
          { label: 'Hidraulica MW', data: data.hourlyBalance.map(item => item.hidraulicaMw), borderColor: '#138a63', tension: 0.2 },
          { label: 'Bateria/bombeo MW', data: data.hourlyBalance.map(item => item.bateriaMw), borderColor: '#6d5bd0', tension: 0.2 },
        ]
      },
      options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { position: 'bottom' } }, scales: { y: { beginAtZero: true } } }
    });

    const tableRows = data.corridors.map(item => `
      <tr>
        <td>${item.corredor}</td>
        <td>${nf.format(item.trenes)}</td>
        <td class="warn">${nf.format(item.modificados)}</td>
        <td class="positive">${nf.format(item.ahorro_co2_kg)}</td>
        <td class="positive">${nf.format(item.ahorro_kwh)}</td>
        <td class="positive">${money.format(item.ahorro_eur)}</td>
      </tr>
    `).join('');
    document.getElementById('corridorTable').innerHTML = `
      <table><thead><tr><th>Corredor</th><th>Trenes</th><th>Mod.</th><th>kg CO2</th><th>kWh</th><th>EUR</th></tr></thead><tbody>${tableRows}</tbody></table>
    `;

    const map = L.map('map', { preferCanvas: true }).setView([40.4168, -3.7038], 6);
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 18,
      attribution: '&copy; OpenStreetMap'
    }).addTo(map);
    const bounds = [];
    data.mapRoutes.forEach(route => {
      const color = route.ahorroCo2 >= 0 ? '#138a63' : '#b54545';
      L.polyline(route.coords, { color, weight: 2, opacity: 0.58 }).addTo(map);
      L.circleMarker(route.mid, { radius: 5, color, fillColor: color, fillOpacity: 0.9, weight: 1 }).addTo(map)
        .bindPopup(`<strong>${route.linea} · ${route.trenId}</strong><br>${route.origen} -> ${route.destino}<br>${route.actual} -> ${route.optima} (${route.desplazamiento} h)<br>${nf.format(route.ahorroCo2)} kg CO2 · ${nf.format(route.ahorroKwh)} kWh`);
      bounds.push(route.coords[0], route.coords[1]);
    });
    if (bounds.length) map.fitBounds(bounds, { padding: [24, 24] });
  </script>
</body>
</html>'''


if __name__ == "__main__":
    args = _parse_args()
    ejecutar_pipeline(args.mes, args.gtfs, args.salida_dir, args.mock_energia, args.max_trenes)