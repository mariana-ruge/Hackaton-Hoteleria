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

import pandas as pd
from flask import Flask, request, jsonify, send_file, render_template

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from limpieza import limpiar, limpiar_libro, es_excel, COLUMNAS
from bodegas import limpiar_bodegas
from dictado import parsear, buscar_producto
from validacion import validar, BLOQUEADO
from unidades import UNIDADES, plural
from auditoria import (SesionInventario, APROBADO, RECONTEO,
                            PENDIENTE_AUDITORIA, PENDIENTE_CONTEO)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FRONTEND_DIR = os.path.join(BASE_DIR, "..", "frontend")
REFERENCIA_XLSX = os.path.join(BASE_DIR, "..", "data", "Excel apoyo",
                                "BODEGAS Y STOCK.xlsx")

app = Flask(
    __name__,
    template_folder=FRONTEND_DIR,
    static_folder=os.path.join(FRONTEND_DIR, "static"),
    static_url_path="/static",
)
app.config["MAX_CONTENT_LENGTH"] = 32 * 1024 * 1024   # 32 MB

# Estado en memoria (para producción: Redis o base de datos)
ESTADO = {"catalogo": None, "reporte": None, "correcciones": [], "bodegas": None, "sesiones": {}}
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


def _cargar_referencia():
    """Precarga el catálogo de referencia (data/Excel apoyo) al iniciar el
    servidor, así la app arranca con datos reales sin exigir una carga manual."""
    if not os.path.isfile(REFERENCIA_XLSX):
        return
    try:
        df, rep = limpiar_libro(REFERENCIA_XLSX)
        if len(df):
            correcciones = [
                {"producto": r["producto"], "detalle": r["observaciones"]}
                for _, r in df[df["observaciones"] != ""].iterrows()
            ]
            ESTADO["catalogo"] = df
            ESTADO["reporte"] = rep
            ESTADO["correcciones"] = correcciones
            print(f"  Catálogo de referencia precargado: {rep['filas_final']} productos "
                  f"({len(rep.get('hojas_catalogo', []))} bodegas)")
        df_bodegas, rep_bodegas = limpiar_bodegas(REFERENCIA_XLSX, hoja=0)
        if len(df_bodegas):
            ESTADO["bodegas"] = df_bodegas
    except Exception as e:
        print(f"  Aviso: no se pudo precargar el catálogo de referencia: {e}")


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
    contadores = [c.strip() for c in d.get("contadores", []) if c and c.strip()]
    auditor = (d.get("auditor") or "").strip()
    bodega = (d.get("bodega") or "").strip()

    if not bodega:
        return _err("Selecciona una bodega. La toma física se abre una bodega por sesión.")
    contexto = _contexto_bodega(bodega)
    if contexto is None:
        return _err("La bodega seleccionada no existe en el catálogo cargado.")

    try:
        ses = SesionInventario(bodega, contadores, auditor, ESTADO["catalogo"])
    except ValueError as e:
        return _err(str(e))

    ESTADO["sesiones"][ses.id] = ses
    return jsonify({"ok": True, "sesion": ses.id, "resumen": ses.resumen(),
                    "contexto_bodega": contexto})


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


@app.errorhandler(413)
def muy_grande(e):
    return _err("El archivo supera el límite de 32 MB.", 413)


_cargar_referencia()

if __name__ == "__main__":
    print("\n  Inventario 360 · Colsubsidio  →  http://localhost:5000\n")
    app.run(host="0.0.0.0", port=5000, debug=False)
