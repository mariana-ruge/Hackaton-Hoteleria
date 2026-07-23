# Inventario 360 · Colsubsidio

Carga, limpia y valida inventarios dictados por voz, con auditoría obligatoria.

## Arquitectura

```text
reto-cocina/
├── backend/                Servidor Flask + lógica de dominio (Python)
│   ├── app.py               CLI (bodegas | limpiar | sesion | demo)
│   ├── server.py            Servidor web Flask · API REST
│   ├── unidades.py          Catálogo canónico, sinónimos, conversiones
│   ├── limpieza.py          Carga y limpieza de Excel/CSV
│   ├── bodegas.py           Limpieza del maestro de bodegas
│   ├── dictado.py           Parser de voz + matching difuso
│   ├── validacion.py        Bloqueo por unidad + umbral de anomalía
│   ├── auditoria.py         Sesiones, consenso, dictamen, bitácora
│   └── requirements.txt
├── frontend/                Interfaz web servida por Flask
│   ├── index.html            Plantilla (estructura de la app)
│   └── static/
│       ├── css/
│       │   ├── tokens.css     Design tokens Colsubsidio (color, radio, sombra)
│       │   └── app.css        Estilos de componentes
│       ├── js/
│       │   └── app.js         Lógica de interfaz (fetch a la API)
│       └── img/
├── data/                    Datos de ejemplo (Excel de apoyo)
└── venv/                    Entorno virtual de Python (no versionar)
```

## Instalación

```bash
python -m venv venv
venv\Scripts\activate          # Windows
pip install -r backend/requirements.txt
```

## Uso por línea de comandos

```bash
# Limpiar un catálogo
python backend/app.py limpiar catalogo.xlsx

# 3. Sesión de conteo con auditoría
python backend/app.py sesion catalogo.xlsx \
    --bodega "almacen general" \
    --contadores "Ana Torres,Luis Pérez" \
    --auditor "Carmen Díaz"

# Demostración completa
python backend/app.py demo
```

---

## 1. Formato canónico único

Todo el sistema opera sobre un solo esquema, sin importar cómo venga el archivo:

| Campo | Descripción |
|---|---|
| `codigo` | SKU o referencia |
| `producto` | Nombre normalizado |
| `bodega` | Bodega normalizada |
| `unidad` | Código canónico: `KG G LB L ML UND CAJ PAQ BOL BAN` |
| `unidad_original` | Lo que decía el archivo (trazabilidad) |
| `stock_disponible` | Float ≥ 0 |
| `estado_stock` | `OK` o `Sin Stock` |
| `observaciones` | Correcciones aplicadas |

**Familias de unidad** (determinan si una conversión es posible):
`MASA` (KG, G, LB) · `VOLUMEN` (L, ML) · `CONTEO` (UND) · `EMPAQUE` (CAJ, PAQ, BOL, BAN)

---

## 2. Limpieza automática

| Problema en el archivo | Corrección |
|---|---|
| Encabezado desplazado (títulos, fechas arriba) | Detecta la fila real de encabezado |
| Columnas con nombres distintos | Mapeo por alias (`SD`, `existencia`, `saldo`, `qty`…) |
| `Kilogram`, `kilos`, `KGS`, `kilogramoss` | Todo → `KG` |
| `1.250,75` / `1,234.50` / `(80)` | Parseo numérico latino y contable |
| Stock negativo `-15` | → `0` con estado **`Sin Stock`** |
| `N/A`, vacío, texto ilegible | → `0` con estado **`Sin Stock`** |
| `25.5` en unidades no fraccionables | Redondeo + advertencia |
| Filas `TOTAL` / `SUBTOTAL` | Descartadas |
| Duplicados producto+bodega+unidad | Eliminados |
| Unidad ausente | Se deduce del nombre (`Café 500 g` → `G`) |

Cada corrección queda registrada en `observaciones` y en la hoja `REPORTE_LIMPIEZA`.

---

## 3. Interpretación del dictado

```
"Arroz Doña Pepa, kilogramos, 25.5"
   ↓
producto = "Arroz Doña Pepa"   unidad = KG   cantidad = 25.5
```

Formatos aceptados:

- `Producto, unidad, cantidad` — formato oficial
- `Producto, cantidad, unidad` — orden invertido
- `Producto 25,5 kilos` — texto corrido
- `Azúcar Morena veinticinco kilos` — números en palabra
- `Harina, KGS, dos y medio` → `2.5`
- Separadores `,` o `;`

Robusto ante productos que contienen números o palabras-unidad
(`Aceite Girasol 1L`, `Café Molido 500 g`, `Leche Entera Bolsa`).
El emparejamiento contra el catálogo es difuso y sugiere alternativas si no acierta.

---

## 4. Verificación de unidad (CRÍTICO — regla de bloqueo)

Antes de cualquier cálculo se compara la unidad dictada contra la del catálogo.

**Familias incompatibles → BLOQUEO DURO.** El conteo no se registra:

```
Catálogo: Arroz Doña Pepa = KG    Dictado: 120 unidades

[X] BLOQUEADO
"El sistema registra este producto en kilogramos, pero reportaste
 unidades. ¿Puedes confirmar la cantidad en kilogramos?"
```

**Misma familia → conversión con confirmación:**

```
Catálogo: Pollo Entero = KG    Dictado: 500 gramos

[X] BLOQUEADO
"...500.0 G equivalen a 0.5 KG. ¿Confirmas?"
```

Con `--autoconvertir` la conversión kg↔g / l↔ml se aplica sin preguntar.
El bloqueo entre familias distintas **nunca** se salta.

---

## 5. Umbral de anomalía

```
Error = |(Conteo − SD) / SD|      para SD > 0
```

| Error | Severidad | Estado |
|---|---|---|
| < 10 % | NINGUNA | `OK` |
| 10–30 % | LEVE | `ALERTA` |
| 30–60 % | MEDIA | `ALERTA` |
| ≥ 60 % | ALTA | `REQUIERE_AUDITORIA` |
| SD = 0 y conteo > 0 | CRÍTICA | `REQUIERE_AUDITORIA` |

Los umbrales se ajustan en `core/validacion.py`.

---

## 6. Auditoría obligatoria

Escenarios: **1, 2 o 3 contadores + 1 auditor**. Reglas del sistema:

- El auditor **no puede** ser contador (segregación de funciones).
- Con varios contadores, se calcula el **consenso** (promedio) y la **dispersión**.
  Si difieren más de **5 %** → estado `RECONTEO` automático.
- **Ningún registro se cierra sin dictamen del auditor**: `APROBAR` / `RECHAZAR` / `RECONTEO`.
- El auditor puede aprobar con una cantidad ajustada distinta al consenso.
- La sesión no se cierra mientras haya un solo registro sin aprobar.

Ciclo de estados:

```
PENDIENTE_CONTEO → PENDIENTE_AUDITORIA → APROBADO
                 ↘ RECONTEO ↗              RECHAZADO
```

Toda acción queda en la **bitácora** (`sesion_XXXX.json`) con actor y marca de tiempo:
quién contó, cuánto, cuándo, quién auditó y con qué comentario.

---

## Integración

Los módulos del backend son importables directamente (import plano, sin
paquete) siempre que se ejecuten desde dentro de `backend/`:

```python
from limpieza import limpiar
from dictado import parsear, buscar_producto
from validacion import validar
from auditoria import SesionInventario

df, reporte = limpiar("catalogo.xlsx")
ses = SesionInventario("almacen general", ["Ana", "Luis"], "Carmen", df)

d = parsear("Arroz Doña Pepa, kilogramos, 25.5")
fila, score, alternativas = buscar_producto(d["producto"], df)
registro, resultado = ses.registrar_conteo("Ana", d, fila)

if resultado.estado == "BLOQUEADO":
    print(resultado.pregunta)     # se le muestra al contador

for r in ses.pendientes_auditoria():
    ses.auditar("Carmen", r["producto"], r["unidad"], "APROBAR")

ses.cerrar("Carmen")
ses.exportar("bitacora.json")
```

---

## Interfaz web

```bash
pip install -r backend/requirements.txt
python backend/server.py        # →  http://localhost:5000
```

El servidor sirve la plantilla y los estáticos desde `frontend/`
(`template_folder` / `static_folder` apuntan ahí en `backend/server.py`).

Cuatro pasos, en orden, con las pestañas siguientes bloqueadas hasta completar
la anterior:

1. **Datos** — arrastra el Excel o CSV. Muestra métricas de limpieza, el mapeo de
   columnas detectado, la lista de correcciones aplicadas y el catálogo
   normalizado. Botón para descargar el archivo limpio.
2. **Sesión** — bodega, contadores (1 a 3) y auditor. Rechaza abrir la sesión sin
   auditor o si el auditor figura también como contador.
3. **Conteo** — campo de dictado con **vista previa en vivo**: mientras escribes
   se muestra qué producto, unidad y cantidad interpretó el sistema y cuánto hay
   en el catálogo. Botón de dictado por voz donde el navegador lo permita
   (Chrome/Edge, `es-CO`). Si el producto no se encuentra, ofrece sugerencias
   pulsables. El turno rota automáticamente entre los contadores.
4. **Auditoría** — tarjetas ordenadas por severidad con todas las cifras
   (sistema, cada conteo, consenso, diferencia, error y dispersión) y los tres
   dictámenes. La insignia roja en la pestaña indica cuántos registros esperan
   dictamen.

### El bloqueo en pantalla

Cuando la unidad dictada no coincide con la del catálogo, el conteo **no se
registra**: aparece un panel rojo con la pregunta y un campo que solo acepta la
cantidad en la unidad correcta.

```
⛔ Conteo bloqueado · no se registró
El sistema registra este producto en kilogramos, pero reportaste
unidades. ¿Puedes confirmar la cantidad en kilogramos?

[ Cantidad en KG ]  [ Confirmar ]  [ Cancelar ]
```

### Notas de despliegue

El estado vive en memoria (`ESTADO` en `server.py`), suficiente para un turno de
inventario en un equipo. Para varios dispositivos simultáneos o para conservar
las sesiones tras un reinicio, reemplaza ese diccionario por Redis o una base de
datos, y sirve la aplicación con `gunicorn` en lugar del servidor de desarrollo.

## API

| Método | Ruta | Función |
|---|---|---|
| `POST` | `/api/cargar` | Sube y limpia (`modo`: `catalogo` o `bodegas`) |
| `POST` | `/api/interpretar` | Vista previa del dictado, sin registrar |
| `POST` | `/api/sesion` | Abre sesión |
| `GET` | `/api/sesion/<id>` | Registros, resumen y bitácora |
| `POST` | `/api/conteo` | Registra un conteo (o devuelve el bloqueo) |
| `POST` | `/api/auditar` | Dictamen: `APROBAR`, `RECHAZAR`, `RECONTEO` |
| `POST` | `/api/cerrar` | Cierra la sesión si todo está aprobado |
| `GET` | `/api/sesion/<id>/exportar` | Acta en Excel (resumen, conteos, bitácora) |
| `GET` | `/api/exportar/<catalogo\|bodegas>` | Datos limpios en Excel |
