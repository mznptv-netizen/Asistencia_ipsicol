"""Consultas por intervalos [inicio, fin), sin sumar dos veces medianoche."""
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from .config import now_local


def period_bounds(mode, anchor, end=None):
    if mode == 'Día':
        start, finish = anchor, anchor + timedelta(days=1)
    elif mode == 'Semana':
        start = anchor - timedelta(days=anchor.weekday())
        finish = start + timedelta(days=7)
    elif mode == 'Mes':
        start = anchor.replace(day=1)
        finish = date(start.year+1, 1, 1) if start.month == 12 else date(start.year, start.month+1, 1)
    elif mode == 'Año':
        start, finish = date(anchor.year, 1, 1), date(anchor.year+1, 1, 1)
    elif mode == 'Personalizado':
        if end is None or end < anchor:
            raise ValueError('La fecha final debe ser igual o posterior a la inicial.')
        start, finish = anchor, end + timedelta(days=1)
    else:
        raise ValueError('Periodo desconocido.')
    return datetime.combine(start, time.min), datetime.combine(finish, time.min)


@dataclass
class Report:
    start: datetime
    end: datetime
    query: str
    kind: str
    generated_at: datetime
    details: list
    summary: list

    @property
    def total_hours(self):
        return sum(r['seconds'] for r in self.details) / 3600

    @property
    def open_count(self):
        return sum(r['fecha_salida'] is None for r in self.details)


def build_report(db, start, end, query='', kind='Todos', clock=now_local):
    if start >= end:
        raise ValueError('El rango de consulta no es válido.')
    with db.connect() as conn:
        records = conn.execute('''SELECT a.*,p.nombres,p.apellidos,p.tipo_persona,p.cargo_o_area
            FROM asistencia a JOIN personas p USING(numero_documento)
            WHERE a.fecha_ingreso < ? AND (a.fecha_salida IS NULL OR a.fecha_salida > ?)
            AND (?='Todos' OR p.tipo_persona=?)
            AND instr(fold(p.numero_documento||' '||p.apellidos||' '||p.nombres||' '||p.cargo_o_area),fold(?))>0
            ORDER BY fold(p.apellidos),fold(p.nombres),a.fecha_ingreso''',
            (end.isoformat(' '), start.isoformat(' '), kind, kind, query.strip())).fetchall()
    details, people = [], {}
    for record in records:
        row = dict(record)
        arrival = datetime.fromisoformat(row['fecha_ingreso'])
        departure = datetime.fromisoformat(row['fecha_salida']) if row['fecha_salida'] else None
        left, right = max(start, arrival), min(end, departure) if departure else None
        seconds = max(0, int((right-left).total_seconds())) if right else 0
        row['seconds'] = seconds
        row['hours_period'] = seconds / 3600 if departure else None
        row['nombre'] = row['apellidos'] + ' ' + row['nombres']
        details.append(row)
        doc = row['numero_documento']
        summary = people.setdefault(doc, {'numero_documento': doc, 'nombre': row['nombre'],
            'tipo_persona': row['tipo_persona'], 'cargo_o_area': row['cargo_o_area'],
            'days': set(), 'seconds': 0, 'pending': 0})
        summary['seconds'] += seconds
        summary['pending'] += departure is None
        if seconds:
            day = left.date()
            final_day = (right - timedelta(microseconds=1)).date()
            while day <= final_day:
                summary['days'].add(day)
                day += timedelta(days=1)
    summaries = []
    for summary in people.values():
        summary['days'] = len(summary['days'])
        summary['hours'] = summary['seconds'] / 3600
        summaries.append(summary)
    return Report(start, end, query, kind, clock(), details, summaries)
