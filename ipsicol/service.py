"""Reglas de asistencia independientes de la interfaz; fáciles de probar."""
from dataclasses import dataclass
from datetime import datetime, timedelta
import json
from .config import DEBOUNCE_SECONDS, MAX_SHIFT_HOURS, now_local
from .scanner import document_number, parse_scan


class AttendanceError(ValueError):
    pass


class StaleShift(AttendanceError):
    pass


@dataclass(frozen=True)
class MarkResult:
    action: str
    at: datetime
    record_id: int
    hours: float | None = None


def stamp(value):
    if value.tzinfo is not None:
        raise ValueError('Use hora local colombiana sin tzinfo para persistencia.')
    return value.isoformat(sep=' ', timespec='seconds')


class AttendanceService:
    def __init__(self, db, clock=now_local, debounce=DEBOUNCE_SECONDS):
        self.db, self.clock, self.debounce = db, clock, debounce

    def lookup_person(self, text):
        """Consultar sin marcar: número manual o lectura previamente vinculada."""
        if not text.strip():
            return None
        scan = parse_scan(text)
        document = scan.document or self.db.linked_document(scan.fingerprint)
        return self.db.person(document) if document else None

    def save_person(self, document, names, surnames, kind, area, *, update=False, fingerprint=None):
        document = document_number(document)
        values = [' '.join(v.split()) for v in (names, surnames, area)]
        names, surnames, area = values
        if not names or not surnames or max(map(len, values)) > 150:
            raise AttendanceError('Nombres y apellidos son obligatorios; máximo 150 caracteres por campo.')
        if kind not in ('Funcionario', 'Visitante'):
            raise AttendanceError('Seleccione Funcionario o Visitante.')
        with self.db.transaction() as conn:
            previous = conn.execute('SELECT * FROM personas WHERE numero_documento=?', (document,)).fetchone()
            if update:
                if not previous:
                    raise AttendanceError('La persona ya no existe.')
                conn.execute('UPDATE personas SET nombres=?,apellidos=?,tipo_persona=?,cargo_o_area=? WHERE numero_documento=?', (names, surnames, kind, area, document))
            else:
                if previous:
                    raise AttendanceError('Este documento ya está registrado. Use Editar persona.')
                conn.execute('INSERT INTO personas VALUES(?,?,?,?,?)', (document, names, surnames, kind, area))
            self._audit(conn, 'EDITAR_PERSONA' if update else 'CREAR_PERSONA', None, document,
                        json.dumps({'antes': dict(previous) if previous else None, 'despues': [names, surnames, kind, area]}, ensure_ascii=False))
            if fingerprint:
                self._link(conn, fingerprint, document)
        return document

    def _audit(self, conn, action, record_id, document, detail):
        conn.execute('INSERT INTO auditoria(fecha,accion,asistencia_id,numero_documento,detalle) VALUES(?,?,?,?,?)',
                     (stamp(self.clock()), action, record_id, document, detail))

    def _link(self, conn, fingerprint, document):
        existing = conn.execute('SELECT numero_documento FROM codigos_lector WHERE huella=?', (fingerprint,)).fetchone()
        if existing and existing[0] != document:
            raise AttendanceError('Esta lectura ya pertenece a otro documento. No se modificó la vinculación.')
        conn.execute('INSERT OR IGNORE INTO codigos_lector VALUES(?,?,?)', (fingerprint, document, stamp(self.clock())))
        self._audit(conn, 'VINCULAR_LECTOR', None, document, 'Documento verificado presencialmente por el operador.')

    def link(self, fingerprint, document):
        with self.db.transaction() as conn:
            if not conn.execute('SELECT 1 FROM personas WHERE numero_documento=?', (document,)).fetchone():
                raise AttendanceError('Primero registre a la persona.')
            self._link(conn, fingerprint, document)

    def mark(self, document):
        now = self.clock().replace(microsecond=0)
        with self.db.transaction() as conn:
            if not conn.execute('SELECT 1 FROM personas WHERE numero_documento=?', (document,)).fetchone():
                raise AttendanceError('Primero registre a la persona.')
            last = conn.execute('SELECT * FROM asistencia WHERE numero_documento=? ORDER BY id DESC LIMIT 1', (document,)).fetchone()
            if last:
                event = datetime.fromisoformat(last['fecha_salida'] or last['fecha_ingreso'])
                elapsed = (now - event).total_seconds()
                if elapsed < 0:
                    raise AttendanceError('El reloj del equipo es anterior a la última marcación. Revise fecha y hora.')
                if elapsed < self.debounce:
                    return MarkResult('Duplicado ignorado', event, last['id'])
            opened = conn.execute('SELECT * FROM asistencia WHERE numero_documento=? AND fecha_salida IS NULL', (document,)).fetchone()
            if opened:
                start = datetime.fromisoformat(opened['fecha_ingreso'])
                if now - start > timedelta(hours=MAX_SHIFT_HOURS):
                    raise StaleShift('Hay un ingreso abierto de más de 18 horas. Revise la fecha real de salida en Pendientes; no se inventará una salida.')
                conn.execute('UPDATE asistencia SET fecha_salida=? WHERE id=?', (stamp(now), opened['id']))
                self._audit(conn, 'SALIDA', opened['id'], document, 'Marcación por lector o digitación.')
                return MarkResult('Salida', now, opened['id'], (now-start).total_seconds()/3600)
            cursor = conn.execute('INSERT INTO asistencia(numero_documento,fecha_ingreso) VALUES(?,?)', (document, stamp(now)))
            self._audit(conn, 'INGRESO', cursor.lastrowid, document, 'Marcación por lector o digitación.')
            return MarkResult('Ingreso', now, cursor.lastrowid)

    def close_pending(self, record_id, end, reason):
        reason = reason.strip()
        if len(reason) < 8 or len(reason) > 500:
            raise AttendanceError('Explique el motivo del cierre (entre 8 y 500 caracteres).')
        with self.db.transaction() as conn:
            row = conn.execute('SELECT * FROM asistencia WHERE id=? AND fecha_salida IS NULL', (record_id,)).fetchone()
            if not row:
                raise AttendanceError('El ingreso ya fue cerrado. Actualice la lista.')
            start = datetime.fromisoformat(row['fecha_ingreso'])
            if not start <= end <= self.clock():
                raise AttendanceError('La salida debe estar entre el ingreso y la hora actual.')
            conn.execute('UPDATE asistencia SET fecha_salida=? WHERE id=?', (stamp(end), record_id))
            self._audit(conn, 'CIERRE_MANUAL', record_id, row['numero_documento'], f'Salida: {stamp(end)}. Motivo: {reason}')
