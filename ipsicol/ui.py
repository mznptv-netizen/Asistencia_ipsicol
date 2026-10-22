"""Interfaz CustomTkinter; toda escritura pasa por AttendanceService."""
import logging
import os
import sqlite3
import subprocess
import sys
import tkinter as tk
from datetime import datetime, timedelta
from tkinter import ttk, messagebox, filedialog
from tkinter.font import Font
import customtkinter as ctk
from PIL import Image
from tkcalendar import DateEntry
from .config import MAGENTA, resource, now_local
from .scanner import parse_scan, document_number
from .service import AttendanceError, StaleShift
from .reports import period_bounds, build_report
from .excel import export_report
from .formatting import duration_text, seconds_from_hours
from .window_chrome import CompactTitleBar

BG = '#F5F5F9'
INK = '#25283D'
MUTED = '#687084'
GREEN = '#177354'
TABLE_STYLE = 'IPSICOL.Treeview'


def button(master, text, command, secondary=False, **kw):
    return ctk.CTkButton(master, text=text, command=command, height=40,
        fg_color='#ECE8F0' if secondary else MAGENTA,
        hover_color='#DFD6E1' if secondary else '#941453',
        text_color=INK if secondary else 'white', font=('Segoe UI', 14), **kw)


def label(master, text, size=15, bold=False, **kw):
    return ctk.CTkLabel(master, text=text, text_color=INK,
        font=('Segoe UI', size, 'bold' if bold else 'normal'), **kw)


def table(master, headings):
    frame = ctk.CTkFrame(master, fg_color='white')
    frame.pack(fill='both', expand=True, pady=(8, 0))
    cols = [str(i) for i in range(len(headings))]
    tree = ttk.Treeview(frame, columns=cols, show='headings', selectmode='browse', style=TABLE_STYLE)
    vertical = ttk.Scrollbar(frame, orient='vertical', command=tree.yview)
    horizontal = ttk.Scrollbar(frame, orient='horizontal', command=tree.xview)
    tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
    tree.grid(row=0, column=0, sticky='nsew')
    vertical.grid(row=0, column=1, sticky='ns')
    horizontal.grid(row=1, column=0, sticky='ew')
    frame.rowconfigure(0, weight=1)
    frame.columnconfigure(0, weight=1)
    app = master.winfo_toplevel()
    tree.content_font = app.table_font
    tree.heading_font = app.table_heading_font
    tree.content_padding = round(32 * app.winfo_fpixels('1i') / 96)
    for col, title in zip(cols, headings):
        tree.heading(col, text=title)
        width = tree.heading_font.measure(title) + tree.content_padding
        tree.column(col, width=width, minwidth=width, anchor='center', stretch=False)
    # Fuentes explícitas en filas y estilo propio: todas las tablas son legibles.
    tree.tag_configure('even', background='white', font=app.table_font)
    tree.tag_configure('odd', background='#FAF4F8', font=app.table_font)
    return tree


def replace_rows(tree, rows):
    tree.delete(*tree.get_children())
    columns = tree['columns']
    widths = [tree.heading_font.measure(tree.heading(col, 'text')) for col in columns]
    measured = {}
    for i, (key, values) in enumerate(rows):
        # Medir todos los datos, no solo las filas visibles ni una muestra.
        for index, value in enumerate(values):
            text = '' if value is None else str(value)
            if text not in measured:
                measured[text] = tree.content_font.measure(text)
            widths[index] = max(widths[index], measured[text])
        tree.insert('', 'end', iid=str(key), values=values, tags=('odd' if i % 2 else 'even',))
    for col, width in zip(columns, widths):
        width += tree.content_padding
        # Evitar que Tk comprima columnas para hacerlas caber en la ventana.
        tree.column(col, width=width, minwidth=width, stretch=False)


class DatabaseLocationDialog(ctk.CTkToplevel):
    """Muestra la base que está usando esta instancia, también con ruta personalizada."""
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.database_path = app.db.path.resolve()
        self.title('Ubicación de la base de datos')
        self.geometry('760x300')
        self.minsize(600, 300)
        self.transient(app)
        label(self, 'Aquí se guardan tus registros', 22, True).pack(pady=(20, 12))
        self.path_var = tk.StringVar(value=str(self.database_path))
        self.path_field = ctk.CTkEntry(self, textvariable=self.path_var, state='readonly',
                                     font=('Segoe UI', 16), height=40)
        self.path_field.pack(fill='x', padx=24)
        label(self, 'Puedes copiar la ruta completa o abrir su carpeta.', 15).pack(pady=12)
        actions = ctk.CTkFrame(self, fg_color='transparent')
        actions.pack(pady=4)
        button(actions, 'Copiar ruta', self.copy_path, width=160).pack(side='left', padx=6)
        button(actions, 'Abrir carpeta', self.open_folder, width=160).pack(side='left', padx=6)
        button(actions, 'Cerrar', self.destroy, True, width=100).pack(side='left', padx=6)
        self.status = label(self, '', 14)
        self.status.pack(pady=8)
        self.after(100, self.activate)

    def activate(self):
        self.grab_set()
        self.path_field.focus_set()

    def copy_path(self):
        self.clipboard_clear()
        self.clipboard_append(str(self.database_path))
        self.status.configure(text='Ruta copiada.')

    def open_folder(self):
        def action():
            folder = str(self.database_path.parent)
            if sys.platform == 'win32':
                os.startfile(folder)
            else:
                subprocess.Popen(['open' if sys.platform == 'darwin' else 'xdg-open', folder])
        self.app.guard(action, parent=self)


class PersonDialog(ctk.CTkToplevel):
    def __init__(self, app, document='', person=None, fingerprint=None, mark_after=False):
        super().__init__(app)
        self.app, self.person, self.fingerprint, self.mark_after = app, person, fingerprint, mark_after
        self.fixed_person = person is not None
        self.lookup_job = None
        self.result = None
        self.title('Editar persona' if person else 'Registrar persona')
        self.geometry('590x680')
        self.resizable(False, False)
        self.transient(app)
        label(self, 'Datos de la persona', 24, True).pack(pady=(24, 8))
        label(self, 'Verifique los datos con el documento físico.', 14).pack()
        self.fields = {}
        self.document_var = tk.StringVar(value=person['numero_documento'] if person else document)
        for key, title in [('numero_documento', 'Número de documento'), ('apellidos', 'Apellidos'), ('nombres', 'Nombres'), ('cargo_o_area', 'Cargo o área')]:
            label(self, title, 14, anchor='w').pack(fill='x', padx=32, pady=(12, 2))
            entry = ctk.CTkEntry(self, height=38, font=('Segoe UI', 17),
                                **({'textvariable': self.document_var} if key == 'numero_documento' else {}))
            entry.pack(fill='x', padx=32)
            if key != 'numero_documento':
                entry.insert(0, person[key] if person else '')
            self.fields[key] = entry
        if person or document:
            self.fields['numero_documento'].configure(state='disabled')
        label(self, 'Tipo de persona', 14, anchor='w').pack(fill='x', padx=32, pady=(12, 2))
        self.kind = ctk.CTkComboBox(self, values=['Funcionario', 'Visitante'], state='readonly', height=38,
                                  font=('Segoe UI', 17), dropdown_font=('Segoe UI', 17))
        self.kind.set(person['tipo_persona'] if person else ('Visitante' if app.page_name == 'Visitantes' else 'Funcionario'))
        self.kind.pack(fill='x', padx=32)
        self.lookup_note = label(self, '', 14, wraplength=520)
        self.lookup_note.pack(fill='x', padx=32, pady=(10, 0))
        caption = 'Guardar y registrar asistencia' if mark_after else 'Guardar persona'
        self.save_button = button(self, caption, self.save)
        self.save_button.pack(fill='x', padx=32, pady=(16, 8))
        button(self, 'Cancelar', self.destroy, True).pack(fill='x', padx=32)
        if not person and not document:
            self.document_var.trace_add('write', self.schedule_lookup)
            self.fields['numero_documento'].bind('<Return>', self.lookup_event)
            self.fields['numero_documento'].bind('<Tab>', self.capture_tab)
        self.after(100, self.activate)

    def capture_tab(self, _event):
        self.fields['numero_documento'].insert(tk.INSERT, '\t')
        return 'break'

    def schedule_lookup(self, *_args):
        if self.lookup_job:
            self.after_cancel(self.lookup_job)
        self.lookup_job = self.after(250, self.lookup_document)

    def lookup_event(self, _event):
        self.lookup_document()
        return 'break'

    def lookup_document(self):
        if self.lookup_job:
            self.after_cancel(self.lookup_job)
            self.lookup_job = None
        if self.fixed_person:
            return
        try:
            person = self.app.service.lookup_person(self.document_var.get())
        except ValueError:
            person = None
        previous = self.person
        self.person = person
        if person or previous:
            for key in ('apellidos', 'nombres', 'cargo_o_area'):
                self.fields[key].delete(0, 'end')
                self.fields[key].insert(0, person[key] if person else '')
        if person:
            self.kind.set(person['tipo_persona'])
        self.lookup_note.configure(text='Persona registrada: datos recuperados automáticamente.' if person else 'Documento nuevo: complete los datos por primera vez.')
        self.save_button.configure(text='Actualizar persona' if person else 'Guardar persona')

    def destroy(self):
        if self.lookup_job:
            self.after_cancel(self.lookup_job)
            self.lookup_job = None
        super().destroy()

    def activate(self):
        self.grab_set()
        self.fields['apellidos' if self.fields['numero_documento'].get() else 'numero_documento'].focus_set()

    def save(self):
        def action():
            if self.lookup_job:
                self.lookup_document()
            values = {key: field.get() for key, field in self.fields.items()}
            document = self.person['numero_documento'] if self.person else values['numero_documento']
            self.result = self.app.service.save_person(document, values['nombres'], values['apellidos'],
                self.kind.get(), values['cargo_o_area'], update=bool(self.person), fingerprint=self.fingerprint)
            self.destroy()
        self.app.guard(action, parent=self)


class LinkDialog(ctk.CTkToplevel):
    def __init__(self, app, fingerprint):
        super().__init__(app)
        self.app, self.fingerprint, self.result = app, fingerprint, None
        self.title('Primera lectura: verificar documento')
        self.geometry('600x440')
        self.transient(app)
        self.resizable(False, False)
        label(self, 'Vincular código del lector', 23, True).pack(pady=(24, 12))
        label(self, 'La lectura contiene más que un número.\nEscriba la cédula que ve en el documento físico.\nDespués, escanee de nuevo para confirmar una lectura estable.', 15).pack(pady=8)
        self.document = ctk.CTkEntry(self, placeholder_text='Número de cédula, sin puntos', height=42)
        self.document.pack(fill='x', padx=32, pady=12)
        self.again = ctk.CTkEntry(self, placeholder_text='Escanee otra vez aquí', show='•', height=42)
        self.again.pack(fill='x', padx=32, pady=8)
        self.again.bind('<Return>', lambda e: 'break')
        self.again.bind('<Tab>', self.insert_tab)
        button(self, 'Verificar lectura y continuar', self.verify).pack(fill='x', padx=32, pady=(22, 8))
        button(self, 'Cancelar / usar ingreso manual', self.destroy, True).pack(fill='x', padx=32)
        self.after(100, self.activate)

    def activate(self):
        self.grab_set()
        self.document.focus_set()

    def insert_tab(self, _event):
        self.again.insert(tk.INSERT, '\t')
        return 'break'

    def verify(self):
        def action():
            doc = document_number(self.document.get())
            if parse_scan(self.again.get()).fingerprint != self.fingerprint:
                raise ValueError('Las dos lecturas no coinciden. Configure el lector o use el número manual; no se vinculó el código.')
            self.result = doc
            self.destroy()
        self.app.guard(action, parent=self)


class CloseDialog(ctk.CTkToplevel):
    def __init__(self, app, record):
        super().__init__(app)
        self.app, self.record = app, record
        self.result = False
        self.title('Resolver salida pendiente')
        self.geometry('600x470')
        self.transient(app)
        self.resizable(False, False)
        label(self, 'Registrar la salida real', 24, True).pack(pady=20)
        label(self, f"{record['apellidos']} {record['nombres']}\nIngreso: {record['fecha_ingreso']}", 16).pack(pady=8)
        line = ctk.CTkFrame(self, fg_color='transparent')
        line.pack(pady=16)
        self.day = DateEntry(line, locale='es_CO', date_pattern='dd/mm/yyyy', width=14, state='readonly', font=('Segoe UI', 13))
        self.day.pack(side='left', padx=8)
        self.hour = ctk.CTkEntry(line, width=145, placeholder_text='HH:MM:SS')
        self.hour.pack(side='left', padx=8)
        self.hour.insert(0, now_local().strftime('%H:%M:%S'))
        label(self, 'Motivo de la corrección (queda en auditoría)', 14).pack(pady=8)
        self.reason = ctk.CTkEntry(self, height=42)
        self.reason.pack(fill='x', padx=32, pady=8)
        button(self, 'Confirmar salida', self.save).pack(fill='x', padx=32, pady=(18, 8))
        button(self, 'Cancelar', self.destroy, True).pack(fill='x', padx=32)
        self.after(100, self.grab_set)

    def save(self):
        def action():
            try:
                hour = datetime.strptime(self.hour.get(), '%H:%M:%S').time()
            except ValueError:
                raise ValueError('Escriba la hora en formato HH:MM:SS. Ejemplo: 17:30:00.') from None
            end = datetime.combine(self.day.get_date(), hour)
            if not messagebox.askyesno('Confirmar', f"¿Registrar salida el {end:%d/%m/%Y a las %H:%M:%S}?", parent=self):
                return
            self.app.service.close_pending(self.record['id'], end, self.reason.get())
            self.result = True
            self.destroy()
        self.app.guard(action, parent=self)


class App(ctk.CTk):
    def __init__(self, db, service, test_mode=False):
        ctk.set_appearance_mode('light')
        super().__init__()
        self.db, self.service = db, service
        self.test_mode = test_mode
        self.callback_errors = []
        self.preview_job = None
        self.suspend_preview = False
        self.reports_ready = False
        self.title('IPSICOL | Control y acceso')
        self.geometry('1250x790')
        self.minsize(1050, 700)
        self.configure(fg_color=BG)
        self.scan_busy = False
        self.current_report = None
        self.page_name = 'Marcación'
        try:
            self.iconbitmap(str(resource('ipsicol.ico')))
        except tk.TclError:
            pass
        self.configure_styles()
        self.grid_columnconfigure(1, weight=1)
        self.title_bar = None
        body_row = 0
        if sys.platform == 'win32':
            self.title_bar = CompactTitleBar(self)
            self.title_bar.grid(row=0, column=0, columnspan=2, sticky='ew')
            body_row = 1
        self.grid_rowconfigure(body_row, weight=1)
        sidebar = ctk.CTkFrame(self, width=224, corner_radius=0, fg_color='white')
        sidebar.grid(row=body_row, column=0, sticky='nsew')
        sidebar.grid_propagate(False)
        with Image.open(resource('logo.png')) as img:
            ratio = min(164 / img.width, 120 / img.height)
            self.logo = ctk.CTkImage(light_image=img.copy(), size=(int(img.width*ratio), int(img.height*ratio)))
        ctk.CTkLabel(sidebar, image=self.logo, text='').pack(pady=(16, 8))
        label(sidebar, 'CONTROL Y\nACCESO', 18, True).pack(pady=(0, 12))
        self.nav = {}
        for name in ['Marcación', 'Funcionarios', 'Visitantes', 'Consultas', 'Pendientes']:
            self.nav[name] = button(sidebar, name, lambda n=name: self.show(n), True, width=190)
            self.nav[name].pack(padx=16, pady=4)
        button(sidebar, '+ Añadir persona', self.add_person, width=190).pack(padx=16, pady=(12, 6))
        button(sidebar, 'Copia de seguridad', self.backup, True, width=190).pack(padx=16, pady=6)
        self.location_button = button(sidebar, 'Ubicación de la base', self.show_database_location, True, width=190)
        self.location_button.pack(padx=16, pady=6)
        label(sidebar, 'Base de datos local\nHora de Colombia', 13).pack(side='bottom', pady=8)
        self.content = ctk.CTkFrame(self, fg_color='transparent')
        self.content.grid(row=body_row, column=1, sticky='nsew', padx=26, pady=14)
        self.content.rowconfigure(2, weight=1)
        self.content.columnconfigure(0, weight=1)
        self.title_label = label(self.content, 'Marcación', 29, True, anchor='w')
        self.title_label.grid(row=0, column=0, sticky='ew')
        self.clock_label = label(self.content, '', 14, anchor='w')
        self.clock_label.grid(row=1, column=0, sticky='ew', pady=(4, 18))
        self.pages = {}
        for name in ['Marcación', 'Funcionarios', 'Visitantes', 'Consultas', 'Pendientes']:
            frame = ctk.CTkFrame(self.content, fg_color='transparent')
            frame.grid(row=2, column=0, sticky='nsew')
            self.pages[name] = frame
        self.build_scan()
        self.build_people()
        self.build_reports()
        self.build_pending()
        self.show('Marcación')
        self.tick()
        if self.title_bar:
            self.after_idle(self.title_bar.install)

    def show_database_location(self):
        return DatabaseLocationDialog(self)

    def configure_styles(self):
        style = ttk.Style(self)
        style.theme_use('clam')
        self.table_font = Font(self, family='Segoe UI', size=18)
        self.table_heading_font = Font(self, family='Segoe UI', size=17, weight='bold')
        scale = self.winfo_fpixels('1i') / 96
        rowheight = max(round(54 * scale), self.table_font.metrics('linespace') + round(20 * scale))
        style.configure(TABLE_STYLE, background='white', fieldbackground='white', foreground=INK,
                        rowheight=rowheight, font=self.table_font, borderwidth=0)
        style.configure(TABLE_STYLE + '.Heading', background='#EFE8EE', foreground=INK,
                        font=self.table_heading_font, padding=(12, round(10 * scale)))
        style.map(TABLE_STYLE, background=[('selected', MAGENTA)], foreground=[('selected', 'white')])

    def tick(self):
        self.clock_label.configure(text=now_local().strftime('%d/%m/%Y  •  %H:%M:%S  |  Bogotá/Colombia'))
        self.after(1000, self.tick)

    def guard(self, action, parent=None):
        try:
            return action()
        except (ValueError, sqlite3.IntegrityError) as exc:
            if self.test_mode:
                raise
            messagebox.showwarning('Revise los datos', str(exc), parent=parent or self)
        except (OSError, sqlite3.Error):
            if self.test_mode:
                raise
            logging.exception('Fallo de archivo o base de datos')
            messagebox.showerror('No se pudo completar', 'Compruebe permisos, espacio disponible y que el archivo no esté abierto. Consulte asistencia.log.', parent=parent or self)

    def report_callback_exception(self, exc, value, tb):
        logging.error('Error de interfaz', exc_info=(exc, value, tb))
        self.callback_errors.append(f'{exc.__name__}: {value}')
        if not self.test_mode:
            messagebox.showerror('Error de interfaz', f'No se pudo completar la operación en {self.page_name}.\n\n{exc.__name__}: {value}\n\nDetalle guardado en asistencia.log.', parent=self)

    def show(self, name):
        self.page_name = name
        self.pages[name].tkraise()
        self.title_label.configure(text=name)
        for key, nav in self.nav.items():
            nav.configure(fg_color=MAGENTA if key == name else '#ECE8F0', text_color='white' if key == name else INK)
        if name == 'Marcación':
            self.refresh_today()
            self.after(100, self.scan_entry.focus_set)
        elif name in ('Funcionarios', 'Visitantes'):
            self.refresh_people()
        elif name == 'Consultas':
            self.run_report()
        else:
            self.refresh_pending()

    def build_scan(self):
        page = self.pages['Marcación']
        card = ctk.CTkFrame(page, fg_color='white', corner_radius=16)
        self.scan_card = card
        card.pack(fill='x')
        label(card, 'Escanee la cédula o escriba el documento', 22, True).pack(pady=(12, 2))
        label(card, 'Presione Enter para registrar ingreso o salida.', 15).pack(pady=(0, 8))
        line = ctk.CTkFrame(card, fg_color='transparent')
        line.pack(fill='x', padx=24)
        self.scan_var = tk.StringVar()
        self.scan_entry = ctk.CTkEntry(line, textvariable=self.scan_var, height=44, font=('Segoe UI', 23), placeholder_text='Número de documento')
        self.scan_entry.pack(side='left', fill='x', expand=True, padx=(0, 12))
        self.scan_entry.bind('<Return>', self.scan_event)
        self.scan_entry.bind('<Tab>', self.scan_tab)
        button(line, 'Registrar', self.process_scan, width=130).pack(side='right')
        identity = ctk.CTkFrame(card, fg_color='transparent')
        identity.pack(fill='x', padx=24, pady=(6, 0))
        identity.columnconfigure((0, 1), weight=1)
        self.name_vars = {key: tk.StringVar() for key in ('apellidos', 'nombres')}
        for col, (key, title) in enumerate((('apellidos', 'Apellidos'), ('nombres', 'Nombres'))):
            label(identity, title, 15, anchor='w').grid(row=0, column=col, sticky='ew', padx=6)
            ctk.CTkEntry(identity, textvariable=self.name_vars[key], state='readonly', height=38,
                         font=('Segoe UI', 18)).grid(row=1, column=col, sticky='ew', padx=6)
        self.scan_status = label(card, 'Listo para recibir una lectura', 21, True, wraplength=760)
        self.scan_status.pack(pady=(8, 2))
        self.person_status = label(card, 'Los datos aparecerán aquí al registrar.', 16, wraplength=760)
        self.person_status.pack(pady=(0, 8))
        self.scan_var.trace_add('write', self.schedule_preview)
        label(page, 'Movimientos de hoy', 18, True, anchor='w').pack(fill='x', pady=(12, 0))
        self.today_tree = table(page, ['Documento', 'Apellidos y nombres', 'Ingreso', 'Salida', 'Tiempo de permanencia', 'Estado'])

    def schedule_preview(self, *_args):
        if self.preview_job:
            self.after_cancel(self.preview_job)
            self.preview_job = None
        if not self.suspend_preview:
            # Quitar de inmediato el titular anterior evita asociarlo a otra lectura.
            self.set_person_preview(None)
            self.preview_job = self.after(250, self.preview_person)

    def set_person_preview(self, person):
        for key, variable in self.name_vars.items():
            variable.set(person[key] if person else '')
        self.person_status.configure(text=f"{person['numero_documento']}  •  {person['tipo_persona']}  •  {person['cargo_o_area']}" if person else '')

    def preview_person(self):
        if self.preview_job:
            self.after_cancel(self.preview_job)
            self.preview_job = None
        try:
            person = self.service.lookup_person(self.scan_var.get())
        except ValueError:
            person = None
        self.set_person_preview(person)
        if not person and self.scan_var.get().strip():
            self.person_status.configure(text='Documento sin ficha reconocida: pulse Enter para registrarlo o verificar la lectura.')

    def scan_tab(self, _event):
        self.scan_entry.insert(tk.INSERT, '\t')
        return 'break'

    def scan_event(self, _event):
        self.process_scan()
        return 'break'

    def process_scan(self):
        if self.scan_busy:
            return
        text = self.scan_entry.get()
        if not text.strip():
            return
        self.scan_busy = True
        self.scan_status.configure(text='Lectura recibida; pendiente de registro', text_color=MUTED)
        self.person_status.configure(text='')
        self.suspend_preview = True
        self.scan_var.set('')
        self.suspend_preview = False
        try:
            self.guard(lambda: self.handle_scan(text))
        finally:
            self.scan_busy = False
            self.scan_entry.focus_set()

    def handle_scan(self, text):
        scan = parse_scan(text)
        doc = scan.document or self.db.linked_document(scan.fingerprint)
        pending_link = None
        if not doc:
            dialog = LinkDialog(self, scan.fingerprint)
            self.wait_window(dialog)
            if not dialog.result:
                self.scan_status.configure(text='Lectura cancelada; no se registró asistencia', text_color=MUTED)
                return
            doc, pending_link = dialog.result, scan.fingerprint
        person = self.db.person(doc)
        if not person:
            dialog = PersonDialog(self, document=doc, fingerprint=pending_link, mark_after=True)
            self.wait_window(dialog)
            if not dialog.result:
                self.scan_status.configure(text='Lectura cancelada; no se registró asistencia', text_color=MUTED)
                return
            person = self.db.person(doc)
        elif pending_link:
            if not messagebox.askyesno('Verificar titular', f"Documento: {doc}\n{person['apellidos']} {person['nombres']}\n\n¿Coincide con el documento físico? Se vinculará el código y se registrará asistencia.", parent=self):
                self.scan_status.configure(text='Vinculación cancelada; no se registró asistencia', text_color=MUTED)
                return
            self.service.link(pending_link, doc)
        self.set_person_preview(person)
        try:
            result = self.service.mark(doc)
        except StaleShift as exc:
            self.scan_status.configure(text='Salida anterior pendiente de revisión', text_color='#A56000')
            messagebox.showwarning('Revisar turno anterior', str(exc), parent=self)
            return
        color = MUTED if result.action == 'Duplicado ignorado' else GREEN
        extra = f'\nTiempo dentro: {duration_text(seconds_from_hours(result.hours))}' if result.hours is not None else ''
        self.scan_status.configure(text=f'{result.action}  •  {result.at:%H:%M:%S}{extra}', text_color=color)
        self.refresh_today()
        if result.action != 'Duplicado ignorado':
            self.bell()

    def refresh_today(self):
        start, end = period_bounds('Día', now_local().date())
        report = build_report(self.db, start, end)
        rows = sorted(report.details, key=lambda r: r['fecha_ingreso'], reverse=True)
        replace_rows(self.today_tree, [(r['id'], [r['numero_documento'], r['nombre'], r['fecha_ingreso'], r['fecha_salida'] or '—', duration_text(seconds_from_hours(r['horas_trabajadas'])), 'Dentro' if not r['fecha_salida'] else 'Fuera']) for r in rows])

    def build_people(self):
        self.people_queries, self.people_trees = {}, {}
        for name in ('Funcionarios', 'Visitantes'):
            page = self.pages[name]
            label(page, f'Listado de {name.lower()}', 20, True, anchor='w').pack(fill='x', pady=(0, 10))
            line = ctk.CTkFrame(page, fg_color='transparent')
            line.pack(fill='x')
            entry = ctk.CTkEntry(line, placeholder_text='Documento, apellidos, nombres o área', height=42, font=('Segoe UI', 17))
            entry.pack(side='left', fill='x', expand=True, padx=(0, 10))
            entry.bind('<Return>', lambda e: self.refresh_people())
            self.people_queries[name] = entry
            button(line, 'Buscar', self.refresh_people, width=95).pack(side='left', padx=5)
            button(line, 'Editar selección', self.edit_person, True).pack(side='left', padx=5)
            tree = table(page, ['Documento', 'Apellidos', 'Nombres', 'Cargo o área'])
            tree.bind('<Double-1>', lambda e: self.edit_person())
            self.people_trees[name] = tree

    def refresh_people(self):
        name = self.page_name
        kind = 'Funcionario' if name == 'Funcionarios' else 'Visitante'
        self.guard(lambda: replace_rows(self.people_trees[name], [(p['numero_documento'], [p[k] for k in ['numero_documento','apellidos','nombres','cargo_o_area']]) for p in self.db.people(self.people_queries[name].get(), kind)]))

    def add_person(self):
        dialog = PersonDialog(self)
        self.wait_window(dialog)
        if self.page_name in ('Funcionarios', 'Visitantes'):
            self.refresh_people()
        elif self.page_name == 'Marcación':
            self.scan_entry.focus_set()

    def edit_person(self):
        selection = self.people_trees[self.page_name].selection()
        if not selection:
            messagebox.showinfo('Seleccione una persona', 'Haga clic sobre la persona que desea editar.', parent=self)
            return
        dialog = PersonDialog(self, person=self.db.person(selection[0]))
        self.wait_window(dialog)
        self.refresh_people()

    def build_reports(self):
        page = self.pages['Consultas']
        filters = ctk.CTkFrame(page, fg_color='white')
        filters.pack(fill='x')
        top = ctk.CTkFrame(filters, fg_color='transparent')
        top.pack(fill='x', padx=14, pady=12)
        self.mode = ctk.CTkComboBox(top, values=['Día','Semana','Mes','Año','Personalizado'], state='readonly', width=150, command=self.period_changed)
        self.mode.pack(side='left', padx=(0, 8))
        self.mode.set('Día')
        self.from_date = DateEntry(top, locale='es_CO', date_pattern='dd/mm/yyyy', state='readonly', width=12, font=('Segoe UI', 12))
        self.from_date.pack(side='left', padx=8)
        label(top, 'Hasta:', 14).pack(side='left', padx=4)
        # DateEntry no puede insertar su fecha inicial si nace deshabilitado.
        # Inicializar primero y deshabilitar después evita un campo vacío/IndexError.
        self.to_date = DateEntry(top, locale='es_CO', date_pattern='dd/mm/yyyy', state='readonly', width=12, font=('Segoe UI', 12))
        self.from_date.set_date(now_local().date())
        self.to_date.set_date(now_local().date())
        self.to_date.configure(state='disabled')
        self.to_date.pack(side='left', padx=8)
        self.from_date.bind('<<DateEntrySelected>>', lambda e: self.invalidate_report())
        self.to_date.bind('<<DateEntrySelected>>', lambda e: self.invalidate_report())
        self.report_kind = ctk.CTkComboBox(top, values=['Todos','Funcionario','Visitante'], state='readonly', width=140, command=lambda _: self.invalidate_report())
        self.report_kind.set('Todos')
        self.report_kind.pack(side='right')
        bottom = ctk.CTkFrame(filters, fg_color='transparent')
        bottom.pack(fill='x', padx=14, pady=(0, 12))
        self.query_var = tk.StringVar()
        self.report_query = ctk.CTkEntry(bottom, textvariable=self.query_var, placeholder_text='Buscar por documento, nombre o área', height=40)
        self.report_query.pack(side='left', fill='x', expand=True, padx=(0, 10))
        self.report_query.bind('<Return>', lambda e: self.run_report())
        button(bottom, 'Consultar', self.run_report, width=110).pack(side='left', padx=4)
        self.export_button = button(bottom, 'Exportar Excel', self.export, True, width=140)
        self.export_button.pack(side='left', padx=4)
        self.metrics = label(page, 'Seleccione un periodo y pulse Consultar.', 16, bold=True, anchor='w', wraplength=900)
        self.metrics.pack(fill='x', pady=(16, 4))
        label(page, 'Tiempo confirmado de permanencia. Los ingresos sin salida quedan pendientes.', 15, anchor='w').pack(fill='x')
        tabs = ctk.CTkTabview(page, fg_color='white', segmented_button_selected_color=MAGENTA)
        tabs.pack(fill='both', expand=True, pady=(8, 0))
        details = tabs.add('Detalle')
        summary = tabs.add('Resumen por persona')
        self.detail_tree = table(details, ['Documento','Apellidos y nombres','Tipo','Ingreso real','Salida real','Tiempo en periodo','Estado'])
        self.summary_tree = table(summary, ['Documento','Apellidos y nombres','Días con presencia','Tiempo en periodo','Pendientes'])
        self.reports_ready = True
        self.query_var.trace_add('write', lambda *_: self.invalidate_report())

    def period_changed(self, _value):
        self.to_date.configure(state='readonly' if self.mode.get() == 'Personalizado' else 'disabled')
        self.invalidate_report()

    def invalidate_report(self):
        self.current_report = None
        if self.reports_ready:
            self.export_button.configure(state='disabled')
            self.metrics.configure(text='Filtros modificados: pulse Consultar para actualizar.')
            replace_rows(self.detail_tree, [])
            replace_rows(self.summary_tree, [])

    def run_report(self):
        def action():
            self.invalidate_report()
            # El calendario final no interviene en periodos de un solo selector.
            final_date = self.to_date.get_date() if self.mode.get() == 'Personalizado' else None
            start, end = period_bounds(self.mode.get(), self.from_date.get_date(), final_date)
            report = build_report(self.db, start, end, self.report_query.get(), self.report_kind.get())
            replace_rows(self.detail_tree, [(r['id'], [r['numero_documento'],r['nombre'],r['tipo_persona'],r['fecha_ingreso'],r['fecha_salida'] or 'Pendiente', duration_text(r['seconds'] if r['fecha_salida'] else None), 'Cerrado' if r['fecha_salida'] else 'Pendiente']) for r in report.details])
            replace_rows(self.summary_tree, [(r['numero_documento'], [r['numero_documento'],r['nombre'],r['days'],duration_text(r['seconds']),r['pending']]) for r in report.summary])
            total = duration_text(sum(r['seconds'] for r in report.details))
            self.metrics.configure(text=f"{start:%d/%m/%Y} — {end-timedelta(days=1):%d/%m/%Y}  |  Personas: {len(report.summary)}\nTiempo total: {total}  |  Pendientes: {report.open_count}")
            self.current_report = report
            self.export_button.configure(state='normal')
        self.guard(action)

    def export(self):
        if self.current_report is None:
            return
        target = filedialog.asksaveasfilename(parent=self, title='Exportar consulta a Excel', defaultextension='.xlsx',
            filetypes=[('Libro de Excel', '*.xlsx')], initialfile=f'Asistencia_IPSICOL_{now_local():%Y%m%d_%H%M%S}.xlsx')
        if target:
            def action():
                export_report(self.current_report, target)
                messagebox.showinfo('Exportación completa', 'Se guardaron el detalle, el resumen por persona y los criterios de consulta.', parent=self)
            self.guard(action)

    def build_pending(self):
        page = self.pages['Pendientes']
        label(page, 'Ingresos que todavía no tienen salida', 21, True, anchor='w').pack(fill='x')
        label(page, 'Seleccione un ingreso y registre la fecha y hora reales de salida.\nLa corrección conserva su motivo en la auditoría.', 15, anchor='w').pack(fill='x', pady=12)
        line = ctk.CTkFrame(page, fg_color='transparent')
        line.pack(fill='x')
        button(line, 'Resolver selección', self.close_selected).pack(side='left', padx=(0, 10))
        button(line, 'Actualizar', self.refresh_pending, True).pack(side='left')
        self.pending_tree = table(page, ['Registro','Documento','Apellidos y nombres','Ingreso','Tiempo transcurrido'])

    def refresh_pending(self):
        self.pending_records = {r['id']: r for r in self.db.open_records()}
        replace_rows(self.pending_tree, [(r['id'], [r['id'],r['numero_documento'],r['apellidos']+' '+r['nombres'],r['fecha_ingreso'],duration_text((now_local()-datetime.fromisoformat(r['fecha_ingreso'])).total_seconds())]) for r in self.pending_records.values()])

    def close_selected(self):
        selection = self.pending_tree.selection()
        if not selection:
            messagebox.showinfo('Seleccione un ingreso', 'Seleccione la fila que desea resolver.', parent=self)
            return
        dialog = CloseDialog(self, self.pending_records[int(selection[0])])
        self.wait_window(dialog)
        self.refresh_pending()

    def backup(self):
        target = filedialog.asksaveasfilename(parent=self, title='Guardar copia de seguridad', defaultextension='.db',
            filetypes=[('Base SQLite', '*.db')], initialfile=f'IPSICOL_respaldo_{now_local():%Y%m%d_%H%M%S}.db')
        if target:
            def action():
                self.db.backup(target)
                messagebox.showinfo('Copia completa', 'La copia de la base de datos se guardó correctamente.', parent=self)
            self.guard(action)
