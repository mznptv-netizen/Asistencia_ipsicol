"""Barra de título compacta de Windows, sin cambiar preferencias del sistema.

Se retira únicamente WS_CAPTION del marco nativo. Se conservan la barra de
tareas, Alt+F4, minimizar/maximizar y los bordes para redimensionar la ventana.
Referencias Win32: Window Styles, SetWindowLongPtrW y SetWindowPos (Microsoft).
"""
import ctypes
import logging
import customtkinter as ctk
from PIL import Image
from .config import resource

WS_CAPTION = 0x00C00000
GWL_STYLE = -16


class WindowsFrame:
    def __init__(self, app):
        from ctypes import wintypes
        self.app = app
        self.api = ctypes.WinDLL('user32', use_last_error=True)
        pointer = ctypes.c_ssize_t
        self.get_style = self.api.GetWindowLongPtrW if ctypes.sizeof(ctypes.c_void_p) == 8 else self.api.GetWindowLongW
        self.get_style.argtypes = [wintypes.HWND, ctypes.c_int]
        self.get_style.restype = pointer
        self.set_style = self.api.SetWindowLongPtrW if ctypes.sizeof(ctypes.c_void_p) == 8 else self.api.SetWindowLongW
        self.set_style.argtypes = [wintypes.HWND, ctypes.c_int, pointer]
        self.set_style.restype = pointer
        self.api.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        self.api.GetAncestor.restype = wintypes.HWND
        self.api.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int,
                                        ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
        self.api.SetWindowPos.restype = wintypes.BOOL
        self.api.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        self.api.ShowWindow.restype = wintypes.BOOL
        self.api.IsZoomed.argtypes = [wintypes.HWND]
        self.api.IsZoomed.restype = wintypes.BOOL
        self.api.ReleaseCapture.argtypes = []
        self.api.ReleaseCapture.restype = wintypes.BOOL
        self.api.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        self.api.SendMessageW.restype = pointer

    @property
    def hwnd(self):
        # GA_ROOT obtiene la ventana superior administrada por Tk, no su hijo.
        hwnd = self.api.GetAncestor(self.app.winfo_id(), 2)
        if not hwnd:
            raise ctypes.WinError(ctypes.get_last_error())
        return hwnd

    def hide_caption(self):
        hwnd = self.hwnd
        old = self.get_style(hwnd, GWL_STYLE)
        if not old & WS_CAPTION:
            return
        ctypes.set_last_error(0)
        result = self.set_style(hwnd, GWL_STYLE, old & ~WS_CAPTION)
        if not result and ctypes.get_last_error():
            raise ctypes.WinError(ctypes.get_last_error())
        # FRAMECHANGED | NOMOVE | NOSIZE | NOZORDER | NOACTIVATE.
        flags = 0x0020 | 0x0002 | 0x0001 | 0x0004 | 0x0010
        if not self.api.SetWindowPos(hwnd, None, 0, 0, 0, 0, flags):
            error = ctypes.get_last_error()
            self.set_style(hwnd, GWL_STYLE, old)
            self.api.SetWindowPos(hwnd, None, 0, 0, 0, 0, flags)
            raise ctypes.WinError(error)

    def minimize(self):
        self.api.ShowWindow(self.hwnd, 6)  # SW_MINIMIZE

    def toggle_maximize(self):
        self.api.ShowWindow(self.hwnd, 9 if self.api.IsZoomed(self.hwnd) else 3)

    def drag(self, _event=None):
        self.api.ReleaseCapture()
        self.api.SendMessageW(self.hwnd, 0x00A1, 2, 0)  # WM_NCLBUTTONDOWN, HTCAPTION


class CompactTitleBar(ctk.CTkFrame):
    def __init__(self, app):
        super().__init__(app, height=32, fg_color='white', corner_radius=0)
        self.app = app
        self.native = WindowsFrame(app)
        self.active = False
        self.pack_propagate(False)
        with Image.open(resource('logo.png')) as image:
            self.logo = ctk.CTkImage(light_image=image.copy(), size=(22, 22))
        icon = ctk.CTkLabel(self, image=self.logo, text='', width=26, height=26)
        icon.pack(side='left', padx=(8, 4))
        title = ctk.CTkLabel(self, text='IPSICOL | Control y acceso', font=('Segoe UI', 14),
                             text_color='#25283D', anchor='w', height=28)
        title.pack(side='left', fill='x', expand=True)
        for text, command, hover in [('×', app.destroy, '#E75A62'),
                                     ('□', self.native.toggle_maximize, '#ECE8F0'),
                                     ('−', self.native.minimize, '#ECE8F0')]:
            ctk.CTkButton(self, text=text, command=command, width=40, height=30,
                         corner_radius=0, fg_color='white', hover_color=hover,
                         text_color='#25283D', font=('Segoe UI', 18)).pack(side='right')
        for widget in (self, icon, title):
            widget.bind('<B1-Motion>', self.native.drag)
            widget.bind('<Double-Button-1>', lambda _event: self.native.toggle_maximize())
        app.bind('<Map>', self.on_map, add='+')

    def on_map(self, event):
        if event.widget is self.app:
            self.after_idle(self.install)

    def install(self):
        try:
            self.native.hide_caption()
            self.active = True
        except OSError:
            # Si Windows rechaza el cambio, conserva el marco nativo utilizable.
            self.active = False
            self.grid_remove()
            logging.exception('No se pudo activar la barra compacta de Windows')
            if self.app.test_mode:
                raise
