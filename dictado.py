"""
Interpreta el dictado del contador.
Entrada:  "Arroz Doña Pepa, kilogramos, 25.5"
Salida:   {"producto": "Arroz Doña Pepa", "unidad": "KG", "cantidad": 25.5}
"""
import re
import unicodedata
from difflib import SequenceMatcher

from unidades import normalizar_unidad, SINONIMOS

NUM_PALABRA = {
    "cero": 0, "un": 1, "uno": 1, "una": 1, "dos": 2, "tres": 3, "cuatro": 4,
    "cinco": 5, "seis": 6, "siete": 7, "ocho": 8, "nueve": 9, "diez": 10,
    "once": 11, "doce": 12, "trece": 13, "catorce": 14, "quince": 15,
    "dieciseis": 16, "diecisiete": 17, "dieciocho": 18, "diecinueve": 19,
    "veinte": 20, "veinticinco": 25, "treinta": 30, "cuarenta": 40,
    "cincuenta": 50, "sesenta": 60, "setenta": 70, "ochenta": 80,
    "noventa": 90, "cien": 100, "ciento": 100, "doscientos": 200,
    "quinientos": 500, "mil": 1000,
}

FRACCION = {"medio": 0.5, "media": 0.5, "cuarto": 0.25, "tres cuartos": 0.75}

_TOKENS_UNIDAD = set()
for _l in SINONIMOS.values():
    _TOKENS_UNIDAD.update(_l)


def _slug(t):
    t = str(t or "").strip().lower()
    t = unicodedata.normalize("NFKD", t)
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", t).strip()


def _a_numero(txt):
    """'25.5' | '25,5' | 'veinticinco' | 'dos y medio' -> float"""
    s = _slug(txt)
    if not s:
        return None

    m = re.search(r"(\d+(?:[.,]\d+)?)", s)
    if m:
        base = float(m.group(1).replace(",", "."))
        if re.search(r"\b(y\s+)?(medio|media)\b", s):
            base += 0.5
        elif re.search(r"\b(y\s+)?cuarto\b", s):
            base += 0.25
        return base

    total, encontrado = 0.0, False
    for p in s.split():
        if p in NUM_PALABRA:
            total += NUM_PALABRA[p]
            encontrado = True

    for frase, val in FRACCION.items():
        if re.search(rf"\b{frase}\b", s):
            total += val
            encontrado = True
            break

    return total if encontrado else None


def _similar(a, b):
    return SequenceMatcher(None, _slug(a), _slug(b)).ratio()


def parsear(texto):
    """
    Devuelve dict con producto, unidad (código), unidad_dictada,
    cantidad y lista de errores. Tolera comas, 'de', 'x', o solo espacios.
    """
    res = {"texto_original": texto, "producto": None, "unidad": None,
           "unidad_dictada": None, "cantidad": None, "errores": []}
    if not texto or not str(texto).strip():
        res["errores"].append("Dictado vacío.")
        return res

    crudo = str(texto).strip()

    # --- Caso 1: separado por comas / punto y coma ---
    # Formato oficial: PRODUCTO, UNIDAD, CANTIDAD (la 1ª parte es SIEMPRE el producto,
    # aunque contenga números o palabras que parezcan unidades: "Aceite Girasol 1L").
    partes = [p.strip() for p in re.split(r"[;,](?!\d)", crudo) if p.strip()]
    if len(partes) == 3:
        res["producto"] = partes[0]
        u_cod, _, _ = normalizar_unidad(partes[1])
        n_val = _a_numero(partes[2])
        if u_cod is not None and n_val is not None:
            res["unidad"], res["unidad_dictada"] = u_cod, partes[1]
            res["cantidad"] = n_val
            return _validar(res)
        # Orden invertido: PRODUCTO, CANTIDAD, UNIDAD
        u_cod2, _, _ = normalizar_unidad(partes[2])
        n_val2 = _a_numero(partes[1])
        if u_cod2 is not None and n_val2 is not None:
            res["unidad"], res["unidad_dictada"] = u_cod2, partes[2]
            res["cantidad"] = n_val2
            return _validar(res)
        res["unidad"], res["unidad_dictada"] = u_cod, partes[1]
        res["cantidad"] = n_val
        return _validar(res)

    if len(partes) > 3:
        # Producto = todo menos las dos últimas partes
        res["producto"] = ", ".join(partes[:-2])
        u_cod, _, _ = normalizar_unidad(partes[-2])
        n_val = _a_numero(partes[-1])
        if u_cod is None:
            u_cod, _, _ = normalizar_unidad(partes[-1])
            n_val = _a_numero(partes[-2])
            res["unidad_dictada"] = partes[-1]
        else:
            res["unidad_dictada"] = partes[-2]
        res["unidad"], res["cantidad"] = u_cod, n_val
        return _validar(res)

    # --- Caso 2: texto corrido, "Producto <cantidad> <unidad>" ---
    palabras = crudo.split()
    slugs = [_slug(p) for p in palabras]

    idx_u = idx_n = None

    # Patrón fuerte al final: <número> <unidad>  ó  <unidad> <número>
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*([a-zA-ZáéíóúñÁÉÍÓÚÑ]+)\s*$", crudo)
    if m and normalizar_unidad(m.group(2))[0]:
        res["cantidad"] = float(m.group(1).replace(",", "."))
        res["unidad_dictada"] = m.group(2)
        res["unidad"] = normalizar_unidad(m.group(2))[0]
        res["producto"] = crudo[:m.start()].strip(" ,.-")
    else:
        m2 = re.search(r"([a-zA-ZáéíóúñÁÉÍÓÚÑ]+)\s+(\d+(?:[.,]\d+)?)\s*$", crudo)
        if m2 and normalizar_unidad(m2.group(1))[0]:
            res["unidad_dictada"] = m2.group(1)
            res["unidad"] = normalizar_unidad(m2.group(1))[0]
            res["cantidad"] = float(m2.group(2).replace(",", "."))
            res["producto"] = crudo[:m2.start()].strip(" ,.-")
        else:
            # Fallback: última palabra-unidad y último número del texto
            for i in range(len(slugs) - 1, -1, -1):
                if slugs[i] in _TOKENS_UNIDAD:
                    idx_u = i
                    break
            if idx_u is not None:
                res["unidad_dictada"] = palabras[idx_u]
                res["unidad"] = normalizar_unidad(palabras[idx_u])[0]

            nums = list(re.finditer(r"(\d+(?:[.,]\d+)?)", crudo))
            if nums:
                ult = nums[-1]
                res["cantidad"] = float(ult.group(1).replace(",", "."))
                pos = 0
                for i, p in enumerate(palabras):
                    if pos <= ult.start() < pos + len(p) + 1:
                        idx_n = i
                        break
                    pos += len(p) + 1
            else:
                for i, sl in enumerate(slugs):
                    if sl in NUM_PALABRA or sl in FRACCION:
                        res["cantidad"] = _a_numero(" ".join(slugs[i:]))
                        idx_n = i
                        break

            ignorar = {idx_u, idx_n} - {None}
            prod = " ".join(p for i, p in enumerate(palabras) if i not in ignorar)
            prod = re.sub(r"\b(de|del|x|por|en|son|hay|tengo|conte|conté)\b", " ",
                          prod, flags=re.I)
            res["producto"] = re.sub(r"\s+", " ", prod).strip(" ,.-")

    return _validar(res)


def _validar(res):
    if not res["producto"]:
        res["errores"].append("No se identificó el producto.")
    if res["cantidad"] is None:
        res["errores"].append("No se identificó la cantidad.")
    elif res["cantidad"] < 0:
        res["errores"].append("La cantidad no puede ser negativa.")
    if res["unidad"] is None:
        res["errores"].append(
            f"Unidad no reconocida: '{res.get('unidad_dictada') or 'ninguna'}'.")
    return res


def buscar_producto(nombre, catalogo, bodega=None, umbral=0.62):
    """
    Empareja el nombre dictado contra el catálogo limpio (DataFrame).
    Devuelve (fila | None, score, alternativas[:5]).
    """
    if catalogo is None or len(catalogo) == 0 or not nombre:
        return None, 0.0, []

    df = catalogo
    if bodega:
        f = df[df["bodega"].astype(str).str.lower() == str(bodega).lower()]
        if len(f):
            df = f

    objetivo = _slug(nombre)
    obj_alfa = re.sub(r"\d+", " ", objetivo)
    obj_alfa = re.sub(r"\s+", " ", obj_alfa).strip()

    puntajes = []
    for idx, fila in df.iterrows():
        cand = _slug(fila["producto"])
        cand_alfa = re.sub(r"\s+", " ", re.sub(r"\d+", " ", cand)).strip()

        sc = max(_similar(objetivo, cand), _similar(obj_alfa, cand_alfa))

        # bonus: todas las palabras significativas dictadas están en el candidato
        pal = [p for p in obj_alfa.split() if len(p) > 2]
        if pal and all(p in cand for p in pal):
            sc = max(sc, 0.90)
        # bonus por solapamiento de palabras clave
        cpal = {p for p in cand_alfa.split() if len(p) > 2}
        if pal and cpal:
            solape = len(set(pal) & cpal) / max(len(set(pal)), len(cpal))
            sc = max(sc, solape * 0.95)
        if objetivo == cand:
            sc = 1.0
        puntajes.append((sc, idx))

    puntajes.sort(reverse=True, key=lambda x: x[0])
    alternativas = [{"producto": df.loc[i, "producto"],
                     "unidad": df.loc[i, "unidad"],
                     "bodega": df.loc[i, "bodega"],
                     "stock_disponible": float(df.loc[i, "stock_disponible"]),
                     "score": round(s, 3)}
                    for s, i in puntajes[:5]]

    if puntajes and puntajes[0][0] >= umbral:
        return df.loc[puntajes[0][1]], round(puntajes[0][0], 3), alternativas
    return None, (round(puntajes[0][0], 3) if puntajes else 0.0), alternativas
