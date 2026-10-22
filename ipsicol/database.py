"""Repositorio SQLite: conexiones cortas, migración inicial y transacciones."""
import sqlite3
import unicodedata
from datetime import datetime
from uuid import uuid4
from contextlib import contextmanager
from pathlib import Path

SCHEMA = '''
CREATE TABLE personas (
    numero_documento TEXT PRIMARY KEY NOT NULL,
    nombres TEXT NOT NULL CHECK(length(trim(nombres)) > 0),
    apellidos TEXT NOT NULL CHECK(length(trim(apellidos)) > 0),
    tipo_persona TEXT NOT NULL CHECK(tipo_persona IN ('Funcionario','Visitante')),
    cargo_o_area TEXT NOT NULL DEFAULT ''
);
CREATE TABLE asistencia (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    numero_documento TEXT NOT NULL REFERENCES personas(numero_documento) ON DELETE RESTRICT,
    fecha_ingreso TEXT NOT NULL CHECK(strftime('%s',fecha_ingreso) IS NOT NULL),
    fecha_salida TEXT CHECK(fecha_salida IS NULL OR
        (strftime('%s',fecha_salida) IS NOT NULL AND fecha_salida >= fecha_ingreso)),
    horas_trabajadas REAL GENERATED ALWAYS AS (
        CASE WHEN fecha_salida IS NULL THEN NULL ELSE
        (CAST(strftime('%s',fecha_salida) AS INTEGER) -
         CAST(strftime('%s',fecha_ingreso) AS INTEGER)) / 3600.0 END
    ) STORED
);
CREATE UNIQUE INDEX un_ingreso_abierto ON asistencia(numero_documento)
    WHERE fecha_salida IS NULL;
CREATE INDEX idx_ingreso ON asistencia(fecha_ingreso);
CREATE INDEX idx_salida ON asistencia(fecha_salida);
CREATE INDEX idx_persona_ingreso ON asistencia(numero_documento,fecha_ingreso);
CREATE TABLE codigos_lector (
    huella TEXT PRIMARY KEY NOT NULL,
    numero_documento TEXT NOT NULL REFERENCES personas(numero_documento) ON DELETE RESTRICT,
    creado_en TEXT NOT NULL
);
CREATE TABLE auditoria (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha TEXT NOT NULL,
    accion TEXT NOT NULL,
    asistencia_id INTEGER REFERENCES asistencia(id),
    numero_documento TEXT REFERENCES personas(numero_documento),
    detalle TEXT NOT NULL
);
CREATE VIEW funcionarios AS SELECT * FROM personas WHERE tipo_persona='Funcionario';
CREATE VIEW visitantes AS SELECT * FROM personas WHERE tipo_persona='Visitante';
PRAGMA user_version=2;
'''

MIGRATION_V2 = '''
CREATE TABLE personas_v2 (
    numero_documento TEXT PRIMARY KEY NOT NULL,
    nombres TEXT NOT NULL CHECK(length(trim(nombres)) > 0),
    apellidos TEXT NOT NULL CHECK(length(trim(apellidos)) > 0),
    tipo_persona TEXT NOT NULL CHECK(tipo_persona IN ('Funcionario','Visitante')),
    cargo_o_area TEXT NOT NULL DEFAULT ''
);
INSERT INTO personas_v2
    SELECT numero_documento,nombres,apellidos,
           CASE WHEN tipo_persona='Empleado' THEN 'Funcionario' ELSE tipo_persona END,
           cargo_o_area FROM personas;
DROP TABLE personas;
ALTER TABLE personas_v2 RENAME TO personas;
CREATE VIEW funcionarios AS SELECT * FROM personas WHERE tipo_persona='Funcionario';
CREATE VIEW visitantes AS SELECT * FROM personas WHERE tipo_persona='Visitante';
PRAGMA user_version=2;
'''


def folded(value):
    return ''.join(c for c in unicodedata.normalize('NFKD', value or '') if not unicodedata.combining(c)).casefold()


class Database:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.migration_backup = None
        with self.connect() as conn:
            conn.execute('PRAGMA journal_mode=WAL')
            version = conn.execute('PRAGMA user_version').fetchone()[0]
            if version == 0:
                conn.executescript('BEGIN IMMEDIATE;\n' + SCHEMA + '\nCOMMIT;')
            elif version == 1:
                # Respaldo consistente antes de reconstruir la tabla con el nuevo CHECK.
                backups = self.path.parent / 'respaldos'
                backups.mkdir(exist_ok=True)
                self.migration_backup = backups / f'antes_v2_{datetime.now():%Y%m%d_%H%M%S}_{uuid4().hex[:8]}.db'
                self.backup(self.migration_backup)
                conn.execute('PRAGMA foreign_keys=OFF')
                try:
                    conn.execute('BEGIN IMMEDIATE')
                    if conn.execute('PRAGMA user_version').fetchone()[0] == 1:
                        # execute conserva la transacción; executescript haría COMMIT implícito.
                        for statement in MIGRATION_V2.split(';'):
                            if statement.strip():
                                conn.execute(statement)
                        if conn.execute('PRAGMA foreign_key_check').fetchall():
                            raise RuntimeError('La actualización detectó referencias inválidas; se conservó la base anterior.')
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise
                finally:
                    conn.execute('PRAGMA foreign_keys=ON')
            elif version != 2:
                raise RuntimeError('Esta base pertenece a otra versión. Actualice el programa.')

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA foreign_keys=ON')
        conn.create_function('fold', 1, folded, deterministic=True)
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def transaction(self):
        with self.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def person(self, document):
        with self.connect() as conn:
            row = conn.execute('SELECT * FROM personas WHERE numero_documento=?', (document,)).fetchone()
            return dict(row) if row else None

    def people(self, query='', kind='Todos'):
        with self.connect() as conn:
            rows = conn.execute('''SELECT * FROM personas
                WHERE (?='Todos' OR tipo_persona=?)
                AND instr(fold(numero_documento||' '||apellidos||' '||nombres||' '||cargo_o_area),fold(?))>0
                ORDER BY fold(apellidos),fold(nombres),numero_documento''', (kind, kind, query.strip())).fetchall()
            return [dict(row) for row in rows]

    def linked_document(self, fingerprint):
        with self.connect() as conn:
            row = conn.execute('SELECT numero_documento FROM codigos_lector WHERE huella=?', (fingerprint,)).fetchone()
            return row[0] if row else None

    def open_records(self):
        with self.connect() as conn:
            return [dict(r) for r in conn.execute('''SELECT a.*,p.apellidos,p.nombres
                FROM asistencia a JOIN personas p USING(numero_documento)
                WHERE fecha_salida IS NULL ORDER BY fecha_ingreso''')]

    def backup(self, destination):
        destination = Path(destination)
        if destination.resolve() == self.path.resolve():
            raise ValueError('Seleccione un archivo diferente de la base activa.')
        # La API de backup incluye correctamente las transacciones del archivo WAL.
        with self.connect() as source:
            target = sqlite3.connect(destination)
            try:
                source.backup(target)
            finally:
                target.close()
