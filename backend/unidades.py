"""
Normalización y verificación de unidades de medida.
Formato canónico único para todo el sistema.
"""
import re
import unicodedata

# ---------- Formato canónico ----------
# Toda unidad del sistema se guarda SIEMPRE con este código.
UNIDADES = {
    "KG":  {"nombre": "Kilogramo",   "familia": "MASA",     "factor_base": 1000.0},   # base: gramo
    "G":   {"nombre": "Gramo",       "familia": "MASA",     "factor_base": 1.0},
    "LB":  {"nombre": "Libra",       "familia": "MASA",     "factor_base": 453.592},
    "L":   {"nombre": "Litro",       "familia": "VOLUMEN",  "factor_base": 1000.0},   # base: mililitro
    "ML":  {"nombre": "Mililitro",   "familia": "VOLUMEN",  "factor_base": 1.0},
    "UND": {"nombre": "Unidad",      "familia": "CONTEO",   "factor_base": 1.0},      # base: unidad
    "CAJ": {"nombre": "Caja",        "familia": "EMPAQUE",  "factor_base": 1.0},
    "PAQ": {"nombre": "Paquete",     "familia": "EMPAQUE",  "factor_base": 1.0},
    "BOL": {"nombre": "Bolsa",       "familia": "EMPAQUE",  "factor_base": 1.0},
    "BAN": {"nombre": "Bandeja",     "familia": "EMPAQUE",  "factor_base": 1.0},
}

# Sinónimos -> código canónico. Cubre voz, inglés, plurales, abreviaturas, errores de dictado.
SINONIMOS = {
    "KG": ["kg", "kgs", "kilo", "kilos", "kilogramo", "kilogramos", "kilogram",
           "kilograms", "kilogramme", "k g", "kilogramoss", "kgr", "kilo gramos"],
    "G":  ["g", "gr", "grs", "gramo", "gramos", "gram", "grams", "gramoss"],
    "LB": ["lb", "lbs", "libra", "libras", "pound", "pounds"],
    "L":  ["l", "lt", "lts", "litro", "litros", "liter", "liters", "litre", "litros."],
    "ML": ["ml", "mls", "mililitro", "mililitros", "milliliter", "milliliters", "cc"],
    "UND": ["u", "un", "und", "unds", "uds", "ud", "unidad", "unidades", "unit", "units",
            "each", "ea", "pieza", "piezas", "pza", "pzas", "item", "items"],
    "CAJ": ["caja", "cajas", "cj", "cjs", "box", "boxes", "cx"],
    "PAQ": ["paq", "paquete", "paquetes", "pack", "packs", "pkt"],
    "BOL": ["bol", "bolsa", "bolsas", "bag", "bags"],
    "BAN": ["ban", "bandeja", "bandejas", "tray", "trays"],
}

_LOOKUP = {}
for _cod, _lista in SINONIMOS.items():
    _LOOKUP[_cod.lower()] = _cod
    for _s in _lista:
        _LOOKUP[_s] = _cod


def _slug(texto: str) -> str:
    """minúsculas, sin tildes, sin puntuación, espacios colapsados"""
    if texto is None:
        return ""
    t = str(texto).strip().lower()
    t = unicodedata.normalize("NFKD", t)
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = re.sub(r"[^\w\s]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def normalizar_unidad(texto):
    """
    Devuelve (codigo_canonico | None, confianza 0-1, motivo).
    Acepta 'Kilogram', 'kilos', 'KGS', 'unidades', 'Cajas', etc.
    """
    s = _slug(texto)
    if not s:
        return None, 0.0, "vacio"

    if s in _LOOKUP:
        return _LOOKUP[s], 1.0, "exacto"

    # sin espacios
    s2 = s.replace(" ", "")
    if s2 in _LOOKUP:
        return _LOOKUP[s2], 0.95, "exacto_sin_espacios"

    # token por token (ej: "en kilos", "x caja")
    for tok in s.split():
        if tok in _LOOKUP:
            return _LOOKUP[tok], 0.85, f"token:{tok}"

    # prefijo/aproximación (ej: "kilogr", "unidad3s")
    mejor, score = None, 0.0
    for clave, cod in _LOOKUP.items():
        if len(clave) < 3:
            continue
        if s.startswith(clave) or clave.startswith(s):
            r = min(len(clave), len(s)) / max(len(clave), len(s))
            if r > score:
                mejor, score = cod, r
    if mejor and score >= 0.6:
        return mejor, round(0.6 * score, 2), "aproximado"

    return None, 0.0, "desconocido"


def familia(cod):
    return UNIDADES.get(cod, {}).get("familia")


def nombre(cod):
    return UNIDADES.get(cod, {}).get("nombre", cod or "N/D")


_PLURAL = {"KG": "kilogramos", "G": "gramos", "LB": "libras", "L": "litros",
           "ML": "mililitros", "UND": "unidades", "CAJ": "cajas",
           "PAQ": "paquetes", "BOL": "bolsas", "BAN": "bandejas"}


def plural(cod):
    """Nombre en plural, en minúscula, para mensajes al usuario."""
    return _PLURAL.get(cod, (nombre(cod) or "unidades").lower())


def convertible(cod_a, cod_b):
    """True si pertenecen a la misma familia física (kg<->g, l<->ml)."""
    fa, fb = familia(cod_a), familia(cod_b)
    return fa is not None and fa == fb and fa in ("MASA", "VOLUMEN")


def convertir(cantidad, desde, hacia):
    """Convierte dentro de la misma familia. Lanza ValueError si no aplica."""
    if desde == hacia:
        return float(cantidad)
    if not convertible(desde, hacia):
        raise ValueError(f"No convertible: {desde} -> {hacia}")
    base = float(cantidad) * UNIDADES[desde]["factor_base"]
    return base / UNIDADES[hacia]["factor_base"]
