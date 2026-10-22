"""Rutas persistentes separadas del ejecutable y reloj de Colombia."""
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

APP_NAME = 'IPSICOL Asistencia'
MAGENTA = '#B51C67'
BOGOTA = ZoneInfo('America/Bogota')
DEBOUNCE_SECONDS = 60
MAX_SHIFT_HOURS = 18


def now_local():
    # SQLite almacena hora civil colombiana con precisión de segundos.
    return datetime.now(BOGOTA).replace(tzinfo=None, microsecond=0)


def data_dir():
    override = os.environ.get('IPSICOL_DATA_DIR')
    base = Path(override) if override else Path(os.environ.get('LOCALAPPDATA', Path.home() / '.local/share')) / 'IPSICOL' / 'Asistencia'
    base.mkdir(parents=True, exist_ok=True)
    return base


def resource(name):
    base = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent.parent))
    return base / 'assets' / name
