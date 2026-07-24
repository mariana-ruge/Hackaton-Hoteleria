#!/usr/bin/env python3
"""
Genera un código QR real (PNG) para probar el lector de la app.
    python generar_qr_prueba.py ["texto a codificar"]

El PNG queda en frontend/static/img/qr-prueba.png, así que también se
puede abrir desde el celular en http://<ip-de-tu-pc>:5000/static/img/qr-prueba.png
mientras el servidor esté corriendo.
"""
import os
import sys

import qrcode

TEXTO_DEFECTO = "Jenny Gutierrez | Restaurante | ID 987654"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DESTINO = os.path.join(BASE_DIR, "..", "frontend", "static", "img", "qr-prueba.png")


def generar(texto):
    qr = qrcode.QRCode(border=4, box_size=14)
    qr.add_data(texto)
    qr.make(fit=True)
    imagen = qr.make_image(fill_color="black", back_color="white")
    imagen.save(DESTINO)
    return imagen.size


if __name__ == "__main__":
    texto = sys.argv[1] if len(sys.argv) > 1 else TEXTO_DEFECTO
    ancho, alto = generar(texto)
    print(f"QR generado ({ancho}x{alto}px): {os.path.abspath(DESTINO)}")
    print(f"Contenido codificado: {texto!r}")
    print("Ábrelo desde el celular en http://<ip-de-tu-pc>:5000/static/img/qr-prueba.png "
          "(el servidor debe estar corriendo) y muéstraselo a la cámara del computador.")
