#!/usr/bin/env python3
"""
SISTEMA DE INVENTARIO CON DICTADO Y AUDITORÍA
=============================================
Uso:
  python app.py limpiar  <archivo.xlsx|csv> [--salida limpio.xlsx]
  python app.py sesion   <catalogo> --bodega "X" --contadores "A,B" --auditor "C"
  python app.py demo
"""
import argparse
import importlib.util
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from limpieza import limpiar, COLUMNAS
from bodegas import limpiar_bodegas
from dictado import parsear, buscar_producto
from validacion import validar, BLOQUEADO, ALERTA, REQUIERE_AUDITORIA, OK
from auditoria import (SesionInventario, PENDIENTE_AUDITORIA,
                            PENDIENTE_CONTEO, RECONTEO, APROBADO)

C = {"ok": "\033[92m", "warn": "\033[93m", "err": "\033[91m",
     "info": "\033[96m", "b": "\033[1m", "r": "\033[0m"}


def p(txt, c=None):
    print(f"{C.get(c,'')}{txt}{C['r']}" if c else txt)


def linea(ch="─", n=64):
    print(ch * n)


# ------------------------------------------------------------------ LIMPIAR
def cmd_bodegas(args):
    p(f"\n Limpiando maestro de bodegas: {args.archivo}", "b")
    linea()
    df, rep = limpiar_bodegas(args.archivo, hoja=args.hoja)
    p(f"  Filas leídas      : {rep['filas_origen']}")
    p(f"  Bodegas únicas    : {rep['bodegas_unicas']}", "ok")
    p(f"  Duplicados exactos: {rep['duplicados_exactos']}",
      "warn" if rep["duplicados_exactos"] else None)
    p(f"  Nombres corregidos: {len(rep['correcciones'])}")
    if rep["posibles_duplicados"]:
        p(f"\n  Posibles duplicados a revisar ({len(rep['posibles_duplicados'])}):",
          "warn")
        for d in rep["posibles_duplicados"][:15]:
            p(f"    '{d['a']}'  ~  '{d['b']}'  ({d['similitud']})")
    print()
    print(df.to_string(index=False))
    salida = args.salida or os.path.splitext(args.archivo)[0] + "_BODEGAS_LIMPIO.xlsx"
    with pd.ExcelWriter(salida, engine="openpyxl") as w:
        df.to_excel(w, sheet_name="BODEGAS", index=False)
        if rep["posibles_duplicados"]:
            pd.DataFrame(rep["posibles_duplicados"]).to_excel(
                w, sheet_name="POSIBLES_DUPLICADOS", index=False)
    p(f"\n Guardado: {salida}\n", "ok")
    return df


def cmd_limpiar(args):
    p(f"\n Limpiando: {args.archivo}", "b")
    linea()
    df, rep = limpiar(args.archivo, hoja=args.hoja)

    p(f"  Filas leídas         : {rep['filas_origen']}")
    p(f"  Filas válidas        : {rep['filas_final']}", "ok")
    p(f"  Descartadas          : {rep['descartadas']}")
    p(f"  Duplicados eliminados: {rep['duplicados']}")
    p(f"  Negativos -> Sin Stock: {rep['negativos_corregidos']}",
      "warn" if rep["negativos_corregidos"] else None)
    p(f"  Unidades corregidas  : {rep['unidades_corregidas']}")
    p(f"  Unidades desconocidas: {rep['unidades_desconocidas']}",
      "err" if rep["unidades_desconocidas"] else None)

    if rep["columnas_detectadas"]:
        p("\n  Mapeo de columnas:", "info")
        for orig, canon in rep["columnas_detectadas"].items():
            p(f"    '{orig}' -> {canon}")
    for a in rep["advertencias"]:
        p(f"  ! {a}", "warn")

    if len(df):
        p("\n  Vista previa:", "info")
        print(df.head(12).to_string(index=False))

    salida = args.salida or os.path.splitext(args.archivo)[0] + "_LIMPIO.xlsx"
    if salida.lower().endswith(".csv"):
        df.to_csv(salida, index=False, encoding="utf-8-sig")
    else:
        with pd.ExcelWriter(salida, engine="openpyxl") as w:
            df.to_excel(w, sheet_name="CATALOGO_LIMPIO", index=False)
            pd.DataFrame([{"metrica": k, "valor": str(v)}
                          for k, v in rep.items()
                          if k != "columnas_detectadas"]).to_excel(
                w, sheet_name="REPORTE_LIMPIEZA", index=False)
    p(f"\n Guardado: {salida}\n", "ok")
    return df


# ------------------------------------------------------------------ SESIÓN
def _mostrar_resultado(res):
    if res.estado == BLOQUEADO:
        p(f"\n  BLOQUEADO", "err")
        p(f"  {res.pregunta}", "err")
    elif res.estado == REQUIERE_AUDITORIA:
        p(f"\n  REQUIERE AUDITORÍA (severidad {res.severidad})", "err")
    elif res.estado == ALERTA:
        p(f"\n  ALERTA (severidad {res.severidad})", "warn")
    else:
        p(f"\n  OK", "ok")
    if res.estado != BLOQUEADO:
        p(f"  {res.producto} | catálogo: {res.stock_disponible} {res.unidad_catalogo}"
          f" | contado: {res.cantidad_normalizada} {res.unidad_catalogo}"
          f" | dif: {res.diferencia} ({res.error_pct}%)")
    for m in res.mensajes:
        p(f"   • {m}")


def cmd_sesion(args):
    df, rep = limpiar(args.catalogo, hoja=args.hoja)
    if not len(df):
        p(" El catálogo quedó vacío tras la limpieza.", "err")
        return

    contadores = [c.strip() for c in args.contadores.split(",") if c.strip()]
    ses = SesionInventario(args.bodega, contadores, args.auditor, df)

    linea("═")
    p(f" SESIÓN {ses.id} — Bodega: {ses.bodega}", "b")
    p(f" Modalidad: {len(contadores)} contador(es) + 1 auditor")
    p(f" Contadores: {', '.join(contadores)}   Auditor: {args.auditor}")
    p(f" Catálogo: {len(df)} productos")
    linea("═")
    p(" Dicta:  producto, unidad, cantidad     ('auditar' / 'resumen' / 'salir')\n",
      "info")

    idx_contador = 0
    while True:
        actor = contadores[idx_contador % len(contadores)]
        try:
            texto = input(f"[{actor}] > ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not texto:
            continue
        cmd = texto.lower()

        if cmd in ("salir", "exit", "q"):
            break
        if cmd == "resumen":
            for k, v in ses.resumen().items():
                p(f"  {k}: {v}")
            continue
        if cmd == "auditar":
            _flujo_auditoria(ses)
            continue

        d = parsear(texto)
        if d["errores"] and (d["producto"] is None or d["cantidad"] is None):
            for e in d["errores"]:
                p(f"   {e}", "err")
            continue

        fila, score, alts = buscar_producto(d["producto"], df, args.bodega)
        if fila is None:
            p(f"   Producto no encontrado: '{d['producto']}' (mejor score {score})",
              "err")
            if alts:
                p("   ¿Quisiste decir?", "warn")
                for a in alts[:3]:
                    p(f"     - {a['producto']} ({a['unidad']}, "
                      f"stock {a['stock_disponible']}) score {a['score']}")
            continue

        reg, res = ses.registrar_conteo(actor, d, fila,
                                        autoconvertir=args.autoconvertir)
        _mostrar_resultado(res)

        if res.estado == BLOQUEADO:
            resp = input("  Confirma cantidad en "
                         f"{res.unidad_catalogo} (Enter para omitir): ").strip()
            if resp:
                d2 = parsear(f"{res.producto}, {res.unidad_catalogo}, {resp}")
                if d2["cantidad"] is not None:
                    d2["unidad"] = res.unidad_catalogo
                    reg, res = ses.registrar_conteo(actor, d2, fila)
                    _mostrar_resultado(res)

        if reg:
            p(f"  Estado del registro: {reg['estado']}", "info")
        idx_contador += 1
        print()

    _flujo_auditoria(ses)
    linea("═")
    for k, v in ses.resumen().items():
        p(f"  {k}: {v}")
    ruta = ses.exportar(f"sesion_{ses.id}.json")
    p(f"\n Bitácora exportada: {ruta}", "ok")


def _flujo_auditoria(ses):
    pend = ses.pendientes_auditoria()
    if not pend:
        p("  No hay registros pendientes de auditoría.", "info")
        return
    linea("═")
    p(f" AUDITORÍA — {ses.auditor} ({len(pend)} pendientes)", "b")
    linea("═")
    for r in pend:
        p(f"\n  {r['producto']} [{r['unidad']}]", "b")
        p(f"   Stock sistema : {r['stock_disponible']}")
        for c, v in r["conteos"].items():
            p(f"   Conteo {c}: {v['cantidad']}")
        p(f"   Consenso: {r['consenso']} | dif {r.get('diferencia')} "
          f"({r.get('error_pct')}%) | severidad {r.get('severidad')}",
          "err" if r.get("severidad") in ("ALTA", "CRITICA") else "warn")
        try:
            dec = input("   [A]probar / [R]echazar / [C]ontar de nuevo / [S]altar: "
                        ).strip().upper()
        except (EOFError, KeyboardInterrupt):
            return
        if dec.startswith("A"):
            val = input("   Cantidad final (Enter = consenso): ").strip()
            ses.auditar(ses.auditor, r["producto"], r["unidad"], "APROBAR",
                        float(val) if val else None,
                        input("   Comentario: ").strip())
            p("    Aprobado", "ok")
        elif dec.startswith("R"):
            ses.auditar(ses.auditor, r["producto"], r["unidad"], "RECHAZAR",
                        None, input("   Motivo: ").strip())
            p("    Rechazado", "err")
        elif dec.startswith("C"):
            ses.auditar(ses.auditor, r["producto"], r["unidad"], "RECONTEO",
                        None, "Reconteo ordenado por auditor")
            p("    Reconteo ordenado", "warn")


# ------------------------------------------------------------------ DEMO
def cmd_demo(args):
    if importlib.util.find_spec("demo") is None:
        raise SystemExit("demo.py no existe en la estructura actual del proyecto.")
    importlib.import_module("demo").main()


def main():
    ap = argparse.ArgumentParser(description="Inventario con dictado y auditoría")
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("limpiar", help="Limpia y normaliza un Excel/CSV")
    a.add_argument("archivo")
    a.add_argument("--salida")
    a.add_argument("--hoja", default=0)
    a.set_defaults(func=cmd_limpiar)

    b = sub.add_parser("sesion", help="Inicia sesión de conteo + auditoría")
    b.add_argument("catalogo")
    b.add_argument("--bodega", required=True)
    b.add_argument("--contadores", required=True, help='Ej: "Ana,Luis"')
    b.add_argument("--auditor", required=True)
    b.add_argument("--hoja", default=0)
    b.add_argument("--autoconvertir", action="store_true")
    b.set_defaults(func=cmd_sesion)

    d = sub.add_parser("bodegas", help="Limpia el maestro de bodegas")
    d.add_argument("archivo")
    d.add_argument("--salida")
    d.add_argument("--hoja", default=0)
    d.set_defaults(func=cmd_bodegas)

    c = sub.add_parser("demo", help="Ejecuta la demostración completa")
    c.set_defaults(func=cmd_demo)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
