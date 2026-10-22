"""Entrada conservadora: nunca extraer dígitos arbitrarios de una cédula.

El hardware decodifica el símbolo. Esta capa recibe texto de teclado HID.
Las tramas no numéricas requieren vinculación presencial verificada.
"""
import hashlib
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Scan:
    document: str | None
    fingerprint: str | None


def document_number(text):
    value = text.strip()
    if not re.fullmatch(r'[0-9]{1,10}', value):
        raise ValueError('Escriba el número de cédula: de 1 a 10 dígitos, sin puntos ni espacios.')
    return value


def parse_scan(text):
    value = text.strip('\r\n')
    if not value or not value.strip():
        raise ValueError('Escanee o escriba un documento.')
    if len(value) > 8192 or '\x00' in value or '\ufffd' in value:
        raise ValueError('Lectura incompatible. Configure el lector para emitir solamente el número de documento o ingréselo manualmente.')
    if re.fullmatch(r'[0-9]{1,10}', value.strip()):
        return Scan(document_number(value), None)
    # No conservar información biográfica o biométrica completa del código.
    digest = hashlib.sha256(value.encode('utf-8')).hexdigest()
    return Scan(None, digest)
