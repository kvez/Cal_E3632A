"""
Keysight 34465A DMM távvezérlő
Kapcsolódás: TCP socket, port 5025 (SCPI Sockets session)
Forrás: 34460-70 Operating and Service Guide
"""

import tkinter as tk
from tkinter import ttk, messagebox
import socket
import threading


SCPI_PORT = 5025


class DMM:
    """SCPI socket kapcsolat a 34465A műszerhez."""

    def __init__(self, ip: str, timeout: float = 10.0):
        self.ip = ip
        self.timeout = timeout
        self._sock: socket.socket | None = None

    def connect(self) -> str:
        """Kapcsolódás és *IDN? lekérdezés. Visszaadja a műszer azonosítóját."""
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.settimeout(self.timeout)
        self._sock.connect((self.ip, SCPI_PORT))
        return self._query("*IDN?")

    def disconnect(self):
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None

    def _send(self, cmd: str):
        if not self._sock:
            raise ConnectionError("Nincs aktív kapcsolat.")
        self._sock.sendall((cmd + "\n").encode("ascii"))

    def _query(self, cmd: str) -> str:
        self._send(cmd)
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = self._sock.recv(4096)
            if not chunk:
                break
            buf += chunk
        return buf.decode("ascii").strip()

    def configure_dcv(self, nplc: float):
        """DC feszültség mérés konfigurálása.

        Sorrend a kézikönyv szerint:
          *RST                         – gyári alapállapot visszaállítás
          CONFigure:VOLTage:DC AUTO,DEF – DC V, autorange, default felbontás
          SENSe:VOLTage:DC:NPLC <n>    – integrálási idő beállítása
        """
        self._send("*RST")
        self._send("CONFigure:VOLTage:DC AUTO,DEF")
        self._send(f"SENSe:VOLTage:DC:NPLC {nplc}")

    def read_once(self) -> float:
        """Egyetlen mérés indítása és az eredmény visszaadása.

        A READ? parancs = INITiate + FETCh? egyben:
        elindítja a mérést és blokkolva vár az eredményre.
        """
        raw = self._query("READ?")
        return float(raw)


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Keysight 34465A DMM vezérlő")
        self.resizable(False, False)
        self._dmm: DMM | None = None
        self._build_ui()

    # --- UI felépítés -------------------------------------------------------

    def _build_ui(self):
        pad = {"padx": 10, "pady": 6}

        # --- Kapcsolat frame ---
        conn_frame = ttk.LabelFrame(self, text="Kapcsolat")
        conn_frame.grid(row=0, column=0, sticky="ew", **pad)

        ttk.Label(conn_frame, text="IP cím:").grid(row=0, column=0, sticky="w", padx=6, pady=4)
        self._ip_var = tk.StringVar(value="192.168.1.100")
        self._ip_entry = ttk.Entry(conn_frame, textvariable=self._ip_var, width=18)
        self._ip_entry.grid(row=0, column=1, padx=6, pady=4)

        self._conn_btn = ttk.Button(conn_frame, text="Csatlakozás", command=self._on_connect)
        self._conn_btn.grid(row=0, column=2, padx=6, pady=4)

        self._idn_var = tk.StringVar(value="—")
        ttk.Label(conn_frame, text="Műszer:").grid(row=1, column=0, sticky="w", padx=6)
        ttk.Label(conn_frame, textvariable=self._idn_var, foreground="navy",
                  wraplength=320).grid(row=1, column=1, columnspan=2, sticky="w", padx=6, pady=2)

        # --- Mérési beállítások frame ---
        meas_frame = ttk.LabelFrame(self, text="Mérési beállítások")
        meas_frame.grid(row=1, column=0, sticky="ew", **pad)

        ttk.Label(meas_frame, text="NPLC:").grid(row=0, column=0, sticky="w", padx=6, pady=4)
        self._nplc_var = tk.IntVar(value=10)
        ttk.Radiobutton(meas_frame, text="1 NPLC  (gyorsabb, kevésbé pontos)",
                        variable=self._nplc_var, value=1).grid(row=0, column=1, sticky="w", padx=4)
        ttk.Radiobutton(meas_frame, text="10 NPLC (lassabb, pontosabb)",
                        variable=self._nplc_var, value=10).grid(row=1, column=1, sticky="w", padx=4)

        # --- Mérés frame ---
        read_frame = ttk.LabelFrame(self, text="DC feszültség mérés")
        read_frame.grid(row=2, column=0, sticky="ew", **pad)

        self._meas_btn = ttk.Button(read_frame, text="▶  Mérés", command=self._on_measure,
                                    state="disabled", width=14)
        self._meas_btn.grid(row=0, column=0, padx=10, pady=10)

        # Nagy kijelző a mért értéknek
        self._result_var = tk.StringVar(value="—")
        result_lbl = tk.Label(read_frame, textvariable=self._result_var,
                              font=("Courier New", 32, "bold"),
                              fg="#00aa00", bg="#1a1a1a",
                              width=16, anchor="e", relief="sunken", bd=2)
        result_lbl.grid(row=0, column=1, padx=10, pady=10)

        ttk.Label(read_frame, text="V DC", font=("TkDefaultFont", 14)).grid(
            row=0, column=2, padx=(0, 10))

        # --- Státuszsor ---
        self._status_var = tk.StringVar(value="Nincs kapcsolat.")
        status_bar = ttk.Label(self, textvariable=self._status_var,
                               relief="sunken", anchor="w")
        status_bar.grid(row=3, column=0, sticky="ew", padx=4, pady=(0, 4))

        self.columnconfigure(0, weight=1)

    # --- Eseménykezelők -----------------------------------------------------

    def _on_connect(self):
        if self._dmm is not None:
            # Lecsatlakozás
            self._dmm.disconnect()
            self._dmm = None
            self._conn_btn.config(text="Csatlakozás")
            self._ip_entry.config(state="normal")
            self._meas_btn.config(state="disabled")
            self._idn_var.set("—")
            self._result_var.set("—")
            self._set_status("Kapcsolat bontva.")
            return

        ip = self._ip_var.get().strip()
        if not ip:
            messagebox.showerror("Hiba", "Add meg a műszer IP-címét!")
            return

        self._set_status(f"Csatlakozás: {ip}:{SCPI_PORT} …")
        self._conn_btn.config(state="disabled")

        def do_connect():
            try:
                dmm = DMM(ip, timeout=10.0)
                idn = dmm.connect()
                self._dmm = dmm
                self.after(0, lambda: self._on_connected(idn))
            except Exception as exc:
                self.after(0, lambda: self._on_connect_error(str(exc)))

        threading.Thread(target=do_connect, daemon=True).start()

    def _on_connected(self, idn: str):
        self._idn_var.set(idn)
        self._conn_btn.config(text="Lecsatlakozás", state="normal")
        self._ip_entry.config(state="disabled")
        self._meas_btn.config(state="normal")
        self._set_status("Kapcsolódva. A műszer készen áll.")

    def _on_connect_error(self, msg: str):
        self._conn_btn.config(state="normal")
        self._set_status(f"Kapcsolódási hiba: {msg}")
        messagebox.showerror("Kapcsolódási hiba", msg)

    def _on_measure(self):
        if not self._dmm:
            return

        nplc = self._nplc_var.get()
        # Timeout: 10 NPLC@50Hz = 200ms + autozero + késleltetés → 5 mp bőven elég
        # 1 NPLC@50Hz = 20ms → 2 mp elég, de 5 mp biztonságos
        timeout = 5.0
        self._dmm.timeout = timeout

        self._meas_btn.config(state="disabled")
        self._result_var.set("…")
        self._set_status(f"Mérés folyamatban ({nplc} NPLC) …")

        def do_measure():
            try:
                self._dmm.configure_dcv(nplc)
                value = self._dmm.read_once()
                self.after(0, lambda: self._on_measure_done(value))
            except Exception as exc:
                self.after(0, lambda: self._on_measure_error(str(exc)))

        threading.Thread(target=do_measure, daemon=True).start()

    def _on_measure_done(self, value: float):
        # Formázás: 7 tizedesjegy, tudományos jelölés ha szükséges
        if abs(value) >= 1000 or (abs(value) < 0.001 and value != 0.0):
            text = f"{value:.6E}"
        else:
            text = f"{value:.7f}"
        self._result_var.set(text)
        self._meas_btn.config(state="normal")
        self._set_status("Mérés kész.")

    def _on_measure_error(self, msg: str):
        self._result_var.set("HIBA")
        self._meas_btn.config(state="normal")
        self._set_status(f"Mérési hiba: {msg}")
        messagebox.showerror("Mérési hiba", msg)

    def _set_status(self, text: str):
        self._status_var.set(text)

    def on_close(self):
        if self._dmm:
            self._dmm.disconnect()
        self.destroy()


if __name__ == "__main__":
    app = App()
    app.protocol("WM_DELETE_WINDOW", app.on_close)
    app.mainloop()
