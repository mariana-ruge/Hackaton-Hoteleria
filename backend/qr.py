"""
Lectura de códigos QR a partir de una imagen capturada en el navegador.
Usa el detector integrado de OpenCV: no depende de librerías de sistema
como zbar, así que funciona igual en cualquier máquina con el venv.
"""
import base64
import re

import cv2
import numpy as np

_detector = cv2.QRCodeDetector()

_RE_DATA_URL = re.compile(r"^data:image/\w+;base64,(.+)$", re.S)


def decodificar_data_url(data_url):
    """
    Recibe un data URL ('data:image/jpeg;base64,...') y devuelve el texto
    del primer código QR encontrado, o None si no hay ninguno en la imagen.
    """
    if not data_url:
        return None
    m = _RE_DATA_URL.match(data_url)
    b64 = m.group(1) if m else data_url

    try:
        binario = base64.b64decode(b64)
    except (base64.binascii.Error, ValueError):
        return None

    arreglo = np.frombuffer(binario, dtype=np.uint8)
    imagen = cv2.imdecode(arreglo, cv2.IMREAD_COLOR)
    if imagen is None:
        return None

    texto, _puntos, _rectificada = _detector.detectAndDecode(imagen)
    return texto or None
