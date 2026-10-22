"""Punto de entrada: python main.py. --smoke-test verifica también el binario."""
import argparse
import json
import logging
import sys
import tempfile
import traceback
from logging.handlers import RotatingFileHandler
from pathlib import Path
from ipsicol.config import data_dir
from ipsicol.database import Database
from ipsicol.service import AttendanceService


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--smoke-test', metavar='RESULTADO_JSON', help='Prueba aislada de inicio y recursos; no modifica la base real.')
    args = parser.parse_args()
    if args.smoke_test:
        # Se importa la UI dentro del bloque para capturar también recursos faltantes.
        output = Path(args.smoke_test)
        app = None
        try:
            from ipsicol.ui import App
            with tempfile.TemporaryDirectory() as temp:
                db = Database(Path(temp) / 'smoke.db')
                service = AttendanceService(db)
                service.save_person('1000000001', 'Persona', 'Prueba', 'Funcionario', 'Pruebas')
                app = App(db, service, test_mode=True)
                app.update()
                app.scan_entry.insert(0, '1000000001')
                app.process_scan()
                for name in ['Funcionarios', 'Visitantes', 'Consultas', 'Pendientes', 'Marcación']:
                    app.show(name)
                    app.update()
                if sys.platform == 'win32':
                    from ipsicol.window_chrome import WS_CAPTION, GWL_STYLE
                    bar = app.title_bar
                    if not bar.active or bar.native.get_style(bar.native.hwnd, GWL_STYLE) & WS_CAPTION:
                        raise RuntimeError('No se activó la barra compacta de Control y acceso.')
                if app.callback_errors:
                    raise RuntimeError('\n'.join(app.callback_errors))
                app.destroy()
                app = None
                output.write_text(json.dumps({'ok': True, 'ui': 'CustomTkinter + calendario + SQLite'}), encoding='utf-8')
            return 0
        except Exception as exc:
            if app:
                try:
                    app.destroy()
                except Exception:
                    pass
            detail = ''.join(traceback.format_exception(type(exc), exc, exc.__traceback__))
            output.write_text(json.dumps({'ok': False, 'error': str(exc), 'traceback': detail}, ensure_ascii=False), encoding='utf-8')
            print(detail, file=sys.stderr)
            return 1
    folder = data_dir()
    handler = RotatingFileHandler(folder / 'asistencia.log', maxBytes=1_000_000, backupCount=3, encoding='utf-8')
    logging.basicConfig(level=logging.WARNING, handlers=[handler], format='%(asctime)s %(levelname)s %(message)s')
    try:
        from ipsicol.ui import App
        db = Database(folder / 'asistencia.db')
        App(db, AttendanceService(db)).mainloop()
        return 0
    except Exception:
        logging.exception('No se pudo iniciar la aplicación')
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror('IPSICOL', f'No se pudo iniciar. Revise el archivo:\n{folder / "asistencia.log"}')
        root.destroy()
        return 1


if __name__ == '__main__':
    sys.exit(main())
