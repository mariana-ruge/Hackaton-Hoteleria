"""
Carga y limpieza de catálogos (Excel / CSV).
Detecta encabezados reales, mapea columnas, normaliza unidades,
corrige negativos -> "Sin Stock", y deja TODO en un formato único.
"""
import os
import re
import unicodedata
import pandas as pd

from unidades import normalizar_unidad, nombre as nombre_unidad, UNIDADES

# ---------- Esquema canónico de salida ----------
COLUMNAS = [
    "codigo",           # str
    "producto",         # str
    "bodega",           # str
    "unidad",           # código canónico (KG, G, UND, ...)
    "unidad_original",  # lo que venía en el archivo
    "stock_disponible", # float >= 0
    "estado_stock",     # "OK" | "Sin Stock"
    "observaciones",    # notas de limpieza
    "score_coherencia", # score estadístico de coherencia
    "clasificacion_coherencia",
    "detalle_coherencia",
]

ALIAS = {
    "codigo": ["codigo", "cod", "sku", "id", "item", "referencia", "ref",
               "codigo producto", "cod producto", "codigo_item"],
    "producto": ["producto", "descripcion", "nombre", "articulo", "item",
                 "detalle", "material", "descripcion producto", "nombre producto"],
    "bodega": ["bodega", "bodegas", "almacen", "almacenes", "deposito",
               "ubicacion", "centro", "warehouse", "sede", "punto"],
    "unidad": ["unidad", "unidades", "unidad medida", "unidad de medida", "um",
               "u m", "uom", "medida", "unit", "unit of measure", "presentacion"],
    "stock_disponible": ["stock disponible", "stock", "sd", "existencia",
                         "existencias", "saldo", "cantidad", "cant", "disponible",
                         "inventario", "qty", "quantity", "on hand", "stock actual"],
}


def es_excel(ruta):
    return str(ruta).lower().endswith((".xlsx", ".xlsm", ".xls", ".xltx"))


def _slug(t):
    if t is None:
        return ""
    t = str(t).strip().lower()
    t = unicodedata.normalize("NFKD", t)
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = re.sub(r"[^\w\s]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _a_numero(valor):
    """
    Convierte texto a float tolerando formatos latinos y basura.
    '1.234,50' -> 1234.5 | '25,5' -> 25.5 | '(30)' -> -30 | 'N/A' -> None
    """
    if valor is None:
        return None
    if isinstance(valor, (int, float)) and not isinstance(valor, bool):
        return None if pd.isna(valor) else float(valor)

    s = str(valor).strip()
    if not s or _slug(s) in ("na", "n a", "nd", "n d", "null", "none", "sin dato",
                             "sin stock", "s d", "-", "--"):
        return None

    negativo = s.startswith("(") and s.endswith(")")
    s = s.strip("()")
    s = re.sub(r"[^\d,.\-]", "", s)
    if not s:
        return None

    # Decide separador decimal
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")   # 1.234,50
        else:
            s = s.replace(",", "")                      # 1,234.50
    elif "," in s:
        entero, _, dec = s.rpartition(",")
        s = s.replace(",", "." if len(dec) in (1, 2) else "")
    elif s.count(".") > 1:
        s = s.replace(".", "")

    try:
        n = float(s)
    except ValueError:
        return None
    return -n if negativo else n


def _detectar_fila_encabezado(df_crudo, max_filas=15):
    """Busca la fila que más se parece a un encabezado conocido."""
    mejor_idx, mejor_puntaje = None, 0
    todos = {a for lista in ALIAS.values() for a in lista}
    for i in range(min(max_filas, len(df_crudo))):
        fila = [_slug(v) for v in df_crudo.iloc[i].tolist()]
        puntaje = sum(1 for c in fila if c and c in todos)
        if puntaje > mejor_puntaje:
            mejor_idx, mejor_puntaje = i, puntaje
    return mejor_idx if mejor_puntaje >= 2 else None


def _mapear_columnas(cols):
    """columna_original -> campo canónico"""
    mapa, usados, detalles = {}, set(), []
    slugs = {c: _slug(c) for c in cols}
    # 1) coincidencia exacta
    for campo, alias in ALIAS.items():
        for c, s in slugs.items():
            if c in mapa or campo in usados:
                continue
            if s in alias:
                mapa[c] = campo
                usados.add(campo)
                if s != campo:
                    detalles.append({
                        "original": str(c),
                        "detectado": campo,
                        "tipo": "exacta_normalizada",
                        "motivo": f"Se reconoció '{c}' como '{campo}'.",
                    })
                break
    # 2) coincidencia parcial
    for campo, alias in ALIAS.items():
        if campo in usados:
            continue
        for c, s in slugs.items():
            if c in mapa or not s:
                continue
            if any(a in s or s in a for a in alias if len(a) > 3):
                mapa[c] = campo
                usados.add(campo)
                detalles.append({
                    "original": str(c),
                    "detectado": campo,
                    "tipo": "parcial",
                    "motivo": f"Columna ambigua '{c}' interpretada como '{campo}'.",
                })
                break
    return mapa, detalles


def _codigo_texto(valor):
    if valor is None:
        return ""
    texto = str(valor).strip()
    return "" if texto.lower() == "nan" else texto


def _anotar_coherencia(df):
    if df is None or not len(df):
        return df, {"registros_analizados": 0, "alertas": 0, "criticos": 0, "detalle": []}

    out = df.copy()
    out["score_coherencia"] = 0.0
    out["clasificacion_coherencia"] = "OK"
    out["detalle_coherencia"] = "Sin alertas estadísticas."
    detalle = []

    grupos = out.groupby(["codigo", "unidad"], dropna=False)
    for (codigo, unidad), idx in grupos.groups.items():
        subset = out.loc[idx]
        vals = subset["stock_disponible"].astype(float)
        if len(vals) < 3:
            continue
        mediana = float(vals.median())
        q1 = float(vals.quantile(0.25))
        q3 = float(vals.quantile(0.75))
        iqr = q3 - q1
        mad = float((vals - mediana).abs().median())
        for row_idx, valor in vals.items():
            score_iqr = abs(valor - mediana) / max(iqr, 1.0)
            score_mad = abs(valor - mediana) / max(mad * 1.4826, 1.0)
            score = round(max(score_iqr, score_mad), 3)
            if score >= 6:
                cls = "CRITICO"
            elif score >= 3:
                cls = "ALERTA"
            else:
                cls = "OK"
            if cls == "OK":
                continue
            texto = (f"Stock atípico frente al histórico cargado del mismo artículo "
                     f"({codigo or 'sin código'}) en otras bodegas: mediana {round(mediana, 4)}, "
                     f"IQR {round(iqr, 4)}, score {score}.")
            out.at[row_idx, "score_coherencia"] = score
            out.at[row_idx, "clasificacion_coherencia"] = cls
            out.at[row_idx, "detalle_coherencia"] = texto
            if out.at[row_idx, "observaciones"]:
                out.at[row_idx, "observaciones"] = out.at[row_idx, "observaciones"] + " | " + texto
            else:
                out.at[row_idx, "observaciones"] = texto
            detalle.append({
                "codigo": codigo,
                "producto": out.at[row_idx, "producto"],
                "bodega": out.at[row_idx, "bodega"],
                "unidad": unidad,
                "stock_disponible": float(valor),
                "score": score,
                "clasificacion": cls,
                "detalle": texto,
            })

    resumen = {
        "registros_analizados": int(len(out)),
        "alertas": int((out["clasificacion_coherencia"] == "ALERTA").sum()),
        "criticos": int((out["clasificacion_coherencia"] == "CRITICO").sum()),
        "detalle": detalle,
    }
    return out, resumen


def cargar_archivo(ruta, hoja=0):
    """Lee Excel o CSV crudo, sin encabezado, tolerando separadores/encodings."""
    if es_excel(ruta):
        return pd.read_excel(ruta, sheet_name=hoja, header=None, dtype=object)

    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        for sep in (None, ",", ";", "\t", "|"):
            try:
                df = pd.read_csv(ruta, header=None, dtype=object, encoding=enc,
                                 sep=sep, engine="python", on_bad_lines="skip")
                if df.shape[1] >= 2:
                    return df
            except Exception:
                continue
    raise ValueError(f"No se pudo leer el archivo: {ruta}")


def limpiar(ruta, hoja=0, permitir_pendientes_por_codigo=False):
    """
    Devuelve (df_limpio, reporte).
    df_limpio siempre tiene exactamente las COLUMNAS canónicas.
    """
    rep = {"archivo": str(ruta), "filas_origen": 0, "filas_final": 0,
           "descartadas": 0, "negativos_corregidos": 0, "unidades_corregidas": 0,
            "unidades_desconocidas": 0, "duplicados": 0, "columnas_detectadas": {},
            "columnas_confusas": [], "advertencias": [], "valores_negativos": [],
            "articulos_completados": [], "articulos_no_encontrados": [],
               "filas_pendientes_articulo": [], "filas_descartadas": [],
               "duplicados_detalle": []}

    crudo = cargar_archivo(ruta, hoja)
    rep["filas_origen"] = len(crudo)

    # --- Encabezado ---
    idx = _detectar_fila_encabezado(crudo)
    if idx is None:
        crudo.columns = [f"col_{i}" for i in range(crudo.shape[1])]
        df = crudo
        rep["advertencias"].append(
            "No se detectó fila de encabezado; se usaron nombres genéricos.")
    else:
        df = crudo.iloc[idx + 1:].copy()
        df.columns = [str(c) if c is not None and str(c) != "nan" else f"col_{i}"
                      for i, c in enumerate(crudo.iloc[idx].tolist())]

    # Elimina columnas y filas totalmente vacías (típico de exportaciones Excel)
    df = df.dropna(axis=1, how="all").dropna(axis=0, how="all")
    df = df.loc[:, [c for c in df.columns
                    if not df[c].astype(str).str.strip().replace("nan", "").eq("").all()]]

    mapa, detalles_columnas = _mapear_columnas(list(df.columns))
    rep["columnas_detectadas"] = mapa
    rep["columnas_confusas"] = detalles_columnas
    for req in ("producto", "unidad", "stock_disponible"):
        if req not in mapa.values():
            rep["advertencias"].append(f"Falta columna '{req}' en el archivo.")

    df = df.rename(columns=mapa)
    for col in COLUMNAS:
        if col not in df.columns:
            df[col] = None

    salida = []
    for _, fila in df.iterrows():
        obs = []
        codigo_txt = _codigo_texto(fila.get("codigo"))
        bodega_txt = re.sub(r"\s+", " ", str(fila.get("bodega") or "").strip())
        unidad_raw = "" if fila.get("unidad") is None else str(fila.get("unidad")).strip()

        prod = str(fila.get("producto") or "").strip()
        prod = re.sub(r"\s+", " ", prod)
        if not prod or prod.lower() == "nan":
            if permitir_pendientes_por_codigo and codigo_txt:
                rep["filas_pendientes_articulo"].append({
                    "codigo": codigo_txt,
                    "bodega": bodega_txt,
                    "unidad_original": unidad_raw,
                    "stock_original": fila.get("stock_disponible"),
                    "causa": "Producto vacío en la hoja origen; se intentará completar con otra hoja.",
                })
                continue
            rep["filas_descartadas"].append({
                "codigo": codigo_txt,
                "producto": prod,
                "bodega": bodega_txt,
                "causa": "Fila descartada porque no tiene producto identificable.",
            })
            rep["descartadas"] += 1
            continue
        # descarta filas de totales/subtotales
        if _slug(prod) in ("total", "totales", "subtotal", "suma", "gran total"):
            rep["filas_descartadas"].append({
                "codigo": codigo_txt,
                "producto": prod,
                "bodega": bodega_txt,
                "causa": "Fila descartada por ser total o subtotal, no un producto.",
            })
            rep["descartadas"] += 1
            continue
        prod = prod.title() if prod.isupper() or prod.islower() else prod

        # --- Unidad ---
        u_orig = fila.get("unidad")
        cod, conf, motivo = normalizar_unidad(u_orig)
        if cod is None:
            # Intenta deducirla del nombre del producto ("Arroz x 500 g")
            m = re.search(r"\b(\d+(?:[.,]\d+)?)\s*(kg|kilos?|g|gr|gramos?|ml|l|litros?|und|unidades?)\b",
                          _slug(prod))
            if m:
                cod, conf, motivo = normalizar_unidad(m.group(2))
                if cod:
                    obs.append(f"Unidad deducida del nombre del producto ({motivo}).")
            if cod is None:
                cod = "UND"
                obs.append(f"Unidad '{u_orig}' no reconocida; se asignó UND por defecto.")
                rep["unidades_desconocidas"] += 1
        elif conf < 1.0:
            obs.append(f"Unidad normalizada '{u_orig}' -> {cod} ({motivo}).")
            rep["unidades_corregidas"] += 1
        elif str(u_orig).strip() != cod:
            rep["unidades_corregidas"] += 1

        # --- Stock ---
        sd = _a_numero(fila.get("stock_disponible"))
        if sd is None:
            sd, estado = 0.0, "Sin Stock"
            obs.append("Stock ilegible o vacío; se registró como Sin Stock.")
        elif sd < 0:
            causa = "Valor negativo eliminado del catálogo; no se admite stock negativo."
            rep["valores_negativos"].append({
                "codigo": codigo_txt,
                "producto": prod,
                "bodega": bodega_txt,
                "valor_original": sd,
                "causa": causa,
            })
            rep["negativos_corregidos"] += 1
            rep["descartadas"] += 1
            continue
        elif sd == 0:
            estado = "Sin Stock"
        else:
            estado = "OK"

        # Enteros para unidades no fraccionables
        if cod in ("UND", "CAJ", "PAQ", "BOL", "BAN") and sd != int(sd):
            obs.append(f"Cantidad decimal ({sd}) redondeada: {cod} no admite fracciones.")
            sd = float(round(sd))

        salida.append({
            "codigo": codigo_txt,
            "producto": prod,
            "bodega": bodega_txt,
            "unidad": cod,
            "unidad_original": ("" if fila.get("unidad") is None
                                else str(fila.get("unidad")).strip()),
            "stock_disponible": round(sd, 4),
            "estado_stock": estado,
            "observaciones": " | ".join(obs),
            "score_coherencia": 0.0,
            "clasificacion_coherencia": "OK",
            "detalle_coherencia": "Sin alertas estadísticas.",
        })

    limpio = pd.DataFrame(salida, columns=COLUMNAS)

    # Duplicados exactos producto+bodega+unidad
    if len(limpio):
        duplicados_mask = limpio.duplicated(subset=["codigo", "producto", "bodega", "unidad"],
                                            keep="first")
        if duplicados_mask.any():
            rep["duplicados_detalle"] = [
                {
                    "codigo": _codigo_texto(row.get("codigo")),
                    "producto": str(row.get("producto") or ""),
                    "bodega": str(row.get("bodega") or ""),
                    "unidad": str(row.get("unidad") or ""),
                    "causa": "Registro duplicado exacto; se conservó la primera aparición.",
                }
                for _, row in limpio[duplicados_mask].iterrows()
            ]
        antes = len(limpio)
        limpio = limpio.drop_duplicates(subset=["codigo", "producto", "bodega", "unidad"],
                                        keep="first")
        rep["duplicados"] = antes - len(limpio)

    limpio = limpio.reset_index(drop=True)
    limpio, coherencia = _anotar_coherencia(limpio)
    rep["filas_final"] = len(limpio)
    rep["coherencia_modelo"] = coherencia
    return limpio, rep


def limpiar_libro(ruta, hoja_bodegas=0):
    """
    Procesa un libro completo de Excel donde la primera hoja suele ser el maestro
    de bodegas y las hojas restantes contienen inventario por bodega.
    """
    if not es_excel(ruta):
        return limpiar(ruta, hoja=0)

    libro = pd.ExcelFile(ruta)
    hojas = list(libro.sheet_names)
    if len(hojas) <= 1:
        return limpiar(ruta, hoja=hojas[0] if hojas else 0)

    rep_total = {
        "archivo": str(ruta),
        "filas_origen": 0,
        "filas_final": 0,
        "descartadas": 0,
        "negativos_corregidos": 0,
        "unidades_corregidas": 0,
        "unidades_desconocidas": 0,
        "duplicados": 0,
        "columnas_detectadas": {},
        "columnas_confusas": [],
        "advertencias": [],
        "hoja_bodegas": hojas[hoja_bodegas] if isinstance(hoja_bodegas, int)
                         and 0 <= hoja_bodegas < len(hojas) else str(hoja_bodegas),
        "hojas_catalogo": [],
        "hojas_sin_datos": [],
        "valores_negativos": [],
        "articulos_completados": [],
        "articulos_no_encontrados": [],
        "filas_descartadas": [],
        "duplicados_detalle": [],
        "coherencia_modelo": {"registros_analizados": 0, "alertas": 0, "criticos": 0, "detalle": []},
    }

    catalogos = []
    pendientes = []
    for idx, hoja in enumerate(hojas):
        if hoja == hoja_bodegas or idx == hoja_bodegas:
            continue

        df, rep = limpiar(ruta, hoja=hoja, permitir_pendientes_por_codigo=True)
        rep_total["hojas_catalogo"].append(hoja)
        rep_total["filas_origen"] += rep["filas_origen"]
        rep_total["descartadas"] += rep["descartadas"]
        rep_total["negativos_corregidos"] += rep["negativos_corregidos"]
        rep_total["unidades_corregidas"] += rep["unidades_corregidas"]
        rep_total["unidades_desconocidas"] += rep["unidades_desconocidas"]
        rep_total["duplicados"] += rep["duplicados"]
        rep_total["valores_negativos"].extend(rep["valores_negativos"])
        rep_total["filas_descartadas"].extend(
            [{**d, "hoja": hoja} for d in rep["filas_descartadas"]]
        )
        rep_total["duplicados_detalle"].extend(
            [{**d, "hoja": hoja} for d in rep["duplicados_detalle"]]
        )
        rep_total["columnas_confusas"].extend(
            [{**d, "hoja": hoja} for d in rep["columnas_confusas"]]
        )
        rep_total["coherencia_modelo"]["detalle"].extend(rep.get("coherencia_modelo", {}).get("detalle", []))
        pendientes.extend([{**p, "hoja": hoja} for p in rep["filas_pendientes_articulo"]])

        for col, canon in rep["columnas_detectadas"].items():
            rep_total["columnas_detectadas"][f"{hoja}::{col}"] = canon

        for advertencia in rep["advertencias"]:
            rep_total["advertencias"].append(f"[{hoja}] {advertencia}")

        if not len(df):
            rep_total["hojas_sin_datos"].append(hoja)
            continue

        nombre_bodega = re.sub(r"\s+", " ", str(hoja).strip())
        vacias = df["bodega"].fillna("").astype(str).str.strip().eq("")
        if vacias.any():
            df.loc[vacias, "bodega"] = nombre_bodega
            df.loc[vacias, "observaciones"] = df.loc[vacias, "observaciones"].apply(
                lambda obs: " | ".join([x for x in [obs, f"Bodega inferida de la hoja '{nombre_bodega}'."] if x])
            )

        catalogos.append(df)

    referencias = {}
    for df in catalogos:
        for _, fila in df.iterrows():
            codigo = _codigo_texto(fila.get("codigo"))
            if not codigo:
                continue
            ref = referencias.setdefault(codigo, {})
            if str(fila.get("producto") or "").strip() and not ref.get("producto"):
                ref["producto"] = fila["producto"]
            if str(fila.get("unidad") or "").strip() and not ref.get("unidad"):
                ref["unidad"] = fila["unidad"]
            if str(fila.get("unidad_original") or "").strip() and not ref.get("unidad_original"):
                ref["unidad_original"] = fila["unidad_original"]

    completados = []
    for pendiente in pendientes:
        codigo = pendiente["codigo"]
        ref = referencias.get(codigo)
        if not ref or not ref.get("producto"):
            rep_total["articulos_no_encontrados"].append({
                "codigo": codigo,
                "hoja": pendiente["hoja"],
                "bodega": pendiente["bodega"],
                "causa": "No se encontró ese número de artículo en otra hoja del libro.",
            })
            rep_total["filas_descartadas"].append({
                "codigo": codigo,
                "producto": "",
                "bodega": pendiente["bodega"],
                "hoja": pendiente["hoja"],
                "causa": "Fila descartada porque el número de artículo no apareció en ninguna otra hoja.",
            })
            rep_total["descartadas"] += 1
            continue

        nombre_bodega = pendiente["bodega"] or re.sub(r"\s+", " ", str(pendiente["hoja"]).strip())
        cod_unidad, _, _ = normalizar_unidad(pendiente.get("unidad_original") or ref.get("unidad_original"))
        if cod_unidad is None:
            cod_unidad = ref.get("unidad") or "UND"
        sd = _a_numero(pendiente.get("stock_original"))
        if sd is None:
            sd, estado = 0.0, "Sin Stock"
            observacion = "Artículo completado desde otra hoja; stock vacío o ilegible, se registró como Sin Stock."
        elif sd < 0:
            rep_total["valores_negativos"].append({
                "codigo": codigo,
                "producto": ref.get("producto", ""),
                "bodega": nombre_bodega,
                "valor_original": sd,
                "causa": "Valor negativo eliminado al completar artículo desde otra hoja.",
            })
            rep_total["negativos_corregidos"] += 1
            rep_total["descartadas"] += 1
            continue
        else:
            estado = "Sin Stock" if sd == 0 else "OK"
            observacion = f"Artículo {codigo} completado con datos encontrados en la hoja '{pendiente['hoja']}'."

        completados.append({
            "codigo": codigo,
            "producto": ref.get("producto", ""),
            "bodega": nombre_bodega,
            "unidad": cod_unidad,
            "unidad_original": pendiente.get("unidad_original") or ref.get("unidad_original") or cod_unidad,
            "stock_disponible": round(sd, 4),
            "estado_stock": estado,
            "observaciones": observacion,
        })
        rep_total["articulos_completados"].append({
            "codigo": codigo,
            "hoja": pendiente["hoja"],
            "bodega": nombre_bodega,
            "producto": ref.get("producto", ""),
            "detalle": observacion,
        })

    if completados:
        catalogos.append(pd.DataFrame(completados, columns=COLUMNAS))

    if not catalogos:
        rep_total["advertencias"].append(
            "No se encontraron hojas de catálogo válidas fuera de la hoja de bodegas."
        )
        return pd.DataFrame(columns=COLUMNAS), rep_total

    limpio = pd.concat(catalogos, ignore_index=True)
    duplicados_mask = limpio.duplicated(subset=["codigo", "producto", "bodega", "unidad"],
                                        keep="first")
    if duplicados_mask.any():
        rep_total["duplicados_detalle"].extend([
            {
                "codigo": _codigo_texto(row.get("codigo")),
                "producto": str(row.get("producto") or ""),
                "bodega": str(row.get("bodega") or ""),
                "unidad": str(row.get("unidad") or ""),
                "causa": "Registro duplicado exacto entre hojas; se conservó la primera aparición.",
            }
            for _, row in limpio[duplicados_mask].iterrows()
        ])
    antes = len(limpio)
    limpio = limpio.drop_duplicates(subset=["codigo", "producto", "bodega", "unidad"],
                                    keep="first")
    rep_total["duplicados"] += antes - len(limpio)
    limpio = limpio.reset_index(drop=True)
    limpio, coherencia = _anotar_coherencia(limpio)
    rep_total["filas_final"] = len(limpio)
    rep_total["coherencia_modelo"] = coherencia
    rep_total["advertencias"].insert(
        0,
        f"Se procesó el libro completo: hoja de bodegas '{rep_total['hoja_bodegas']}' y {len(rep_total['hojas_catalogo'])} hoja(s) de catálogo."
    )
    return limpio, rep_total
