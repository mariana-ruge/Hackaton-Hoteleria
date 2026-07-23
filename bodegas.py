"""
Limpieza del maestro de BODEGAS.
Normaliza nombres, expande abreviaturas ('caf.' -> 'cafeteria', 'rest.' ->
'restaurante') y detecta duplicados por similitud.
"""
import re
import unicodedata
from difflib import SequenceMatcher

import pandas as pd

from limpieza import cargar_archivo, _slug, _detectar_fila_encabezado

ABREVIATURAS = {
    r"\bcaf\.?\b": "cafeteria",
    r"\brest\.?\b": "restaurante",
    r"\bsumin\.?\b": "suministros",
    r"\balm\.?\b": "almacen",
    r"\bautoserv\.?\b": "autoservicio",
    r"\bmov\.?\b": "movil",
    r"\bkio\.?\b": "kiosco",
    r"\btda\.?\b": "tienda",
}

# Errores de digitación observados
TIPOGRAFICOS = {
    "paqueadero": "parqueadero",
    "pisciloca": "piscilago",
    "autoservicios": "autoservicio",
    "bosques": "bosque",
}


def normalizar_nombre(nombre):
    s = str(nombre or "").strip().lower()
    s = re.sub(r"\s+", " ", s)
    for patron, rep in ABREVIATURAS.items():
        s = re.sub(patron, rep, s)
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    palabras = [TIPOGRAFICOS.get(p, p) for p in s.split()]
    return " ".join(palabras)


def _clave(nombre):
    """Clave canónica: sin tildes y con palabras ordenadas (detecta reordenamientos)."""
    s = normalizar_nombre(nombre)
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return " ".join(sorted(s.split()))


def limpiar_bodegas(ruta, hoja=0, umbral_similitud=0.86):
    """Devuelve (df_bodegas, reporte) con duplicados y variantes agrupados."""
    rep = {"filas_origen": 0, "bodegas_unicas": 0, "duplicados_exactos": 0,
           "posibles_duplicados": [], "correcciones": []}

    crudo = cargar_archivo(ruta, hoja)
    rep["filas_origen"] = len(crudo)

    idx = _detectar_fila_encabezado(crudo)
    if idx is not None:
        df = crudo.iloc[idx + 1:].copy()
        df.columns = [str(c) for c in crudo.iloc[idx].tolist()]
    else:
        df = crudo.copy()
        df.columns = [f"col_{i}" for i in range(df.shape[1])]

    df = df.dropna(axis=1, how="all").dropna(axis=0, how="all")

    # Columna con los nombres = la de mayor cantidad de texto no numérico
    col_nom, mejor = None, -1
    for c in df.columns:
        vals = df[c].dropna().astype(str)
        txt = sum(1 for v in vals if not v.replace(".", "").strip().isdigit()
                  and len(v.strip()) > 3)
        if txt > mejor:
            col_nom, mejor = c, txt
    if col_nom is None:
        return pd.DataFrame(columns=["id", "bodega", "bodega_original",
                                     "variantes"]), rep

    vistos, filas = {}, []
    for v in df[col_nom].dropna().astype(str):
        orig = v.strip()
        if not orig or orig.lower() == "nan":
            continue
        limpio = normalizar_nombre(orig)
        if not limpio:
            continue
        if limpio != _slug(orig):
            rep["correcciones"].append({"original": orig, "normalizado": limpio})

        k = _clave(limpio)
        if k in vistos:
            rep["duplicados_exactos"] += 1
            vistos[k]["variantes"].append(orig)
            continue
        reg = {"bodega": limpio, "bodega_original": orig, "variantes": []}
        vistos[k] = reg
        filas.append(reg)

    # Posibles duplicados por similitud (nombres parecidos, no idénticos)
    nombres = [f["bodega"] for f in filas]
    for i in range(len(nombres)):
        for j in range(i + 1, len(nombres)):
            r = SequenceMatcher(None, nombres[i], nombres[j]).ratio()
            if umbral_similitud <= r < 1.0:
                rep["posibles_duplicados"].append(
                    {"a": nombres[i], "b": nombres[j], "similitud": round(r, 3)})

    out = pd.DataFrame(filas)
    if len(out):
        out = out.sort_values("bodega").reset_index(drop=True)
        out.insert(0, "id", range(1, len(out) + 1))
        out["variantes"] = out["variantes"].apply(
            lambda v: " | ".join(v) if v else "")
    rep["bodegas_unicas"] = len(out)
    return out, rep
