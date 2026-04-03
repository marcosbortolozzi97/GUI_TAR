
from windows.Ventana_gui import MainWindow
from linux.Ventana_gui import MainWindow
import logging
""" 
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s.%(msecs)03d [%(threadName)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S"
)
"""

if __name__ == "__main__":
    app = MainWindow()
    app.mainloop()
