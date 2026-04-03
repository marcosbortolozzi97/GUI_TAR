
# GUITAR.py
import sys

PLATFORM = sys.platform

if PLATFORM.startswith("win"):
    from windows.Ventana_gui import MainWindow
elif PLATFORM.startswith("linux"):
    from linux.Ventana_gui import MainWindow
else:
    raise RuntimeError(f"Sistema operativo no soportado: {PLATFORM}")

if __name__ == "__main__":
    app = MainWindow()
    app.mainloop()
