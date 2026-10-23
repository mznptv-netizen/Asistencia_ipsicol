"""Dimensiones de ventanas limitadas al área de trabajo y a la escala de Tk."""
import sys


def work_area(window):
    if sys.platform == 'win32':
        import ctypes
        from ctypes import wintypes
        rect = wintypes.RECT()
        api = ctypes.WinDLL('user32', use_last_error=True)
        api.SystemParametersInfoW.argtypes = [wintypes.UINT, wintypes.UINT, wintypes.LPVOID, wintypes.UINT]
        api.SystemParametersInfoW.restype = wintypes.BOOL
        if api.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0):  # SPI_GETWORKAREA
            return rect.left, rect.top, rect.right, rect.bottom
    return 0, 0, window.winfo_screenwidth(), window.winfo_screenheight()


def fit_window(window, width, height, *, parent=None, minimum=(600, 440)):
    left, top, right, bottom = work_area(window)
    scale = window._get_window_scaling()
    # Reserva para marco y barra nativa de diálogos, además de la barra de tareas.
    available_w = max(200, right - left - 32)
    available_h = max(180, bottom - top - 64)
    width = min(width, int(available_w / scale))
    height = min(height, int(available_h / scale))
    physical_w, physical_h = round(width * scale), round(height * scale)
    center_x = parent.winfo_rootx() + parent.winfo_width() // 2 if parent else (left + right) // 2
    center_y = parent.winfo_rooty() + parent.winfo_height() // 2 if parent else (top + bottom) // 2
    x = max(left + 8, min(center_x - physical_w // 2, right - physical_w - 16))
    y = max(top + 8, min(center_y - physical_h // 2, bottom - physical_h - 48))
    window.minsize(min(minimum[0], width), min(minimum[1], height))
    window.geometry(f'{width}x{height}+{x}+{y}')
    return width, height
