"""
Proceso de auditoría OBLIGATORIO.
Escenarios soportados:
  - 1 contador + 1 auditor
  - 2 contadores + 1 auditor
  - hasta 3 contadores + 1 auditor
Regla: ningún conteo queda cerrado sin dictamen del auditor.
"""
import json
import uuid
from datetime import datetime, timezone
from statistics import mean, pstdev

from validacion import validar, calcular_error, OK, BLOQUEADO, ALERTA, REQUIERE_AUDITORIA

# Estados del registro
PENDIENTE_CONTEO = "PENDIENTE_CONTEO"
PENDIENTE_AUDITORIA = "PENDIENTE_AUDITORIA"
APROBADO = "APROBADO"
RECHAZADO = "RECHAZADO"
RECONTEO = "RECONTEO"

# Tolerancia entre contadores (5 %) antes de exigir reconteo
TOLERANCIA_ENTRE_CONTADORES = 0.05


def _ahora():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class SesionInventario:
    """
    Una sesión = una toma física de inventario en una bodega.
    contadores: lista de nombres (1 a 3). auditor: nombre (obligatorio).
    """

    def __init__(self, bodega, contadores, auditor, catalogo):
        if not auditor:
            raise ValueError("La auditoría es obligatoria: debe asignarse un auditor.")
        if not contadores or len(contadores) < 1:
            raise ValueError("Debe haber al menos 1 contador.")
        if len(contadores) > 3:
            raise ValueError("Máximo 3 contadores por sesión.")
        if auditor in contadores:
            raise ValueError("El auditor no puede ser también contador "
                             "(segregación de funciones).")

        self.id = str(uuid.uuid4())[:8].upper()
        self.bodega = bodega
        self.contadores = list(contadores)
        self.auditor = auditor
        self.catalogo = catalogo
        self.creada = _ahora()
        self.cerrada = None
        self.registros = {}   # clave producto|unidad -> registro
        self.bitacora = []    # trazabilidad completa

    # ---------- Trazabilidad ----------
    def _log(self, accion, actor, detalle):
        self.bitacora.append({"ts": _ahora(), "accion": accion,
                              "actor": actor, "detalle": detalle})

    def _clave(self, producto, unidad):
        return f"{producto}|{unidad}".lower()

    # ---------- Registro de conteos ----------
    def registrar_conteo(self, contador, dictado, fila_catalogo, autoconvertir=False):
        """Registra el conteo de un contador. Devuelve (registro, resultado_validacion)."""
        if contador not in self.contadores:
            raise ValueError(f"'{contador}' no está asignado a esta sesión.")

        res = validar(dictado, fila_catalogo, autoconvertir=autoconvertir)

        if res.estado == BLOQUEADO:
            self._log("CONTEO_BLOQUEADO", contador,
                      {"producto": res.producto, "motivo": res.pregunta})
            return None, res

        clave = self._clave(res.producto, res.unidad_catalogo)
        reg = self.registros.get(clave)
        if reg is None:
            reg = {
                "producto": res.producto,
                "bodega": res.bodega or self.bodega,
                "unidad": res.unidad_catalogo,
                "stock_disponible": res.stock_disponible,
                "conteos": {},
                "estado": PENDIENTE_CONTEO,
                "consenso": None,
                "error_pct": None,
                "severidad": None,
                "dictamen": None,
            }
            self.registros[clave] = reg

        reg["conteos"][contador] = {
            "cantidad": res.cantidad_normalizada,
            "unidad_dictada": res.unidad_dictada,
            "texto": dictado.get("texto_original", ""),
            "ts": _ahora(),
            "mensajes": res.mensajes,
        }
        self._log("CONTEO", contador,
                  {"producto": res.producto, "cantidad": res.cantidad_normalizada,
                   "unidad": res.unidad_catalogo})

        self._evaluar(reg)
        return reg, res

    def _evaluar(self, reg):
        """Calcula consenso entre contadores y define si pasa a auditoría."""
        valores = [c["cantidad"] for c in reg["conteos"].values()]
        n_esperado = len(self.contadores)

        if len(reg["conteos"]) < n_esperado:
            reg["estado"] = PENDIENTE_CONTEO
            reg["consenso"] = round(mean(valores), 4) if valores else None
            return reg

        # Coherencia entre contadores
        if len(valores) > 1:
            base = mean(valores)
            disp = (max(valores) - min(valores)) / base if base > 0 else (
                0.0 if max(valores) == 0 else float("inf"))
            reg["dispersion_pct"] = (round(disp * 100, 2)
                                     if disp != float("inf") else -1.0)
            reg["desviacion"] = round(pstdev(valores), 4) if len(valores) > 1 else 0.0
            if disp > TOLERANCIA_ENTRE_CONTADORES:
                reg["estado"] = RECONTEO
                reg["consenso"] = round(base, 4)
                reg["conteos_previos"] = dict(reg["conteos"])
                reg["conteos"] = {}          # se vuelve a contar desde cero
                _e = calcular_error(reg["consenso"], reg["stock_disponible"])
                reg["error_pct"] = round(_e * 100, 2) if _e != float("inf") else -1.0
                reg["diferencia"] = round(reg["consenso"] - reg["stock_disponible"], 4)
                reg["severidad"] = ("CRITICA" if _e == float("inf") else
                                    "ALTA" if _e >= 0.60 else
                                    "MEDIA" if _e >= 0.30 else
                                    "LEVE" if _e >= 0.10 else "NINGUNA")
                reg["motivo"] = (
                    f"Los contadores difieren en {reg['dispersion_pct']}% "
                    f"(máximo permitido {int(TOLERANCIA_ENTRE_CONTADORES*100)}%). "
                    f"Se requiere reconteo.")
                self._log("RECONTEO_SOLICITADO", "SISTEMA",
                          {"producto": reg["producto"], "valores": valores})
                return reg
            consenso = round(base, 4)
        else:
            reg["dispersion_pct"] = 0.0
            reg["desviacion"] = 0.0
            consenso = valores[0]

        reg["consenso"] = consenso
        err = calcular_error(consenso, reg["stock_disponible"])
        reg["error_pct"] = round(err * 100, 2) if err != float("inf") else -1.0
        reg["diferencia"] = round(consenso - reg["stock_disponible"], 4)
        reg["severidad"] = ("CRITICA" if err == float("inf") else
                            "ALTA" if err >= 0.60 else
                            "MEDIA" if err >= 0.30 else
                            "LEVE" if err >= 0.10 else "NINGUNA")
        reg["estado"] = PENDIENTE_AUDITORIA   # auditoría siempre obligatoria
        return reg

    # ---------- Auditoría ----------
    def pendientes_auditoria(self):
        return [r for r in self.registros.values()
                if r["estado"] == PENDIENTE_AUDITORIA]

    def auditar(self, auditor, producto, unidad, decision,
                cantidad_auditor=None, comentario=""):
        """decision: 'APROBAR' | 'RECHAZAR' | 'RECONTEO'"""
        if auditor != self.auditor:
            raise ValueError(f"'{auditor}' no es el auditor de esta sesión.")

        clave = self._clave(producto, unidad)
        reg = self.registros.get(clave)
        if reg is None:
            raise ValueError(f"No existe registro para '{producto}' ({unidad}).")

        decision = decision.upper()
        reg["dictamen"] = {
            "auditor": auditor,
            "decision": decision,
            "cantidad_auditor": cantidad_auditor,
            "comentario": comentario,
            "ts": _ahora(),
        }

        if decision == "APROBAR":
            if cantidad_auditor is not None:
                reg["consenso"] = float(cantidad_auditor)
                err = calcular_error(reg["consenso"], reg["stock_disponible"])
                reg["error_pct"] = round(err * 100, 2) if err != float("inf") else -1.0
                reg["diferencia"] = round(reg["consenso"] - reg["stock_disponible"], 4)
            reg["estado"] = APROBADO
        elif decision == "RECHAZAR":
            reg["estado"] = RECHAZADO
        elif decision == "RECONTEO":
            reg["estado"] = RECONTEO
            reg["conteos"] = {}
        else:
            raise ValueError("Decisión inválida. Use APROBAR, RECHAZAR o RECONTEO.")

        self._log("AUDITORIA", auditor,
                  {"producto": producto, "decision": decision,
                   "comentario": comentario})
        return reg

    # ---------- Cierre ----------
    def puede_cerrar(self):
        abiertos = [r for r in self.registros.values() if r["estado"] != APROBADO]
        return len(abiertos) == 0, abiertos

    def cerrar(self, auditor):
        if auditor != self.auditor:
            raise ValueError("Solo el auditor puede cerrar la sesión.")
        ok, abiertos = self.puede_cerrar()
        if not ok:
            raise ValueError(
                f"No se puede cerrar: {len(abiertos)} registro(s) sin aprobación "
                f"del auditor.")
        self.cerrada = _ahora()
        self._log("CIERRE", auditor, {"registros": len(self.registros)})
        return self.resumen()

    def resumen(self):
        regs = list(self.registros.values())
        return {
            "sesion": self.id,
            "bodega": self.bodega,
            "modalidad": f"{len(self.contadores)} contador(es) + 1 auditor",
            "contadores": self.contadores,
            "auditor": self.auditor,
            "creada": self.creada,
            "cerrada": self.cerrada,
            "total_registros": len(regs),
            "aprobados": sum(1 for r in regs if r["estado"] == APROBADO),
            "pendientes_auditoria": sum(1 for r in regs
                                        if r["estado"] == PENDIENTE_AUDITORIA),
            "pendientes_conteo": sum(1 for r in regs
                                     if r["estado"] == PENDIENTE_CONTEO),
            "reconteo": sum(1 for r in regs if r["estado"] == RECONTEO),
            "rechazados": sum(1 for r in regs if r["estado"] == RECHAZADO),
            "anomalias": sum(1 for r in regs
                             if r.get("severidad") in ("ALTA", "CRITICA")),
        }

    def exportar(self, ruta_json):
        data = {"resumen": self.resumen(),
                "registros": list(self.registros.values()),
                "bitacora": self.bitacora}
        with open(ruta_json, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return ruta_json
