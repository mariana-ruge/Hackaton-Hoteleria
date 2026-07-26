#!/usr/bin/env python3
"""
Inventario 360 · Colsubsidio — servidor web (backend Flask).
    python server.py          ->  http://localhost:5000
"""
import io
import os
import sys
import uuid
import tempfile
from datetime import datetime, timezone

import pandas as pd
from flask import Flask, request, jsonify, send_file, render_template

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from limpieza import limpiar, limpiar_libro, es_excel, COLUMNAS
from bodegas import limpiar_bodegas
from dictado import parsear, buscar_producto
from qr import decodificar_data_url
from validacion import validar, BLOQUEADO
from unidades import UNIDADES, plural
from auditoria import (SesionInventario, APROBADO, RECONTEO,
                            PENDIENTE_AUDITORIA, PENDIENTE_CONTEO)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FRONTEND_DIR = os.path.join(BASE_DIR, "..", "frontend")

app = Flask(
    __name__,
    template_folder=FRONTEND_DIR,
    static_folder=os.path.join(FRONTEND_DIR, "static"),
    static_url_path="/static",
)
app.config["MAX_CONTENT_LENGTH"] = 32 * 1024 * 1024   # 32 MB
# Sin esto, Flask cachea index.html compilado desde el primer render y los
# cambios en el HTML no se ven hasta reiniciar el servidor a mano.
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.jinja_env.auto_reload = True

# Estado en memoria (para producción: Redis o base de datos)
ESTADO = {"catalogo": None, "reporte": None, "correcciones": [], "bodegas": None, "sesiones": {}, "perfil": None}
SUBIDAS = tempfile.mkdtemp(prefix="inv_")


def _err(mensaje, codigo=400):
    return jsonify({"ok": False, "error": mensaje}), codigo


def _respuesta_bodegas(df, rep, autodetectado=False):
    ESTADO["bodegas"] = df
    payload = {
        "ok": True,
        "modo": "bodegas",
        "reporte": {
            "filas_origen": rep["filas_origen"],
            "bodegas_unicas": rep["bodegas_unicas"],
            "duplicados_exactos": rep["duplicados_exactos"],
            "correcciones": len(rep["correcciones"]),
            "posibles_duplicados": rep["posibles_duplicados"][:20],
        },
        "filas": df.fillna("").to_dict("records"),
    }
    if autodetectado:
        payload["autodetectado"] = True
        payload["mensaje"] = (
            "El archivo parece un maestro de bodegas, no un catálogo de productos. "
            "Se procesó automáticamente como bodegas."
        )
    return jsonify(payload)


def _catalogo_bodega(bodega):
    if ESTADO["catalogo"] is None:
        return None
    return ESTADO["catalogo"][
        ESTADO["catalogo"]["bodega"].astype(str).str.lower() == str(bodega).lower()
    ].copy()


def _payload_catalogo(df, rep, correcciones):
    return {
        "ok": True, "modo": "catalogo",
        "reporte": {
            "filas_origen": rep["filas_origen"],
            "filas_final": rep["filas_final"],
            "descartadas": rep["descartadas"],
            "duplicados": rep["duplicados"],
            "negativos_corregidos": rep["negativos_corregidos"],
            "unidades_corregidas": rep["unidades_corregidas"],
            "unidades_desconocidas": rep["unidades_desconocidas"],
            "columnas_detectadas": rep["columnas_detectadas"],
            "columnas_confusas": rep.get("columnas_confusas", []),
            "advertencias": rep["advertencias"],
            "valores_negativos": rep.get("valores_negativos", []),
            "articulos_completados": rep.get("articulos_completados", []),
            "articulos_no_encontrados": rep.get("articulos_no_encontrados", []),
            "filas_descartadas": rep.get("filas_descartadas", []),
            "duplicados_detalle": rep.get("duplicados_detalle", []),
            "coherencia_modelo": rep.get("coherencia_modelo", {}),
        },
        "correcciones": correcciones,
        "filas": df.fillna("").to_dict("records"),
        "bodegas": sorted({b for b in df["bodega"].astype(str) if b.strip()}),
    }


def _contexto_bodega(bodega):
    df = _catalogo_bodega(bodega)
    if df is None or not len(df):
        return None
    sin_stock = df[df["estado_stock"].astype(str) == "Sin Stock"]
    return {
        "bodega": bodega,
        "productos": int(len(df)),
        "sin_stock": int(len(sin_stock)),
        "con_stock": int(len(df) - len(sin_stock)),
        "unidades": int(df["unidad"].astype(str).nunique()),
        "referencias": int(df["codigo"].fillna("").astype(str).str.strip().replace("nan", "").ne("").sum()),
    }


@app.route("/")
def index():
    return render_template("index.html")


# ─────────────────────────────────────────── CARGA Y LIMPIEZA
@app.route("/api/cargar", methods=["POST"])
def api_cargar():
    if "archivo" not in request.files:
        return _err("No se recibió ningún archivo.")
    f = request.files["archivo"]
    if not f.filename:
        return _err("El archivo no tiene nombre.")
    if not f.filename.lower().endswith((".xlsx", ".xlsm", ".xls", ".csv", ".tsv")):
        return _err("Formato no admitido. Usa Excel (.xlsx) o CSV.")

    ruta = os.path.join(SUBIDAS, f"{uuid.uuid4().hex}_{f.filename}")
    f.save(ruta)
    modo = request.form.get("modo", "catalogo")

    try:
        if modo == "bodegas":
            df, rep = limpiar_bodegas(ruta)
            return _respuesta_bodegas(df, rep)

        if es_excel(ruta):
            df, rep = limpiar_libro(ruta)
            try:
                df_bodegas, rep_bodegas = limpiar_bodegas(ruta, hoja=0)
                if len(df_bodegas):
                    ESTADO["bodegas"] = df_bodegas
            except Exception:
                pass
        else:
            df, rep = limpiar(ruta)
        if not len(df):
            columnas = set(rep.get("columnas_detectadas", {}).values())
            if "bodega" in columnas and "stock_disponible" in columnas and \
               "producto" not in columnas:
                df_bodegas, rep_bodegas = limpiar_bodegas(ruta)
                if len(df_bodegas):
                    return _respuesta_bodegas(df_bodegas, rep_bodegas,
                                              autodetectado=True)
            return _err("Tras la limpieza no quedaron filas válidas. "
                        "Revisa que el archivo tenga columnas de producto, "
                        "unidad y stock.")
        ESTADO["catalogo"] = df
        ESTADO["reporte"] = rep

        correcciones = [
            {"producto": r["producto"], "detalle": r["observaciones"]}
            for _, r in df[df["observaciones"] != ""].iterrows()
        ]
        ESTADO["correcciones"] = correcciones
        return jsonify(_payload_catalogo(df, rep, correcciones))
    except Exception as e:
        return _err(f"No se pudo procesar el archivo: {e}", 500)

@app.route("/api/demo/<tipo>", methods=["GET"])
def api_cargar_demo(tipo):
    """
    Carga uno de los archivos Excel incluidos en el proyecto
    y ejecuta la misma limpieza utilizada por /api/cargar.
    """

    archivos_demo = {
        "bodegas-stock": "BODEGAS Y STOCK.xlsx",
        "stock-disponible": "BODEGAS Y STOCK.xlsx - BODEGAS DISPONIBLES.csv",
    }

    nombre_archivo = archivos_demo.get(tipo)

    if not nombre_archivo:
        return _err("Archivo de demostración no válido.", 404)

    ruta = os.path.join(
    BASE_DIR,
    "..",
    "data",
    "Excel apoyo",
    nombre_archivo,
)

    ruta = os.path.abspath(ruta)

    if not os.path.isfile(ruta):
        return _err(
            f"No se encontró el archivo de demostración: {nombre_archivo}",
            404,
        )

    try:
        if es_excel(ruta):
            df, rep = limpiar_libro(ruta)

            # Intenta obtener también el maestro de bodegas,
            # igual que sucede durante la carga normal.
            try:
                df_bodegas, rep_bodegas = limpiar_bodegas(ruta, hoja=0)

                if len(df_bodegas):
                    ESTADO["bodegas"] = df_bodegas

            except Exception:
                pass

        else:
            df, rep = limpiar(ruta)

        if not len(df):
            return _err(
                "La limpieza terminó, pero no quedaron productos válidos.",
                400,
            )

        ESTADO["catalogo"] = df
        ESTADO["reporte"] = rep

        correcciones = [
            {
                "producto": fila["producto"],
                "detalle": fila["observaciones"],
            }
            for _, fila in df[df["observaciones"] != ""].iterrows()
        ]

        ESTADO["correcciones"] = correcciones

        respuesta = _payload_catalogo(df, rep, correcciones)
        respuesta["archivo_demo"] = nombre_archivo

        return jsonify(respuesta)

    except Exception as error:
        return _err(
            f"No se pudo procesar el archivo de demostración: {error}",
            500,
        )

@app.route("/api/dashboard")
def api_dashboard():
    """Resumen de inicio: cuenta las sesiones de conteo reales del día
    (no datos ficticios) y calcula la fecha del próximo inventario."""
    hoy = datetime.now(timezone.utc).date()
    sesiones_hoy = [s for s in ESTADO["sesiones"].values()
                    if s.creada[:10] == hoy.isoformat()]

    finalizado = sum(1 for s in sesiones_hoy if s.cerrada)
    en_proceso = sum(1 for s in sesiones_hoy if not s.cerrada and s.registros)
    pendientes = sum(1 for s in sesiones_hoy if not s.cerrada and not s.registros)

    if hoy.month == 12:
        proximo_mes = hoy.replace(year=hoy.year + 1, month=1, day=1)
    else:
        proximo_mes = hoy.replace(month=hoy.month + 1, day=1)

    proximo_inventario = None
    df = ESTADO["catalogo"]
    if df is not None and len(df):
        bodegas_disponibles = sorted({b for b in df["bodega"].astype(str) if b.strip()})
        if bodegas_disponibles:
            bodega = bodegas_disponibles[0]
            contexto = _contexto_bodega(bodega)
            proximo_inventario = {
                "bodega": bodega,
                "articulos": contexto["productos"] if contexto else 0,
                "fecha": proximo_mes.isoformat(),
                "hora": "09:00",
            }

    return jsonify({
        "ok": True,
        "hoy": {
            "total": len(sesiones_hoy),
            "pendientes": pendientes,
            "en_proceso": en_proceso,
            "finalizado": finalizado,
        },
        "proximo_inventario": proximo_inventario,
    })


@app.route("/api/catalogo")
def api_catalogo():
    df, rep = ESTADO["catalogo"], ESTADO["reporte"]
    if df is None or rep is None:
        return _err("Aún no has cargado un catálogo.", 404)
    return jsonify(_payload_catalogo(df, rep, ESTADO.get("correcciones") or []))


@app.route("/api/exportar/<tipo>")
def api_exportar(tipo):
    df = ESTADO["catalogo"] if tipo == "catalogo" else ESTADO["bodegas"]
    if df is None:
        return _err("No hay datos para exportar.", 404)
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        df.to_excel(w, sheet_name=tipo.upper()[:31], index=False)
        if tipo == "catalogo" and ESTADO["reporte"]:
            pd.DataFrame([{"metrica": k, "valor": str(v)}
                          for k, v in ESTADO["reporte"].items()
                          if k != "columnas_detectadas"]).to_excel(
                w, sheet_name="REPORTE_LIMPIEZA", index=False)
    buf.seek(0)
    return send_file(buf, as_attachment=True,
                     download_name=f"{tipo}_LIMPIO.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument"
                              ".spreadsheetml.sheet")


# ─────────────────────────────────────────── SESIONES
@app.route("/api/sesion", methods=["POST"])
def api_crear_sesion():
    if ESTADO["catalogo"] is None:
        return _err("Carga primero un catálogo.")

    d = request.get_json(force=True)

    contadores = [ c.strip() for c in d.get("contadores", []) if c and c.strip() ]
    auditor = (d.get("auditor") or "").strip()
    bodega = (d.get("bodega") or "").strip()

    if not bodega:
        return _err(
            "Selecciona una bodega. "
            "La toma física se abre una bodega por sesión."
        )

    contexto = _contexto_bodega(bodega)

    if contexto is None:
        return _err(
            "La bodega seleccionada no existe "
            "en el catálogo cargado."
        )

    try:
        ses = SesionInventario(bodega, contadores, auditor, ESTADO["catalogo"] )
    except ValueError as e:
        return _err(str(e))

    ESTADO["sesiones"][ses.id] = ses

    perfil = ESTADO.get("perfil") or {}
    nombre_perfil = str(perfil.get("nombre", "")).strip()

    if nombre_perfil:
        if nombre_perfil.casefold() == auditor.casefold():
            perfil["rol"] = "auditor"
            perfil["bodega"] = bodega

        elif any(
            nombre_perfil.casefold() == contador.casefold()
            for contador in contadores
        ):
            perfil["rol"] = "contador"
            perfil["bodega"] = bodega

        ESTADO["perfil"] = perfil

    return jsonify({
        "ok": True, "sesion": ses.id, "resumen": ses.resumen(), "contexto_bodega": contexto, "perfil": ESTADO.get("perfil")})


@app.route("/api/sesion/<sid>")
def api_sesion(sid):
    ses = ESTADO["sesiones"].get(sid)
    if not ses:
        return _err("Sesión no encontrada.", 404)
    return jsonify({"ok": True, "resumen": ses.resumen(),
                    "registros": list(ses.registros.values()),
                    "bitacora": ses.bitacora[-40:]})


# ─────────────────────────────────────────── DICTADO
@app.route("/api/interpretar", methods=["POST"])
def api_interpretar():
    """Vista previa del dictado sin registrarlo."""
    d = request.get_json(force=True)
    p = parsear(d.get("texto", ""))
    fila, score, alts = buscar_producto(
        p["producto"], ESTADO["catalogo"], d.get("bodega")) \
        if ESTADO["catalogo"] is not None and p["producto"] else (None, 0.0, [])

    salida = {"ok": True, "dictado": {
        "producto": p["producto"], "unidad": p["unidad"],
        "unidad_dictada": p["unidad_dictada"], "cantidad": p["cantidad"],
        "errores": p["errores"]}, "coincidencia": None,
        "alternativas": alts, "score": score}

    if fila is not None:
        salida["coincidencia"] = {
            "producto": fila["producto"], "unidad": fila["unidad"],
            "bodega": fila["bodega"],
            "stock_disponible": float(fila["stock_disponible"]),
            "estado_stock": fila["estado_stock"]}
    return jsonify(salida)


@app.route("/api/conteo", methods=["POST"])
def api_conteo():
    d = request.get_json(force=True)
    ses = ESTADO["sesiones"].get(d.get("sesion"))
    if not ses:
        return _err("Sesión no encontrada.", 404)

    contador = d.get("contador")
    texto = d.get("texto", "")

    # Confirmación tras un bloqueo: unidad forzada por el usuario
    if d.get("forzar_unidad") and d.get("cantidad") is not None:
        p = {"texto_original": texto, "producto": d.get("producto"),
             "unidad": d["forzar_unidad"], "unidad_dictada": d["forzar_unidad"],
             "cantidad": float(d["cantidad"]), "errores": []}
    else:
        p = parsear(texto)
        if p["producto"] is None or p["cantidad"] is None:
            return jsonify({"ok": False, "tipo": "parseo",
                            "errores": p["errores"], "dictado": p})

    nombre = d.get("producto") or p["producto"]
    fila, score, alts = buscar_producto(nombre, ESTADO["catalogo"], ses.bodega)
    if fila is None:
        return jsonify({"ok": False, "tipo": "sin_coincidencia",
                        "buscado": nombre, "score": score,
                        "alternativas": alts})

    try:
        reg, res = ses.registrar_conteo(contador, p, fila,
                                        autoconvertir=bool(d.get("autoconvertir")))
    except ValueError as e:
        return _err(str(e))

    return jsonify({
        "ok": True,
        "bloqueado": res.estado == BLOQUEADO,
        "resultado": res.dict(),
        "registro": reg,
        "resumen": ses.resumen(),
    })


# ─────────────────────────────────────────── AUDITORÍA
@app.route("/api/auditar", methods=["POST"])
def api_auditar():
    d = request.get_json(force=True)
    ses = ESTADO["sesiones"].get(d.get("sesion"))
    if not ses:
        return _err("Sesión no encontrada.", 404)
    try:
        cant = d.get("cantidad_auditor")
        reg = ses.auditar(d.get("auditor"), d.get("producto"), d.get("unidad"),
                          d.get("decision"),
                          float(cant) if cant not in (None, "") else None,
                          d.get("comentario", ""))
    except ValueError as e:
        return _err(str(e))
    return jsonify({"ok": True, "registro": reg, "resumen": ses.resumen(),
                    "pendientes": len(ses.pendientes_auditoria())})


@app.route("/api/cerrar", methods=["POST"])
def api_cerrar():
    d = request.get_json(force=True)
    ses = ESTADO["sesiones"].get(d.get("sesion"))
    if not ses:
        return _err("Sesión no encontrada.", 404)
    try:
        resumen = ses.cerrar(d.get("auditor"))
    except ValueError as e:
        ok, abiertos = ses.puede_cerrar()
        return jsonify({"ok": False, "error": str(e),
                        "abiertos": [{"producto": r["producto"],
                                      "estado": r["estado"],
                                      "motivo": r.get("motivo", "")}
                                     for r in abiertos]}), 400
    return jsonify({"ok": True, "resumen": resumen})


@app.route("/api/sesion/<sid>/exportar")
def api_exportar_sesion(sid):
    ses = ESTADO["sesiones"].get(sid)
    if not ses:
        return _err("Sesión no encontrada.", 404)

    catalogo_bodega = _catalogo_bodega(ses.bodega)
    if catalogo_bodega is None or not len(catalogo_bodega):
        return _err("No hay datos limpios para la bodega de esta sesión.", 404)

    salida = catalogo_bodega.copy()
    salida["conteo_fisico"] = None
    salida["diferencia_contra_sistema"] = None
    salida["error_pct_conteo"] = None
    salida["estado_conteo"] = "PENDIENTE"
    salida["severidad_conteo"] = ""
    salida["auditor"] = ""
    salida["decision_auditoria"] = ""
    salida["comentario_auditoria"] = ""

    detalle = []
    for r in ses.registros.values():
        mask = (
            salida["producto"].astype(str).str.lower().eq(str(r["producto"]).lower()) &
            salida["bodega"].astype(str).str.lower().eq(str(r["bodega"]).lower()) &
            salida["unidad"].astype(str).eq(str(r["unidad"]))
        )
        salida.loc[mask, "conteo_fisico"] = r.get("consenso")
        salida.loc[mask, "diferencia_contra_sistema"] = r.get("diferencia")
        salida.loc[mask, "error_pct_conteo"] = r.get("error_pct")
        salida.loc[mask, "estado_conteo"] = r["estado"]
        salida.loc[mask, "severidad_conteo"] = r.get("severidad") or ""
        if r.get("dictamen"):
            salida.loc[mask, "auditor"] = r["dictamen"].get("auditor", "")
            salida.loc[mask, "decision_auditoria"] = r["dictamen"].get("decision", "")
            salida.loc[mask, "comentario_auditoria"] = r["dictamen"].get("comentario", "")

        fila = {"producto": r["producto"], "bodega": r["bodega"],
                "unidad": r["unidad"], "stock_sistema": r["stock_disponible"],
                "conteo_fisico": r["consenso"], "diferencia": r.get("diferencia"),
                "error_pct": r.get("error_pct"), "severidad": r.get("severidad"),
                "dispersion_pct": r.get("dispersion_pct"), "estado": r["estado"]}
        for i, (c, v) in enumerate(r["conteos"].items(), 1):
            fila[f"contador_{i}"] = c
            fila[f"conteo_{i}"] = v["cantidad"]
        if r.get("dictamen"):
            fila["auditor"] = r["dictamen"]["auditor"]
            fila["decision"] = r["dictamen"]["decision"]
            fila["comentario"] = r["dictamen"]["comentario"]
        detalle.append(fila)

    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        salida.to_excel(w, sheet_name="DATOS_LIMPIOS", index=False)
        pd.DataFrame(detalle).to_excel(w, sheet_name="DETALLE_CONTEO", index=False)
        pd.DataFrame([ses.resumen()]).T.reset_index().rename(
            columns={"index": "metrica", 0: "valor"}).to_excel(
            w, sheet_name="RESUMEN", index=False)
        pd.DataFrame(ses.bitacora).to_excel(w, sheet_name="BITACORA", index=False)
        if ESTADO.get("reporte") and ESTADO["reporte"].get("coherencia_modelo", {}).get("detalle"):
            pd.DataFrame(ESTADO["reporte"]["coherencia_modelo"]["detalle"]).to_excel(
                w, sheet_name="ALERTAS_COHERENCIA", index=False)
    buf.seek(0)
    return send_file(buf, as_attachment=True,
                     download_name=f"datos_limpios_{sid}.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument"
                              ".spreadsheetml.sheet")


@app.route("/api/unidades")
def api_unidades():
    return jsonify({"ok": True, "unidades": [
        {"codigo": k, "nombre": v["nombre"], "plural": plural(k),
         "familia": v["familia"]} for k, v in UNIDADES.items()]})


def parsear_perfil_qr(texto):
    partes = [parte.strip() for parte in str(texto or "").split("|")]

    if len(partes) < 3:
        return None

    nombre = partes[0]
    bodega = partes[1]

    documento = partes[2]
    if documento.upper().startswith("ID"):
        documento = documento[2:].strip()

    if not nombre or not bodega or not documento:
        return None

    return {
        "uid": f"USR-{documento}",
        "nombre": nombre,
        "email": "",
        "telefono": "",
        "rol": "encargado",
        "bodega": bodega,
        "documento": documento,
        "estado": "Cuenta activa",
        "ultimo_acceso": "Hoy"
    }

# ─────────────────────────────────────────── QR (carnet)
@app.route("/api/qr/decodificar", methods=["POST"])
def api_qr_decodificar():
    datos = request.get_json(silent=True) or {}

    texto = decodificar_data_url(datos.get("imagen"))

    if not texto:
        return jsonify({
            "ok": False,
            "error": "No se detectó ningún código QR en la imagen."
        })

    perfil = parsear_perfil_qr(texto)

    if perfil is None:
        return jsonify({
            "ok": False,
            "error": (
                "El código QR no tiene el formato esperado: "
                "Nombre | Bodega | ID documento"
            )
        })

    ESTADO["perfil"] = perfil

    return jsonify({
        "ok": True,
        "texto": texto,
        "perfil": perfil
    })


@app.errorhandler(413)
def muy_grande(e):
    return _err("El archivo supera el límite de 32 MB.", 413)


@app.route("/api/perfil", methods=["GET"])
def obtener_perfil():
    perfil = ESTADO.get("perfil")

    if perfil is None:
        perfil = {
            "nombre": "",
            "email": "",
            "telefono": "",
            "rol": "",
            "bodega": "",
            "documento": "",
            "estado": "Cuenta activa",
            "ultimo_acceso": "Hoy"
        }

    return jsonify({
        "ok": True,
        "perfil": perfil
    })


@app.route("/api/perfil", methods=["PUT"])
def actualizar_perfil():
    datos = request.get_json(silent=True) or {}

    nombre = str(datos.get("nombre", "")).strip()
    email = str(datos.get("email", "")).strip()
    telefono = str(datos.get("telefono", "")).strip()
    rol = str(datos.get("rol", "")).strip()
    bodega = str(datos.get("bodega", "")).strip()
    documento = str(datos.get("documento", "")).strip()

    if not nombre:
        return _err("El nombre es obligatorio.")

    if not email:
        return _err("El correo electrónico es obligatorio.")

    if "@" not in email:
        return _err("El correo electrónico no es válido.")

    roles_validos = {
        "contador",
        "auditor",
        "encargado",
        "administrador"
    }

    if rol and rol not in roles_validos:
        return _err("El rol seleccionado no es válido.")

    ESTADO["perfil"] = {
        "nombre": nombre,
        "email": email,
        "telefono": telefono,
        "rol": rol,
        "bodega": bodega,
        "documento": documento,
        "estado": "Cuenta activa",
        "ultimo_acceso": "Hoy"
    }

    return jsonify({
        "ok": True,
        "mensaje": "Perfil actualizado correctamente.",
        "perfil": ESTADO["perfil"]
    })

if __name__ == "__main__":
    print("\n  Inventario 360 · Colsubsidio  →  http://localhost:5000\n")
    app.run(host="0.0.0.0", port=5000, debug=False)