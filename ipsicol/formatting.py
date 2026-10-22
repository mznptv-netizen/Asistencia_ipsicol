"""Presentación única de duraciones; los cálculos conservan los segundos."""
from decimal import Decimal, ROUND_HALF_UP


def seconds_from_hours(hours):
    if hours is None:
        return None
    return int((Decimal(str(hours)) * 3600).quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def duration_text(seconds):
    if seconds is None:
        return 'Pendiente'
    seconds = max(0, int(seconds))
    if seconds == 0:
        return '0 minutos'
    if seconds < 60:
        return 'Menos de 1 minuto'
    minutes = seconds // 60
    if minutes < 60:
        return f'{minutes} minuto' + ('' if minutes == 1 else 's')
    hours, minutes = divmod(minutes, 60)
    hours_label = 'hora' if hours == 1 else 'horas'
    minutes_label = 'minuto' if minutes == 1 else 'minutos'
    return f'{hours} {hours_label} {minutes} {minutes_label}'
