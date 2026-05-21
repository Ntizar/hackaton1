# TrEnergIA

Gemelo energetico ferroviario para auditar, simular y optimizar el consumo mensual de trenes Renfe/GTFS frente al mix electrico horario de ESIOS/REE.

## Que calcula

El proyecto convierte el GTFS local (`avegtfs.zip`) en circulaciones mensuales, estima consumo por viaje y mueve salidas dentro de una ventana operativa para aprovechar horas con mas renovable, menor CO2 y menor precio.

Resultados mensuales principales:

- trenes procesados y trenes modificados;
- consumo total ferroviario en kWh;
- kWh no renovables evitados;
- CO2 evitado en kg y toneladas;
- ahorro economico estimado en EUR;
- renovable media antes/despues;
- ranking de corredores con mayor impacto;
- dashboard HTML con mapa OpenStreetMap, graficas y trazabilidad.

Los kWh ahorrados representan energia no renovable evitada por desplazar consumo a horas con mayor `renovable_pct`. No significan que el tren consuma fisicamente menos energia de traccion.

## Como corre el modelo

1. `gtfs_trenes.py` lee `routes.txt`, `trips.txt`, `stop_times.txt` y `stops.txt` desde el ZIP GTFS.
2. Calcula origen, destino, duracion, distancia aproximada y consumo neto por tren con factores auditables por tipo de servicio.
3. `datos_energia.py` descarga ESIOS si existe `ESIOS_TOKEN`; si no, crea una serie mock reproducible.
4. La energia se normaliza por hora: demanda, eolica, solar, hidraulica, bombeo/baterias, precio, CO2 y renovable calculada.
5. `modelo.py` prueba salidas cada 15 minutos dentro de `+/- 3 h` y puntua cada opcion:

```text
score = 0.5 renovable - 0.3 CO2 - 0.2 precio
```

6. El corredor reserva todos los slots de 15 minutos del trayecto para evitar solapes simples.
7. `dashboard.py` agrega resultados por dia, hora y corredor, y genera `dashboard.html` + CSVs + `auditoria.json`.

## Instalacion

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Configura ESIOS solo si quieres datos reales:

```powershell
$env:ESIOS_TOKEN="tu_token"
```

Sin token, el sistema usa energia simulada reproducible y lo marca en el dashboard.

## Generar dashboard mensual

```powershell
python dashboard.py --mes 2026-06 --mock-energia
```

Salida esperada:

```text
salidas/mes_2026-06/trenes_gtfs_mes.csv
salidas/mes_2026-06/energia_mes.csv
salidas/mes_2026-06/resultados_optimizacion_mes.csv
salidas/mes_2026-06/auditoria.json
salidas/mes_2026-06/dashboard.html
```

Para una prueba rapida:

```powershell
python dashboard.py --mes 2026-06 --mock-energia --max-trenes 500
```

## Datos ESIOS usados

Indicadores base:

- `2037`: demanda real nacional;
- `2038`: generacion eolica nacional;
- `2042`: generacion hidraulica nacional;
- `2044`: solar fotovoltaica nacional;
- `2045`: solar termica nacional;
- `2046`: termica renovable nacional;
- `2065`: consumo bombeo nacional;
- `2066`: turbinacion bombeo nacional;
- `10355`: CO2 asociado generacion real;
- `600`: precio mercado SPOT diario.

`renovable_pct` se calcula como:

```text
100 * (eolica + solar + hidraulica + termica renovable + bombeo turbinacion) / demanda
```

## Archivos principales

- `dashboard.py`: pipeline mensual y dashboard HTML.
- `gtfs_trenes.py`: conversion GTFS -> trenes con consumo.
- `datos_energia.py`: ESIOS/mock y normalizacion energetica.
- `modelo.py`: optimizacion de ventanas horarias.
- `main.py`: flujo historico por CSV de trenes.
- `ver_resultados.py`: resumen CLI de resultados CSV.

## Enfoque de soberania

- datos abiertos GTFS + ESIOS;
- token fuera del codigo;
- salidas CSV/JSON auditables;
- fallback offline reproducible;
- dashboard local en HTML, sin backend externo obligatorio;
- algoritmo explicito y revisable.