# Estructura del repositorio

Este documento describe la organizacion del repositorio `hackaton1` y la funcion de cada archivo dentro del pipeline TrEnergIA.

## Arbol principal

```text
.
├── .env.example
├── .gitattributes
├── .gitignore
├── README.md
├── README2.md
├── requirements.txt
├── probar_trenergia.bat
├── avegtfs.zip
├── dashboard.py
├── datos_energia.py
├── gtfs_trenes.py
├── modelo.py
├── main.py
├── preparar_trenes.py
└── ver_resultados.py
```

## Archivos de documentacion y configuracion

| Archivo | Funcion |
| --- | --- |
| `README.md` | Documentacion principal: que calcula el sistema, como se ejecuta, indicadores ESIOS y enfoque de soberania. |
| `README2.md` | Este documento. Resume la estructura del repositorio. |
| `requirements.txt` | Dependencias Python minimas: `pandas`, `numpy` y `requests`. |
| `.env.example` | Plantilla para configurar `ESIOS_TOKEN` sin subir secretos al repositorio. |
| `.gitignore` | Excluye `.env`, `.venv`, caches Python, salidas generadas y CSV temporales. |
| `.gitattributes` | Normaliza finales de linea y marca `avegtfs.zip` como binario. |
| `probar_trenergia.bat` | Script Windows para crear entorno, instalar dependencias, ejecutar el pipeline mensual y abrir el dashboard. |

## Datos de entrada

| Archivo | Funcion |
| --- | --- |
| `avegtfs.zip` | Feed GTFS de Renfe usado para generar trenes, horarios, estaciones, trayectos y consumo estimado. |
| `.env` | Archivo local opcional, no versionado, para guardar `ESIOS_TOKEN`. |

El ZIP GTFS contiene internamente ficheros como:

```text
agency.txt
calendar.txt
calendar_dates.txt
routes.txt
stops.txt
stop_times.txt
trips.txt
```

## Modulos Python principales

### `dashboard.py`

Orquestador mensual del sistema. Hace todo el flujo completo:

1. carga trenes desde GTFS con `gtfs_trenes.py`;
2. carga energia desde ESIOS/mock con `datos_energia.py`;
3. optimiza horarios con `modelo.py`;
4. agrega KPIs diarios, horarios y por corredor;
5. genera CSV, JSON de auditoria y dashboard HTML.

Comando principal:

```powershell
python dashboard.py --mes 2026-06 --mock-energia
```

### `gtfs_trenes.py`

Convierte el GTFS en una tabla mensual de circulaciones ferroviarias.

Calcula:

- `tren_id`, `trip_id`, linea y tipo de servicio;
- fecha, hora de salida y hora de llegada;
- origen, destino y coordenadas;
- distancia aproximada del trayecto;
- consumo bruto, regeneracion estimada y consumo neto;
- potencia media estimada.

Salida conceptual: `trenes_gtfs_mes.csv`.

### `datos_energia.py`

Descarga y normaliza datos energeticos horarios.

Si existe `ESIOS_TOKEN`, usa la API de ESIOS/REE. Si no existe token o se fuerza `--mock`, genera datos simulados reproducibles.

Indicadores reales configurados:

- demanda real nacional;
- generacion eolica;
- generacion solar fotovoltaica y solar termica;
- generacion hidraulica;
- termica renovable;
- bombeo en carga y turbinacion;
- CO2 asociado a generacion real;
- precio SPOT.

Salida conceptual: `energia_mes.csv`.

### `modelo.py`

Contiene el optimizador de ventanas horarias.

Para cada tren:

- prueba salidas cada 15 minutos;
- limita cambios a `+/- 3 h`;
- puntua cada candidato por renovable, CO2 y precio;
- evita solapes simples por corredor usando slots de 15 minutos;
- calcula ahorro de CO2, euros y kWh no renovables evitados.

Score usado:

```text
score = 0.5 renovable - 0.3 CO2 - 0.2 precio
```

### `main.py`

Flujo historico del prototipo inicial. Trabaja con un CSV de trenes ya preparado, en vez de leer directamente GTFS.

Uso esperado:

```powershell
python main.py --trenes trenes.csv --salida resultados.csv --mock
```

### `preparar_trenes.py`

Conversor auxiliar del prototipo inicial. Convierte un CSV de trayectos agregados al formato esperado por `main.py`.

### `ver_resultados.py`

Herramienta CLI para leer un CSV de resultados y mostrar resumen de un dia concreto.

## Carpetas generadas localmente

Estas carpetas no se suben al repositorio por `.gitignore`.

```text
.venv/
__pycache__/
salidas/
```

La carpeta `salidas/` se crea al ejecutar `dashboard.py` o `probar_trenergia.bat`.

Estructura tipica:

```text
salidas/
└── mes_2026-06/
    ├── auditoria.json
    ├── dashboard.html
    ├── energia_mes.csv
    ├── resultados_optimizacion_mes.csv
    └── trenes_gtfs_mes.csv
```

## Flujo de datos

```text
avegtfs.zip
   ↓
gtfs_trenes.py
   ↓
trenes_gtfs_mes.csv
   ↓
modelo.py ← datos_energia.py ← ESIOS/mock
   ↓
resultados_optimizacion_mes.csv
   ↓
dashboard.py
   ↓
auditoria.json + dashboard.html
```

## Flujo recomendado de uso

1. Ejecutar `probar_trenergia.bat` en Windows.
2. Revisar el dashboard abierto en el navegador.
3. Revisar `salidas/mes_YYYY-MM/auditoria.json` para trazabilidad.
4. Revisar `resultados_optimizacion_mes.csv` para detalle por tren.
5. Configurar `ESIOS_TOKEN` y repetir sin `--mock-energia` cuando se quieran datos reales.