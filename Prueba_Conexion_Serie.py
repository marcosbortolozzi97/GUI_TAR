#!/usr/bin/env python3
"""
Pueba_Conexion_Serie.py
----------------------
Prueba de conexión serie con el TAR físico.
 
Secuencia:
    1. Abre el puerto serie
    2. Limpia el buffer (igual que serial_ReadBuffer)
    3. Envía CHA_H + CHB_H (con 700ms entre comandos, como utilizar usleep(700000))
    4. Envía GET_CONF y espera respuesta delimitada por '{' '}'
    5. Escribe log en archivo
    6. Envía START
    7. Lee datos durante DURACION_SEG segundos
    8. Envía STOP
    9. Espera cierre por inactividad y guarda .bin
 
Uso:
    python test_conexion_serie.py COM3
    python test_conexion_serie.py /dev/ttyUSB0
"""
 
import sys
import struct
import serial
import threading
import time
import logging
from pathlib import Path
 
# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s.%(msecs)03d [%(threadName)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger("test_serie")
 
 
# =============================================================
# CONFIGURACIÓN DE LA PRUEBA
# =============================================================
BAUDRATE     = 115200
DURACION_SEG = 3        # Segundos entre START y STOP
INACTIVITY_S = 2.0      # Segundos sin datos para cerrar el hilo tras STOP
CHUNK_SIZE   = 256
 
# Parámetros de histéresis fijos en CUENTAS ADC
PARAMS = {
    "umbral_cha_min": 500,
    "umbral_cha_max": 3000,
    "umbral_chb_min": 500,
    "umbral_chb_max": 3000,
}
 
# Comandos TAR
CMD_START    = b'\x25\x01'
CMD_STOP     = b'\x25\x02'
CMD_GET_CONF = b'\x25\xF0'
 
# Frame TAR
FRAME_SIZE = 8
FRAME_HDR  = 0x26
FRAME_FTR  = 0x27
 
 
# =============================================================
# CLASE DE PRUEBA
# =============================================================
class TestConexionSerie:
 
    def __init__(self, port: str):
        self.port    = port
        self._ser    = None
        self._thread = None
 
        self._running   = False
        self._streaming = False
        self._stopping  = False
 
        self._last_data_time = 0.0
 
        self._raw_bytes   = bytearray()
        self._bytes_total = 0
        self._frames_ok   = 0
        self._frames_bad  = 0
 
        # Puntero de hasta dónde ya se contaron frames.
        # Evita recontar frames anteriores en cada chunk nuevo.
        self._frames_counted_up_to = 0
 
        self._conf_response = None
 
 
    # ──────────────────────────────────────────────────────────
    # APERTURA / CIERRE
    # ──────────────────────────────────────────────────────────
    def abrir_puerto(self):
        log.info("Abriendo puerto %s a %d baud...", self.port, BAUDRATE)
        self._ser = serial.Serial(self.port, BAUDRATE, timeout=0.05)
        log.info("Puerto abierto OK")
 
    def cerrar_puerto(self):
        if self._ser and self._ser.is_open:
            self._ser.close()
            log.info("Puerto cerrado")
 
    def limpiar_buffer(self):
        """Descarta basura inicial igual que el serial_ReadBuffer"""
        self._ser.reset_input_buffer()
        log.info("Buffer de entrada limpiado")
 
 
    # ──────────────────────────────────────────────────────────
    # ENVÍO DE COMANDOS
    # ──────────────────────────────────────────────────────────
    def _enviar(self, cmd: bytes, descripcion: str):
        """Actualiza _streaming ANTES de escribir."""
        if len(cmd) >= 2:
            if cmd[1] == 0x01:    # START
                self._streaming = True
                self._stopping  = False
                log.debug("Modo STREAMING activado")
            elif cmd[1] == 0x02:  # STOP
                self._streaming = False
                log.debug("Modo STREAMING desactivado")
 
        self._ser.write(cmd)
        self._ser.flush()
        log.info(">> Enviado %s (%d bytes): %s", descripcion, len(cmd), cmd.hex())
 
    def enviar_histeresis(self):
        """
        Empaqueta y envía CHA_H + CHB_H alineado con serial_port.c:
            param = (high << 16) | low   ← to_hist(low, high) del C
            formato: [0x25][cmd][uint32 Big-Endian]  = 6 bytes
            700ms entre comandos         ← usleep(700000) del C
        """
        A_min = PARAMS["umbral_cha_min"]
        A_max = PARAMS["umbral_cha_max"]
        B_min = PARAMS["umbral_chb_min"]
        B_max = PARAMS["umbral_chb_max"]
 
        param_cha = (A_max << 16) | A_min
        param_chb = (B_max << 16) | B_min
 
        cmd_cha = struct.pack(">BBI", 0x25, 0xA0, param_cha)
        cmd_chb = struct.pack(">BBI", 0x25, 0xB0, param_chb)
 
        log.info("CHA_H: min=%d max=%d → param=0x%08X → %s",
                 A_min, A_max, param_cha, cmd_cha.hex())
        self._enviar(cmd_cha, "CHA_H")
        time.sleep(0.7)
 
        log.info("CHB_H: min=%d max=%d → param=0x%08X → %s",
                 B_min, B_max, param_chb, cmd_chb.hex())
        self._enviar(cmd_chb, "CHB_H")
        time.sleep(0.7)
 
    def enviar_get_conf(self, timeout_s: float = 2.0) -> bool:
        """
        Envía GET_CONF y espera respuesta delimitada por '{' '}'.
        Alineado con readLOG() del C.
        """
        log.info("Enviando GET_CONF...")
        self._conf_response = None
 
        self._enviar(CMD_GET_CONF, "GET_CONF")
 
        buffer   = bytearray()
        in_block = False
        t0       = time.time()
 
        while time.time() - t0 < timeout_s:
            if self._ser.in_waiting > 0:
                chunk = self._ser.read(self._ser.in_waiting)
                self._bytes_total += len(chunk)
 
                for b in chunk:
                    c = chr(b)
                    if not in_block:
                        if c == '{':
                            in_block = True
                            buffer.clear()
                    else:
                        if c == '}':
                            self._conf_response = buffer.decode("ascii", errors="ignore")
                            log.info("GET_CONF respuesta:\n{%s}", self._conf_response)
                            return True
                        else:
                            buffer.append(b)
            else:
                time.sleep(0.01)
 
        log.warning("GET_CONF timeout — no se recibió '{...}' en %.1fs", timeout_s)
        return False
 
    def guardar_log(self):
        if not self._conf_response:
            return
        ts       = time.strftime("%d%m%Y-%H%M%S")
        filename = f"{ts}_test-log.txt"
        Path(filename).write_text(self._conf_response, encoding="utf-8")
        log.info("Log guardado en %s", filename)
 
 
    # ──────────────────────────────────────────────────────────
    # HILO DE LECTURA
    # ──────────────────────────────────────────────────────────
    def _arrancar_hilo(self):
        self._running        = True
        self._last_data_time = time.time()
        self._thread = threading.Thread(
            target=self._read_loop,
            name="TestSerieReader",
            daemon=True
        )
        self._thread.start()
        log.info("Hilo de lectura arrancado")
 
    def _read_loop(self):
        log.debug("Loop de lectura iniciado")
        while self._running and self._ser:
            try:
                disponibles = self._ser.in_waiting
                if disponibles > 0:
                    chunk = self._ser.read(min(disponibles, CHUNK_SIZE))
                    if chunk:
                        self._last_data_time = time.time()
                        self._bytes_total   += len(chunk)
                        if self._streaming:
                            self._procesar_binario(chunk)
                else:
                    if self._stopping:
                        elapsed = time.time() - self._last_data_time
                        if elapsed > INACTIVITY_S:
                            log.info("Sin datos por %.1fs — cerrando hilo", elapsed)
                            break
                    time.sleep(0.005)
 
            except Exception as e:
                log.error("Error en lectura: %s", e)
                break
 
        self._running = False
        log.debug("Loop de lectura finalizado")
 
    def _procesar_binario(self, data: bytes):
        """
        Acumula bytes crudos y cuenta frames válidos.
 
        _frames_counted_up_to es un puntero al byte desde donde hay que
        seguir contando. Así cada chunk solo procesa los bytes nuevos
        y no recorre todo _raw_bytes desde el principio cada vez.
        """
        self._raw_bytes.extend(data)
        log.debug("Chunk: %d bytes | acumulado: %d bytes", len(data), len(self._raw_bytes))
 
        # Contar solo desde donde se quedó la última vez
        buf = self._raw_bytes
        i   = self._frames_counted_up_to
 
        while i + FRAME_SIZE <= len(buf):
            # Frame TAR little-endian: byte[7]=HDR (0x26), byte[0]=FTR (0x27)
            if buf[i + 7] == FRAME_HDR and buf[i] == FRAME_FTR:
                self._frames_ok  += 1
            else:
                self._frames_bad += 1
            i += FRAME_SIZE
 
        # Actualizar puntero: apunta al inicio del próximo frame completo
        self._frames_counted_up_to = i
 
    def esperar_fin_hilo(self, timeout: float = 10.0):
        if self._thread:
            self._thread.join(timeout=timeout)
            if self._thread.is_alive():
                log.warning("El hilo no terminó en %.1fs", timeout)
                self._running = False
 
 
    # ──────────────────────────────────────────────────────────
    # GUARDAR BIN
    # ──────────────────────────────────────────────────────────
    def guardar_bin(self):
        if not self._raw_bytes:
            log.warning("No hay datos binarios para guardar")
            return
        ts       = time.strftime("%d%m%Y-%H%M%S")
        filename = f"{ts}_test-raw.bin"
        Path(filename).write_bytes(self._raw_bytes)
        log.info("Binario guardado en %s (%d bytes)", filename, len(self._raw_bytes))
 
 
    # ──────────────────────────────────────────────────────────
    # RESUMEN FINAL
    # ──────────────────────────────────────────────────────────
    def imprimir_resumen(self):
        # Bytes que llegaron pero no completaron un frame de 8 bytes
        bytes_sobrantes = len(self._raw_bytes) - self._frames_counted_up_to
 
        print("\n" + "="*55)
        print("  RESUMEN DE LA PRUEBA")
        print("="*55)
        print(f"  Puerto:                    {self.port}")
        print(f"  Bytes totales rx:          {self._bytes_total}")
        print(f"  Bytes en streaming (bin):  {len(self._raw_bytes)}")
        print(f"  Frames válidos:            {self._frames_ok}")
        print(f"  Frames inválidos:          {self._frames_bad}")
        print(f"  Bytes sobrantes:           {bytes_sobrantes}  (< {FRAME_SIZE} = normal)")
        print(f"  GET_CONF recibido:         {'Sí' if self._conf_response else 'No'}")
        if self._conf_response:
            print(f"  Contenido GET_CONF:        {self._conf_response.strip()[:80]}")
        print("="*55)
 
        if self._bytes_total == 0:
            print("\n  ⚠ No se recibió ningún byte.")
            print("    → Verificar: cable USB, baudrate, puerto correcto.")
        elif not self._conf_response:
            print("\n  ⚠ No se recibió respuesta GET_CONF.")
            print("    → El TAR no respondió al comando 0xF0.")
            print("    → Verificar: baudrate, formato del comando (Big-Endian).")
        elif len(self._raw_bytes) == 0:
            print("\n  ⚠ GET_CONF OK pero ningún byte en streaming.")
            print("    → El TAR está vivo pero no emitió pulsos tras START.")
            print("    → Verificar: umbrales de histéresis vs señal de entrada.")
        elif self._frames_ok == 0:
            print("\n  ⚠ Bytes en streaming pero sin frames válidos.")
            print("    → Posible desincronización de bytes o baudrate incorrecto.")
        else:
            print(f"\n  ✓ Prueba exitosa — {self._frames_ok} frames recibidos.")
 
 
# =============================================================
# MAIN
# =============================================================
def main():
    if len(sys.argv) < 2:
        print("Uso: python Prueba_Conexion_Serie.py <puerto>")
        print("Ejemplo: python Prueba_Conexion_Serie.py COM4")
        print("         python Prueba_Conexion_Serie.py /dev/ttyUSB1")
        sys.exit(1)
 
    port = sys.argv[1]
    test = TestConexionSerie(port)
 
    try:
        # 1. Abrir puerto
        test.abrir_puerto()
 
        # 2. Limpiar buffer inicial
        test.limpiar_buffer()
 
        # 3. Arrancar hilo de lectura
        test._arrancar_hilo()
 
        # 4. Enviar histéresis (700ms entre comandos)
        test.enviar_histeresis()
 
        # 5. GET_CONF + guardar log
        test.enviar_get_conf(timeout_s=2.0)
        test.guardar_log()
 
        # 6. START
        log.info("Enviando START — adquisición por %d segundos...", DURACION_SEG)
        test._enviar(CMD_START, "START")
 
        # 7. Esperar duración
        time.sleep(DURACION_SEG)
 
        # 8. STOP
        log.info("Enviando STOP...")
        test._stopping = True
        test._enviar(CMD_STOP, "STOP")
 
        # 9. Esperar cierre del hilo
        log.info("Esperando cierre del hilo (inactividad %.1fs)...", INACTIVITY_S)
        test.esperar_fin_hilo(timeout=INACTIVITY_S + 3.0)
 
        # 10. Guardar y resumir
        test.guardar_bin()
        test.imprimir_resumen()
 
    except Exception as e:
        log.error("Error durante la prueba: %s", e, exc_info=True)
 
    finally:
        test.cerrar_puerto()
 
 
if __name__ == "__main__":
    main()