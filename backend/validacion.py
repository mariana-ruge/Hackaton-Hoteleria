"""
Motor de validación de conteos.
Reglas:
  R1 BLOQUEO por discrepancia de unidad (familias incompatibles).
  R2 CONVERSIÓN sugerida cuando la unidad es de la misma familia (kg <-> g).
  R3 ANOMALÍA por umbral: Error = |(Conteo - SD) / SD|, con SD > 0.
"""
from dataclasses import dataclass, field, asdict

from unidades import (normalizar_unidad, convertible, convertir,
                       familia, nombre as nombre_unidad, plural)

# Umbrales de anomalía por severidad (diferencia porcentual)
UMBRAL_LEVE = 0.10    # 10 %  -> aviso
UMBRAL_MEDIO = 0.30   # 30 %  -> requiere revisión
UMBRAL_ALTO = 0.60    # 60 %  -> requiere auditoría obligatoria

# Estados
OK = "OK"
BLOQUEADO = "BLOQUEADO"
ALERTA = "ALERTA"
REQUIERE_AUDITORIA = "REQUIERE_AUDITORIA"


@dataclass
class Resultado:
    estado: str = OK
    producto: str = ""
    bodega: str = ""
    unidad_catalogo: str = ""
    unidad_dictada: str = ""
    cantidad_dictada: float = 0.0
    cantidad_normalizada: float = 0.0
    stock_disponible: float = 0.0
    diferencia: float = 0.0
    error_pct: float = 0.0
    severidad: str = "NINGUNA"
    pregunta: str = ""
    mensajes: list = field(default_factory=list)
    requiere_confirmacion: bool = False

    def dict(self):
        return asdict(self)


def calcular_error(conteo, sd):
    """Error = |(Conteo - SD) / SD| ; si SD == 0 no es porcentualmente definible."""
    sd = float(sd)
    if sd > 0:
        return abs((float(conteo) - sd) / sd)
    return float("inf") if float(conteo) > 0 else 0.0


def _severidad(err):
    if err == float("inf"):
        return "CRITICA"
    if err >= UMBRAL_ALTO:
        return "ALTA"
    if err >= UMBRAL_MEDIO:
        return "MEDIA"
    if err >= UMBRAL_LEVE:
        return "LEVE"
    return "NINGUNA"


def validar(dictado, fila_catalogo, autoconvertir=False):
    """
    dictado: dict de dictado.parsear()
    fila_catalogo: fila del catálogo limpio (Series/dict)
    autoconvertir: si True, convierte kg<->g sin preguntar.
    """
    r = Resultado()
    r.producto = str(fila_catalogo["producto"])
    r.bodega = str(fila_catalogo.get("bodega", "") or "")
    r.unidad_catalogo = str(fila_catalogo["unidad"])
    r.stock_disponible = float(fila_catalogo["stock_disponible"])
    r.unidad_dictada = dictado.get("unidad") or ""
    r.cantidad_dictada = float(dictado.get("cantidad") or 0.0)
    r.cantidad_normalizada = r.cantidad_dictada

    u_cat, u_dic = r.unidad_catalogo, r.unidad_dictada

    # ---------- R1 / R2: verificación de unidad (CRÍTICO) ----------
    if not u_dic:
        r.estado = BLOQUEADO
        r.requiere_confirmacion = True
        r.pregunta = (f"No entendí la unidad de medida. El sistema registra "
                      f"'{r.producto}' en {plural(u_cat)}. "
                      f"¿Puedes confirmar la cantidad en "
                      f"{plural(u_cat)}?")
        r.mensajes.append("Unidad dictada ausente o no reconocida.")
        return r

    if u_dic != u_cat:
        if convertible(u_dic, u_cat):
            equivalente = convertir(r.cantidad_dictada, u_dic, u_cat)
            if autoconvertir:
                r.cantidad_normalizada = round(equivalente, 4)
                r.mensajes.append(
                    f"Conversión automática: {r.cantidad_dictada} {u_dic} = "
                    f"{r.cantidad_normalizada} {u_cat}.")
            else:
                r.estado = BLOQUEADO
                r.requiere_confirmacion = True
                r.pregunta = (
                    f"El sistema registra este producto en "
                    f"{plural(u_cat)}, pero reportaste "
                    f"{plural(u_dic)}. "
                    f"{r.cantidad_dictada} {u_dic} equivalen a "
                    f"{round(equivalente, 4)} {u_cat}. ¿Confirmas?")
                r.mensajes.append(
                    f"Discrepancia de unidad convertible {u_dic} -> {u_cat}.")
                return r
        else:
            # Familias incompatibles: KG vs UND -> BLOQUEO DURO
            r.estado = BLOQUEADO
            r.requiere_confirmacion = True
            r.pregunta = (
                f"El sistema registra este producto en "
                f"{plural(u_cat)}, pero reportaste "
                f"{plural(u_dic)}. ¿Puedes confirmar la "
                f"cantidad en {plural(u_cat)}?")
            r.mensajes.append(
                f"Unidades incompatibles: catálogo={familia(u_cat)}, "
                f"dictado={familia(u_dic)}. Conteo no registrado.")
            return r

    # ---------- R3: umbral de anomalía ----------
    r.diferencia = round(r.cantidad_normalizada - r.stock_disponible, 4)
    err = calcular_error(r.cantidad_normalizada, r.stock_disponible)
    r.error_pct = round(err * 100, 2) if err != float("inf") else -1.0
    r.severidad = _severidad(err)

    if r.stock_disponible == 0 and r.cantidad_normalizada > 0:
        r.estado = REQUIERE_AUDITORIA
        r.mensajes.append(
            f"El catálogo indica Sin Stock pero se contaron "
            f"{r.cantidad_normalizada} {u_cat}. Requiere auditoría.")
    elif r.severidad == "ALTA":
        r.estado = REQUIERE_AUDITORIA
        r.mensajes.append(
            f"Diferencia del {r.error_pct}% supera el umbral crítico "
            f"({int(UMBRAL_ALTO*100)}%). Requiere auditoría obligatoria.")
    elif r.severidad == "MEDIA":
        r.estado = ALERTA
        r.mensajes.append(
            f"Diferencia del {r.error_pct}% supera el umbral de revisión "
            f"({int(UMBRAL_MEDIO*100)}%).")
    elif r.severidad == "LEVE":
        r.estado = ALERTA
        r.mensajes.append(f"Diferencia leve del {r.error_pct}%.")
    else:
        r.estado = OK
        r.mensajes.append(
            f"Conteo dentro del rango esperado ({r.error_pct}%).")

    # Coherencia adicional: fracciones en unidades no fraccionables
    if u_cat in ("UND", "CAJ", "PAQ", "BOL", "BAN") and \
       r.cantidad_normalizada != int(r.cantidad_normalizada):
        r.mensajes.append(
            f"Advertencia: {nombre_unidad(u_cat)} no admite fracciones "
            f"({r.cantidad_normalizada}).")
        if r.estado == OK:
            r.estado = ALERTA

    return r
