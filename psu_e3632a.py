"""
Keysight E3632A DC tápegység vezérlő
Kapcsolódás: RS-232 soros port (null-modem/crossover kábel)
Forrás: Keysight E3632A User's Guide (9018-01309)

RS-232 konfiguráció (gyári alapbeállítás):
  9600 baud · 8 adatbit · paritás nélkül · 2 stopbit
  DTR/DSR hardver kézfogás · null-modem (DTE–DTE) kábel

Tartományok:
  LOW  (P15V): 0–15 V, 0–7 A
  HIGH (P30V): 0–30 V, 0–4 A

A multiméter: Keysight 34465A – ugyanaz a dmm_34465a.py osztály.
"""

import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import serial
import serial.tools.list_ports
import threading
import time
import csv

try:
    import matplotlib
    matplotlib.use("TkAgg")
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
    _HAS_MPL = True
except ImportError:
    _HAS_MPL = False

# ─── Tartomány konstansok ────────────────────────────────────────────────────

RANGE_LOW  = {"label": "LOW  (0–15 V / 0–7 A)", "vmax": 15.0, "imax": 7.0,
               "vovp_max": 16.0, "code": "P15V"}
RANGE_HIGH = {"label": "HIGH (0–30 V / 0–4 A)", "vmax": 30.0, "imax": 4.0,
               "vovp_max": 32.0, "code": "P30V"}
RANGES = [RANGE_LOW, RANGE_HIGH]

BAUD_OPTIONS = [300, 600, 1200, 2400, 4800, 9600]

# ─── PSU kommunikációs osztály ───────────────────────────────────────────────

class PSU:
    """SCPI RS-232 kapcsolat a Keysight E3632A tápegységhez.

    A kézikönyv szerint (p. 115) az RS-232-ön minden kommunikáció előtt
    el kell küldeni a SYST:REM parancsot – ez engedélyezi a távvezérlést.
    Lecsatlakozáskor SYST:LOC visszaadja a kezelést a frontpanelnek.

    A tápegység 2 stopbitet használ (fix), DTR/DSR kézfogással.
    A \n (ASCII 10) a parancs terminátor.
    """

    def __init__(self, port: str, baudrate: int = 9600, timeout: float = 5.0):
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self._ser: serial.Serial | None = None
        self._lock = threading.Lock()

    def connect(self) -> str:
        """Megnyitja a soros portot és visszaadja a *IDN? választ."""
        self._ser = serial.Serial(
            port=self.port,
            baudrate=self.baudrate,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_TWO,
            timeout=self.timeout,
            dsrdtr=True,        # DTR/DSR hardver kézfogás (kézikönyv p. 76)
        )
        time.sleep(0.1)         # port stabilizálás
        # Kötelező lépés RS-232-n: remote módba helyezés (kézikönyv p. 115)
        self._send_raw("SYST:REM")
        time.sleep(0.15)
        return self._query_raw("*IDN?")

    def disconnect(self):
        if self._ser and self._ser.is_open:
            try:
                self._send_raw("SYST:LOC")   # frontpanel visszaadása
            except Exception:
                pass
            try:
                self._ser.close()
            except OSError:
                pass
        self._ser = None

    @property
    def connected(self) -> bool:
        return self._ser is not None and self._ser.is_open

    def _send_raw(self, cmd: str):
        """Parancs küldés lock nélkül (belső használatra)."""
        if not self.connected:
            raise ConnectionError("Nincs aktív kapcsolat.")
        self._ser.reset_input_buffer()
        self._ser.write((cmd + "\n").encode("ascii"))
        self._ser.flush()

    def _query_raw(self, cmd: str) -> str:
        """Lekérdezés lock nélkül (belső használatra)."""
        if not self.connected:
            raise ConnectionError("Nincs aktív kapcsolat.")
        self._ser.reset_input_buffer()
        self._ser.write((cmd + "\n").encode("ascii"))
        self._ser.flush()
        line = self._ser.readline()
        if not line:
            raise TimeoutError(f"Időtúllépés – nincs válasz a(z) '{cmd}' parancsra.")
        return line.decode("ascii").strip()

    def send(self, cmd: str):
        """Thread-safe parancs küldés."""
        with self._lock:
            self._send_raw(cmd)

    def query(self, cmd: str) -> str:
        """Thread-safe lekérdezés."""
        with self._lock:
            return self._query_raw(cmd)

    # ── Tartomány ─────────────────────────────────────────────────────────────

    def set_range(self, code: str):
        """Tartomány: 'P15V' (LOW, gyár) vagy 'P30V' (HIGH)."""
        self.send(f"VOLT:RANG {code}")

    def get_range_code(self) -> str:
        return self.query("VOLT:RANG?")     # "P15V" vagy "P30V"

    # ── Setpoint beállítás ────────────────────────────────────────────────────

    def apply(self, volts: float, amps: float):
        """Feszültség és áramlimit egyidejű beállítása (APPL parancs)."""
        self.send(f"APPL {volts:.4f},{amps:.4f}")

    def get_setpoints(self) -> tuple[float, float]:
        """Visszaadja a (U_set, I_set) setpoint értékeket."""
        raw = self.query("APPL?")           # pl. '"15.00000, 4.00000"'
        parts = raw.strip('"').split(",")
        return float(parts[0]), float(parts[1])

    # ── Kimenet ───────────────────────────────────────────────────────────────

    def output_on(self):
        self.send("OUTP ON")

    def output_off(self):
        self.send("OUTP OFF")

    def get_output_state(self) -> bool:
        return self.query("OUTP?") == "1"

    # ── Tényleges kimenet mérése ──────────────────────────────────────────────

    def measure_voltage(self) -> float:
        """A tápegység belső érzékelőjével mért tényleges kimeneti feszültség."""
        return float(self.query("MEAS:VOLT?"))

    def measure_current(self) -> float:
        """A tápegység belső érzékelőjével mért tényleges kimeneti áram."""
        return float(self.query("MEAS:CURR?"))

    def measure_both(self) -> tuple[float, float]:
        """Visszaadja a (U_meas, I_meas) mért értékeket."""
        u = self.measure_voltage()
        i = self.measure_current()
        return u, i

    # ── OVP (túlfeszültség védelem) ───────────────────────────────────────────

    def set_ovp(self, volts: float, enabled: bool):
        self.send(f"VOLT:PROT {volts:.4f}")
        self.send("VOLT:PROT:STAT " + ("ON" if enabled else "OFF"))

    def get_ovp(self) -> tuple[float, bool]:
        level = float(self.query("VOLT:PROT?"))
        state = self.query("VOLT:PROT:STAT?") == "1"
        return level, state

    def clear_ovp(self):
        self.send("VOLT:PROT:CLE")

    def is_ovp_tripped(self) -> bool:
        return self.query("VOLT:PROT:TRIP?") == "1"

    # ── OCP (túláram védelem) ─────────────────────────────────────────────────

    def set_ocp(self, amps: float, enabled: bool):
        self.send(f"CURR:PROT {amps:.4f}")
        self.send("CURR:PROT:STAT " + ("ON" if enabled else "OFF"))

    def get_ocp(self) -> tuple[float, bool]:
        level = float(self.query("CURR:PROT?"))
        state = self.query("CURR:PROT:STAT?") == "1"
        return level, state

    def clear_ocp(self):
        self.send("CURR:PROT:CLE")

    def is_ocp_tripped(self) -> bool:
        return self.query("CURR:PROT:TRIP?") == "1"

    # ── Rendszer ──────────────────────────────────────────────────────────────

    def get_error(self) -> str:
        return self.query("SYST:ERR?")

    def reset(self):
        """*RST + SYST:REM (reset után remote módba kell visszahelyezni)."""
        self.send("*RST")
        time.sleep(0.3)
        self.send("SYST:REM")

    def beep(self):
        self.send("SYST:BEEP")


# ─── GUI ─────────────────────────────────────────────────────────────────────

class App(tk.Tk):

    POLL_INTERVAL_MS = 1000     # automatikus mérés frissítési idő

    def __init__(self):
        super().__init__()
        self.title("Keysight E3632A tápegység vezérlő")
        self.resizable(False, False)

        self._psu: PSU | None = None
        self._range = RANGE_LOW             # aktív tartomány (szoftver oldal)
        self._output_on = False
        self._poll_job: str | None = None   # after() job azonosítója

        # Sweep állapot
        self._sweep_running = False
        self._sweep_data: list[tuple[float, float]] = []  # [(U_meas, I_meas), …]
        self._graph_win: tk.Toplevel | None = None
        self._graph_ax = None
        self._graph_canvas = None

        self._build_ui()
        self._refresh_ports()

    # ─── UI felépítés ────────────────────────────────────────────────────────

    def _build_ui(self):
        pad = {"padx": 10, "pady": 6}

        # ── Kapcsolat frame ──────────────────────────────────────────────────
        conn_frame = ttk.LabelFrame(self, text="Kapcsolat (RS-232)")
        conn_frame.grid(row=0, column=0, sticky="ew", **pad)

        ttk.Label(conn_frame, text="Port:").grid(row=0, column=0, sticky="w", padx=6, pady=4)
        self._port_var = tk.StringVar()
        self._port_cb = ttk.Combobox(conn_frame, textvariable=self._port_var,
                                     state="readonly", width=12)
        self._port_cb.grid(row=0, column=1, padx=4, pady=4)

        ttk.Button(conn_frame, text="⟳", width=3,
                   command=self._refresh_ports).grid(row=0, column=2, padx=2)

        ttk.Label(conn_frame, text="Baud:").grid(row=0, column=3, sticky="w", padx=(12, 4))
        self._baud_var = tk.IntVar(value=9600)
        baud_cb = ttk.Combobox(conn_frame, textvariable=self._baud_var,
                               values=BAUD_OPTIONS, state="readonly", width=7)
        baud_cb.grid(row=0, column=4, padx=4, pady=4)

        self._conn_btn = ttk.Button(conn_frame, text="Csatlakozás",
                                    command=self._on_connect, width=14)
        self._conn_btn.grid(row=0, column=5, padx=10, pady=4)

        ttk.Label(conn_frame, text="Műszer:").grid(row=1, column=0, sticky="w", padx=6)
        self._idn_var = tk.StringVar(value="—")
        ttk.Label(conn_frame, textvariable=self._idn_var,
                  foreground="navy", wraplength=400).grid(
            row=1, column=1, columnspan=5, sticky="w", padx=6, pady=2)

        ttk.Button(conn_frame, text="*RST", command=self._on_reset,
                   width=6).grid(row=2, column=5, padx=10, pady=2)
        ttk.Label(conn_frame, text="(gyári alapállapot visszaállítás)",
                  foreground="gray").grid(row=2, column=0, columnspan=5,
                                          sticky="w", padx=6)

        # ── Tartomány frame ──────────────────────────────────────────────────
        rng_frame = ttk.LabelFrame(self, text="Kimeneti tartomány")
        rng_frame.grid(row=1, column=0, sticky="ew", **pad)

        self._range_var = tk.IntVar(value=0)   # 0 = LOW, 1 = HIGH
        for i, r in enumerate(RANGES):
            ttk.Radiobutton(rng_frame, text=r["label"], variable=self._range_var,
                            value=i, command=self._on_range_change,
                            state="disabled").grid(
                row=0, column=i, sticky="w", padx=16, pady=6)
        self._range_radios = rng_frame.winfo_children()

        # ── Setpoint frame ───────────────────────────────────────────────────
        sp_frame = ttk.LabelFrame(self, text="Kimenet beállítása")
        sp_frame.grid(row=2, column=0, sticky="ew", **pad)

        # Feszültség
        ttk.Label(sp_frame, text="Feszültség:").grid(row=0, column=0, sticky="w",
                                                       padx=8, pady=6)
        self._volt_var = tk.StringVar(value="0.0000")
        self._volt_entry = ttk.Entry(sp_frame, textvariable=self._volt_var,
                                     width=10, state="disabled")
        self._volt_entry.grid(row=0, column=1, padx=4)
        ttk.Label(sp_frame, text="V").grid(row=0, column=2, sticky="w")

        # Áramlimit
        ttk.Label(sp_frame, text="Áramlimit:").grid(row=0, column=3, sticky="w",
                                                      padx=(20, 8))
        self._curr_var = tk.StringVar(value="0.0000")
        self._curr_entry = ttk.Entry(sp_frame, textvariable=self._curr_var,
                                     width=10, state="disabled")
        self._curr_entry.grid(row=0, column=4, padx=4)
        ttk.Label(sp_frame, text="A").grid(row=0, column=5, sticky="w")

        self._apply_btn = ttk.Button(sp_frame, text="▶  Beállít",
                                     command=self._on_apply, state="disabled",
                                     width=12)
        self._apply_btn.grid(row=0, column=6, padx=16)

        # Tartomány-korlát kijelzés
        self._range_info_var = tk.StringVar(value="")
        ttk.Label(sp_frame, textvariable=self._range_info_var,
                  foreground="gray").grid(row=1, column=0, columnspan=7,
                                           sticky="w", padx=8, pady=(0, 4))

        # ── Védelem frame ─────────────────────────────────────────────────────
        prot_frame = ttk.LabelFrame(self, text="Védelem (OVP / OCP)")
        prot_frame.grid(row=3, column=0, sticky="ew", **pad)

        # OVP sor
        ttk.Label(prot_frame, text="OVP szint:").grid(row=0, column=0,
                                                        sticky="w", padx=8, pady=6)
        self._ovp_var = tk.StringVar(value="16.0000")
        self._ovp_entry = ttk.Entry(prot_frame, textvariable=self._ovp_var,
                                    width=10, state="disabled")
        self._ovp_entry.grid(row=0, column=1, padx=4)
        ttk.Label(prot_frame, text="V").grid(row=0, column=2, sticky="w")

        self._ovp_en_var = tk.BooleanVar(value=True)
        self._ovp_chk = ttk.Checkbutton(prot_frame, text="Engedélyezve",
                                         variable=self._ovp_en_var,
                                         state="disabled")
        self._ovp_chk.grid(row=0, column=3, padx=12)

        self._ovp_apply_btn = ttk.Button(prot_frame, text="Alkalmaz",
                                          command=self._on_ovp_apply,
                                          state="disabled", width=9)
        self._ovp_apply_btn.grid(row=0, column=4, padx=4)

        self._ovp_clr_btn = ttk.Button(prot_frame, text="Reset OVP",
                                        command=self._on_ovp_clear,
                                        state="disabled", width=9)
        self._ovp_clr_btn.grid(row=0, column=5, padx=4)

        self._ovp_trip_var = tk.StringVar(value="")
        ttk.Label(prot_frame, textvariable=self._ovp_trip_var,
                  foreground="red", font=("TkDefaultFont", 9, "bold")).grid(
            row=0, column=6, padx=8)

        # OCP sor
        ttk.Label(prot_frame, text="OCP szint:").grid(row=1, column=0,
                                                        sticky="w", padx=8, pady=6)
        self._ocp_var = tk.StringVar(value="7.5000")
        self._ocp_entry = ttk.Entry(prot_frame, textvariable=self._ocp_var,
                                    width=10, state="disabled")
        self._ocp_entry.grid(row=1, column=1, padx=4)
        ttk.Label(prot_frame, text="A").grid(row=1, column=2, sticky="w")

        self._ocp_en_var = tk.BooleanVar(value=True)
        self._ocp_chk = ttk.Checkbutton(prot_frame, text="Engedélyezve",
                                         variable=self._ocp_en_var,
                                         state="disabled")
        self._ocp_chk.grid(row=1, column=3, padx=12)

        self._ocp_apply_btn = ttk.Button(prot_frame, text="Alkalmaz",
                                          command=self._on_ocp_apply,
                                          state="disabled", width=9)
        self._ocp_apply_btn.grid(row=1, column=4, padx=4)

        self._ocp_clr_btn = ttk.Button(prot_frame, text="Reset OCP",
                                        command=self._on_ocp_clear,
                                        state="disabled", width=9)
        self._ocp_clr_btn.grid(row=1, column=5, padx=4)

        self._ocp_trip_var = tk.StringVar(value="")
        ttk.Label(prot_frame, textvariable=self._ocp_trip_var,
                  foreground="red", font=("TkDefaultFont", 9, "bold")).grid(
            row=1, column=6, padx=8)

        # ── Kimenet vezérlés + mérés frame ───────────────────────────────────
        out_frame = ttk.LabelFrame(self, text="Kimenet vezérlés")
        out_frame.grid(row=4, column=0, sticky="ew", **pad)

        # Nagy ON/OFF gomb
        self._outp_btn = tk.Button(
            out_frame, text="KIMENET\nKI", font=("TkDefaultFont", 14, "bold"),
            bg="#cc3333", fg="white", activebackground="#aa2222",
            activeforeground="white", width=10, height=3,
            state="disabled", command=self._on_output_toggle, relief="raised", bd=4)
        self._outp_btn.grid(row=0, column=0, rowspan=2, padx=16, pady=10)

        # Mérési kijelzők
        meas_inner = ttk.Frame(out_frame)
        meas_inner.grid(row=0, column=1, padx=8, pady=8, sticky="w")

        ttk.Label(meas_inner, text="Feszültség:", anchor="w",
                  width=12).grid(row=0, column=0, sticky="w")
        self._vmeas_var = tk.StringVar(value="—")
        tk.Label(meas_inner, textvariable=self._vmeas_var,
                 font=("Courier New", 22, "bold"),
                 fg="#00bb00", bg="#1a1a1a", width=10,
                 anchor="e", relief="sunken", bd=2).grid(row=0, column=1, padx=4)
        ttk.Label(meas_inner, text="V", font=("TkDefaultFont", 12)).grid(
            row=0, column=2, padx=(0, 16))

        ttk.Label(meas_inner, text="Áram:", anchor="w",
                  width=12).grid(row=1, column=0, sticky="w", pady=(6, 0))
        self._imeas_var = tk.StringVar(value="—")
        tk.Label(meas_inner, textvariable=self._imeas_var,
                 font=("Courier New", 22, "bold"),
                 fg="#00bb00", bg="#1a1a1a", width=10,
                 anchor="e", relief="sunken", bd=2).grid(row=1, column=1, padx=4,
                                                          pady=(6, 0))
        ttk.Label(meas_inner, text="A", font=("TkDefaultFont", 12)).grid(
            row=1, column=2, padx=(0, 16), pady=(6, 0))

        # Automata frissítés
        poll_frame = ttk.Frame(out_frame)
        poll_frame.grid(row=1, column=1, sticky="w", padx=8, pady=(0, 6))

        self._auto_poll_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(poll_frame, text="Automata frissítés",
                        variable=self._auto_poll_var,
                        command=self._on_poll_toggle).grid(row=0, column=0)

        self._meas_btn = ttk.Button(poll_frame, text="Mérés most",
                                     command=self._on_measure_now,
                                     state="disabled", width=12)
        self._meas_btn.grid(row=0, column=1, padx=12)

        ttk.Button(poll_frame, text="Hiba?",
                   command=self._on_read_error,
                   width=7).grid(row=0, column=2, padx=4)
        # (a Hiba? gomb is disabled lesz – ld. _set_controls_state)
        self._err_btn = poll_frame.winfo_children()[-1]

        # ── Sweep frame ──────────────────────────────────────────────────────
        self._build_sweep_frame(row=5)

        # ── Státuszsor ───────────────────────────────────────────────────────
        self._status_var = tk.StringVar(value="Nincs kapcsolat.")
        ttk.Label(self, textvariable=self._status_var,
                  relief="sunken", anchor="w").grid(
            row=6, column=0, sticky="ew", padx=4, pady=(0, 4))

        self.columnconfigure(0, weight=1)

    # ─── Sweep frame felépítése ──────────────────────────────────────────────

    def _build_sweep_frame(self, row: int):
        pad = {"padx": 10, "pady": 6}
        sw = ttk.LabelFrame(self, text="Sweep mérés")
        sw.grid(row=row, column=0, sticky="ew", **pad)

        # ── 1. sor: típus + paraméterek ──────────────────────────────────────
        self._sw_type_var = tk.IntVar(value=0)   # 0 = V sweep, 1 = I sweep
        ttk.Radiobutton(sw, text="Feszültség sweep", variable=self._sw_type_var,
                        value=0, command=self._sw_on_type_change,
                        state="disabled").grid(row=0, column=0, padx=8, pady=4, sticky="w")
        ttk.Radiobutton(sw, text="Áram sweep", variable=self._sw_type_var,
                        value=1, command=self._sw_on_type_change,
                        state="disabled").grid(row=0, column=1, padx=4, pady=4, sticky="w")

        ttk.Separator(sw, orient="vertical").grid(row=0, column=2, rowspan=2,
                                                   sticky="ns", padx=8)

        ttk.Label(sw, text="Start:").grid(row=0, column=3, sticky="e", padx=(4, 2))
        self._sw_start_var = tk.StringVar(value="0.0")
        self._sw_start_entry = ttk.Entry(sw, textvariable=self._sw_start_var,
                                          width=8, state="disabled")
        self._sw_start_entry.grid(row=0, column=4, padx=2)
        self._sw_unit1_var = tk.StringVar(value="V")
        ttk.Label(sw, textvariable=self._sw_unit1_var, width=2).grid(
            row=0, column=5, sticky="w")

        ttk.Label(sw, text="Stop:").grid(row=0, column=6, sticky="e", padx=(8, 2))
        self._sw_stop_var = tk.StringVar(value="15.0")
        self._sw_stop_entry = ttk.Entry(sw, textvariable=self._sw_stop_var,
                                         width=8, state="disabled")
        self._sw_stop_entry.grid(row=0, column=7, padx=2)
        self._sw_unit2_var = tk.StringVar(value="V")
        ttk.Label(sw, textvariable=self._sw_unit2_var, width=2).grid(
            row=0, column=8, sticky="w")

        ttk.Label(sw, text="Lépések:").grid(row=0, column=9, sticky="e", padx=(8, 2))
        self._sw_steps_var = tk.IntVar(value=30)
        ttk.Spinbox(sw, textvariable=self._sw_steps_var, from_=2, to=500,
                    width=5, state="disabled").grid(row=0, column=10, padx=2)
        self._sw_spinbox = sw.winfo_children()[-1]

        ttk.Label(sw, text="Késleltetés:").grid(row=0, column=11, sticky="e",
                                                  padx=(8, 2))
        self._sw_delay_var = tk.StringVar(value="0.30")
        ttk.Entry(sw, textvariable=self._sw_delay_var, width=6,
                  state="disabled").grid(row=0, column=12, padx=2)
        self._sw_delay_entry = sw.winfo_children()[-1]
        ttk.Label(sw, text="s").grid(row=0, column=13, sticky="w")

        # ── 2. sor: gombok + progress ─────────────────────────────────────────
        self._sw_start_btn = ttk.Button(sw, text="▶  Sweep",
                                         command=self._on_sweep_start,
                                         state="disabled", width=10)
        self._sw_start_btn.grid(row=1, column=0, padx=8, pady=(0, 6), sticky="w")

        self._sw_stop_btn = ttk.Button(sw, text="■  Stop",
                                        command=self._on_sweep_stop,
                                        state="disabled", width=8)
        self._sw_stop_btn.grid(row=1, column=1, padx=4, pady=(0, 6), sticky="w")

        self._sw_graph_btn = ttk.Button(sw, text="📊 Grafikon",
                                         command=self._open_graph_window,
                                         state="disabled", width=10)
        self._sw_graph_btn.grid(row=1, column=3, columnspan=2, padx=4,
                                 pady=(0, 6), sticky="w")

        self._sw_csv_btn = ttk.Button(sw, text="💾 CSV",
                                       command=self._export_csv,
                                       state="disabled", width=8)
        self._sw_csv_btn.grid(row=1, column=5, padx=4, pady=(0, 6), sticky="w")

        # Progress bar
        self._sw_progress_var = tk.DoubleVar(value=0.0)
        self._sw_progress = ttk.Progressbar(sw, variable=self._sw_progress_var,
                                             maximum=100, length=200)
        self._sw_progress.grid(row=1, column=6, columnspan=5, padx=8,
                                pady=(0, 6), sticky="ew")

        self._sw_info_var = tk.StringVar(value="")
        ttk.Label(sw, textvariable=self._sw_info_var, foreground="navy",
                  width=28).grid(row=1, column=11, columnspan=3, sticky="w",
                                  padx=4, pady=(0, 6))

        # Sweep típus-specifikus widget lista (enable/disable)
        self._sw_widgets = [
            self._sw_start_entry, self._sw_stop_entry,
            self._sw_spinbox, self._sw_delay_entry,
        ]
        self._sw_radios = [w for w in sw.winfo_children()
                           if isinstance(w, ttk.Radiobutton)]

    # ─── Port lista frissítés ────────────────────────────────────────────────

    def _refresh_ports(self):
        ports = [p.device for p in serial.tools.list_ports.comports()]
        self._port_cb["values"] = ports
        if ports and not self._port_var.get():
            self._port_var.set(ports[0])

    # ─── Kapcsolat ───────────────────────────────────────────────────────────

    def _on_connect(self):
        if self._psu is not None:
            # Lecsatlakozás
            self._stop_poll()
            self._psu.disconnect()
            self._psu = None
            self._output_on = False
            self._conn_btn.config(text="Csatlakozás")
            self._idn_var.set("—")
            self._vmeas_var.set("—")
            self._imeas_var.set("—")
            self._ovp_trip_var.set("")
            self._ocp_trip_var.set("")
            self._set_controls_state(False)
            self._set_status("Kapcsolat bontva.")
            return

        port = self._port_var.get().strip()
        if not port:
            messagebox.showerror("Hiba", "Válassz soros portot!")
            return

        baud = self._baud_var.get()
        self._set_status(f"Csatlakozás: {port} @ {baud} baud …")
        self._conn_btn.config(state="disabled")

        def do_connect():
            try:
                psu = PSU(port, baudrate=baud, timeout=5.0)
                idn = psu.connect()
                # Aktuális állapot olvasása
                range_code = psu.get_range_code()
                u_set, i_set = psu.get_setpoints()
                ovp_lvl, ovp_en = psu.get_ovp()
                ocp_lvl, ocp_en = psu.get_ocp()
                out_state = psu.get_output_state()
                self._psu = psu
                self.after(0, lambda: self._on_connected(
                    idn, range_code, u_set, i_set,
                    ovp_lvl, ovp_en, ocp_lvl, ocp_en, out_state))
            except Exception as exc:
                self.after(0, lambda: self._on_connect_error(str(exc)))

        threading.Thread(target=do_connect, daemon=True).start()

    def _on_connected(self, idn, range_code, u_set, i_set,
                      ovp_lvl, ovp_en, ocp_lvl, ocp_en, out_state):
        self._idn_var.set(idn)
        self._conn_btn.config(text="Lecsatlakozás", state="normal")

        # Tartomány szinkronizálás
        idx = 1 if range_code == "P30V" else 0
        self._range_var.set(idx)
        self._range = RANGES[idx]
        self._update_range_info()

        # Setpoint szinkronizálás
        self._volt_var.set(f"{u_set:.4f}")
        self._curr_var.set(f"{i_set:.4f}")

        # Védelem szinkronizálás
        self._ovp_var.set(f"{ovp_lvl:.4f}")
        self._ovp_en_var.set(ovp_en)
        self._ocp_var.set(f"{ocp_lvl:.4f}")
        self._ocp_en_var.set(ocp_en)

        # Kimenet állapot
        self._output_on = out_state
        self._update_output_btn()

        self._set_controls_state(True)
        self._set_status("Kapcsolódva. Műszer készen áll.")

        if self._auto_poll_var.get():
            self._start_poll()

    def _on_connect_error(self, msg: str):
        self._conn_btn.config(state="normal")
        self._set_status(f"Kapcsolódási hiba: {msg}")
        messagebox.showerror("Kapcsolódási hiba", msg)

    # ─── Reset ───────────────────────────────────────────────────────────────

    def _on_reset(self):
        if not self._psu:
            return
        if not messagebox.askyesno("*RST megerősítés",
                                    "Gyári alapállapot visszaállítás?\n"
                                    "A kimenet KI lesz kapcsolva!"):
            return
        self._stop_poll()
        self._set_status("*RST végrehajtása …")

        def do_reset():
            try:
                self._psu.reset()
                range_code = self._psu.get_range_code()
                u_set, i_set = self._psu.get_setpoints()
                ovp_lvl, ovp_en = self._psu.get_ovp()
                ocp_lvl, ocp_en = self._psu.get_ocp()
                out_state = self._psu.get_output_state()
                self.after(0, lambda: self._on_reset_done(
                    range_code, u_set, i_set,
                    ovp_lvl, ovp_en, ocp_lvl, ocp_en, out_state))
            except Exception as exc:
                self.after(0, lambda: self._set_status(f"Reset hiba: {exc}"))

        threading.Thread(target=do_reset, daemon=True).start()

    def _on_reset_done(self, range_code, u_set, i_set,
                       ovp_lvl, ovp_en, ocp_lvl, ocp_en, out_state):
        idx = 1 if range_code == "P30V" else 0
        self._range_var.set(idx)
        self._range = RANGES[idx]
        self._update_range_info()
        self._volt_var.set(f"{u_set:.4f}")
        self._curr_var.set(f"{i_set:.4f}")
        self._ovp_var.set(f"{ovp_lvl:.4f}")
        self._ovp_en_var.set(ovp_en)
        self._ocp_var.set(f"{ocp_lvl:.4f}")
        self._ocp_en_var.set(ocp_en)
        self._output_on = out_state
        self._update_output_btn()
        self._vmeas_var.set("—")
        self._imeas_var.set("—")
        self._ovp_trip_var.set("")
        self._ocp_trip_var.set("")
        self._set_status("*RST kész – gyári alapállapot visszaállítva.")
        if self._auto_poll_var.get():
            self._start_poll()

    # ─── Tartomány ───────────────────────────────────────────────────────────

    def _on_range_change(self):
        new_range = RANGES[self._range_var.get()]
        if not self._psu:
            self._range = new_range
            self._update_range_info()
            return

        self._set_status(f"Tartomány váltás: {new_range['code']} …")

        def do_range():
            try:
                self._psu.set_range(new_range["code"])
                self.after(0, lambda: self._on_range_done(new_range))
            except Exception as exc:
                self.after(0, lambda: self._set_status(f"Tartomány hiba: {exc}"))

        threading.Thread(target=do_range, daemon=True).start()

    def _on_range_done(self, new_range: dict):
        self._range = new_range
        self._update_range_info()
        self._sw_on_type_change()           # sweep stop érték frissítése
        self._set_status(f"Tartomány: {new_range['code']} aktív.")

    def _update_range_info(self):
        r = self._range
        self._range_info_var.set(
            f"Max. feszültség: {r['vmax']:.0f} V  ·  "
            f"Max. áram: {r['imax']:.0f} A  ·  "
            f"Max. OVP: {r['vovp_max']:.0f} V")

    # ─── Setpoint beállítás ───────────────────────────────────────────────────

    def _on_apply(self):
        if not self._psu:
            return
        try:
            v = float(self._volt_var.get())
            i = float(self._curr_var.get())
        except ValueError:
            messagebox.showerror("Beviteli hiba", "Érvényes számot adj meg!")
            return

        r = self._range
        if not (0.0 <= v <= r["vmax"]):
            messagebox.showerror("Beviteli hiba",
                                  f"Feszültség {r['vmax']} V-on belül legyen! (0 – {r['vmax']} V)")
            return
        if not (0.0 <= i <= r["imax"]):
            messagebox.showerror("Beviteli hiba",
                                  f"Áram {r['imax']} A-en belül legyen! (0 – {r['imax']} A)")
            return

        self._apply_btn.config(state="disabled")
        self._set_status(f"Beállítás: {v:.4f} V / {i:.4f} A …")

        def do_apply():
            try:
                self._psu.apply(v, i)
                self.after(0, self._on_apply_done)
            except Exception as exc:
                self.after(0, lambda: self._on_error(f"Beállítási hiba: {exc}"))

        threading.Thread(target=do_apply, daemon=True).start()

    def _on_apply_done(self):
        self._apply_btn.config(state="normal")
        self._set_status("Setpoint beállítva.")

    # ─── OVP ─────────────────────────────────────────────────────────────────

    def _on_ovp_apply(self):
        if not self._psu:
            return
        try:
            v = float(self._ovp_var.get())
        except ValueError:
            messagebox.showerror("Beviteli hiba", "Érvényes OVP értéket adj meg!")
            return
        if not (0.0 <= v <= self._range["vovp_max"]):
            messagebox.showerror("Beviteli hiba",
                                  f"OVP max {self._range['vovp_max']} V!")
            return
        en = self._ovp_en_var.get()
        self._set_status(f"OVP: {v:.4f} V ({'be' if en else 'ki'}) …")

        def do_ovp():
            try:
                self._psu.set_ovp(v, en)
                self.after(0, lambda: self._set_status("OVP beállítva."))
            except Exception as exc:
                self.after(0, lambda: self._on_error(f"OVP hiba: {exc}"))

        threading.Thread(target=do_ovp, daemon=True).start()

    def _on_ovp_clear(self):
        if not self._psu:
            return

        def do_clear():
            try:
                self._psu.clear_ovp()
                self.after(0, lambda: (self._ovp_trip_var.set(""),
                                       self._set_status("OVP visszaállítva.")))
            except Exception as exc:
                self.after(0, lambda: self._on_error(f"OVP reset hiba: {exc}"))

        threading.Thread(target=do_clear, daemon=True).start()

    # ─── OCP ─────────────────────────────────────────────────────────────────

    def _on_ocp_apply(self):
        if not self._psu:
            return
        try:
            i = float(self._ocp_var.get())
        except ValueError:
            messagebox.showerror("Beviteli hiba", "Érvényes OCP értéket adj meg!")
            return
        # OCP max kicsit magasabb mint az áramlimit max
        ocp_max = self._range["imax"] * 1.1
        if not (0.0 <= i <= ocp_max):
            messagebox.showerror("Beviteli hiba",
                                  f"OCP kb. {ocp_max:.1f} A alatt legyen!")
            return
        en = self._ocp_en_var.get()
        self._set_status(f"OCP: {i:.4f} A ({'be' if en else 'ki'}) …")

        def do_ocp():
            try:
                self._psu.set_ocp(i, en)
                self.after(0, lambda: self._set_status("OCP beállítva."))
            except Exception as exc:
                self.after(0, lambda: self._on_error(f"OCP hiba: {exc}"))

        threading.Thread(target=do_ocp, daemon=True).start()

    def _on_ocp_clear(self):
        if not self._psu:
            return

        def do_clear():
            try:
                self._psu.clear_ocp()
                self.after(0, lambda: (self._ocp_trip_var.set(""),
                                       self._set_status("OCP visszaállítva.")))
            except Exception as exc:
                self.after(0, lambda: self._on_error(f"OCP reset hiba: {exc}"))

        threading.Thread(target=do_clear, daemon=True).start()

    # ─── Kimenet BE/KI ────────────────────────────────────────────────────────

    def _on_output_toggle(self):
        if not self._psu:
            return
        new_state = not self._output_on
        self._outp_btn.config(state="disabled")
        self._set_status(("Kimenet bekapcsolása …" if new_state
                           else "Kimenet kikapcsolása …"))

        def do_toggle():
            try:
                if new_state:
                    self._psu.output_on()
                else:
                    self._psu.output_off()
                self.after(0, lambda: self._on_output_toggled(new_state))
            except Exception as exc:
                self.after(0, lambda: self._on_error(f"Kimenet hiba: {exc}"))

        threading.Thread(target=do_toggle, daemon=True).start()

    def _on_output_toggled(self, state: bool):
        self._output_on = state
        self._update_output_btn()
        self._set_status("Kimenet " + ("BEKAPCSOLVA." if state else "kikapcsolva."))
        if not state:
            self._vmeas_var.set("—")
            self._imeas_var.set("—")

    def _update_output_btn(self):
        if self._output_on:
            self._outp_btn.config(text="KIMENET\nBE", bg="#1a7a1a",
                                   activebackground="#145a14", state="normal")
        else:
            self._outp_btn.config(text="KIMENET\nKI", bg="#cc3333",
                                   activebackground="#aa2222", state="normal")

    # ─── Mérés ───────────────────────────────────────────────────────────────

    def _on_measure_now(self):
        if not self._psu:
            return
        self._meas_btn.config(state="disabled")

        def do_meas():
            try:
                u, i = self._psu.measure_both()
                ovp_t = self._psu.is_ovp_tripped()
                ocp_t = self._psu.is_ocp_tripped()
                self.after(0, lambda: self._on_meas_done(u, i, ovp_t, ocp_t))
            except Exception as exc:
                self.after(0, lambda: self._on_error(f"Mérési hiba: {exc}"))

        threading.Thread(target=do_meas, daemon=True).start()

    def _on_meas_done(self, u: float, i: float, ovp_t: bool, ocp_t: bool):
        self._vmeas_var.set(f"{u:.5f}")
        self._imeas_var.set(f"{i:.5f}")
        self._ovp_trip_var.set("▲ OVP!" if ovp_t else "")
        self._ocp_trip_var.set("▲ OCP!" if ocp_t else "")
        self._meas_btn.config(state="normal")

    # ─── Automata polling ─────────────────────────────────────────────────────

    def _start_poll(self):
        if self._poll_job:
            return
        self._poll_tick()

    def _stop_poll(self):
        if self._poll_job:
            self.after_cancel(self._poll_job)
            self._poll_job = None

    def _poll_tick(self):
        """Periodikus mérés – főszálon indítja, háttérszálon fut a lekérdezés."""
        self._poll_job = None
        if not self._psu or not self._auto_poll_var.get():
            return

        def do_poll():
            try:
                u, i = self._psu.measure_both()
                ovp_t = self._psu.is_ovp_tripped()
                ocp_t = self._psu.is_ocp_tripped()
                self.after(0, lambda: self._on_poll_result(u, i, ovp_t, ocp_t))
            except Exception:
                # Polling hiba esetén nem dobjuk el a kapcsolatot, csak várunk
                self.after(0, self._schedule_next_poll)

        threading.Thread(target=do_poll, daemon=True).start()

    def _on_poll_result(self, u: float, i: float, ovp_t: bool, ocp_t: bool):
        self._vmeas_var.set(f"{u:.5f}")
        self._imeas_var.set(f"{i:.5f}")
        self._ovp_trip_var.set("▲ OVP!" if ovp_t else "")
        self._ocp_trip_var.set("▲ OCP!" if ocp_t else "")
        self._schedule_next_poll()

    def _schedule_next_poll(self):
        if self._auto_poll_var.get() and self._psu:
            self._poll_job = self.after(self.POLL_INTERVAL_MS, self._poll_tick)

    def _on_poll_toggle(self):
        if self._auto_poll_var.get() and self._psu:
            self._start_poll()
        else:
            self._stop_poll()

    # ─── Hiba lekérdezés ─────────────────────────────────────────────────────

    def _on_read_error(self):
        if not self._psu:
            return

        def do_err():
            try:
                err = self._psu.get_error()
                self.after(0, lambda: self._show_error_msg(err))
            except Exception as exc:
                self.after(0, lambda: self._on_error(str(exc)))

        threading.Thread(target=do_err, daemon=True).start()

    def _show_error_msg(self, err: str):
        self._set_status(f"Hiba lekérdezés: {err}")
        if not err.startswith("+0"):
            messagebox.showwarning("Műszer hibakód", err)

    # ─── Segédmetódusok ───────────────────────────────────────────────────────

    def _set_controls_state(self, enabled: bool):
        state = "normal" if enabled else "disabled"
        widgets = [
            self._volt_entry, self._curr_entry, self._apply_btn,
            self._ovp_entry, self._ovp_chk, self._ovp_apply_btn, self._ovp_clr_btn,
            self._ocp_entry, self._ocp_chk, self._ocp_apply_btn, self._ocp_clr_btn,
            self._outp_btn, self._meas_btn, self._sw_start_btn,
        ]
        for w in widgets:
            w.config(state=state)
        # Tartomány rádiógombok
        for w in self._range_radios:
            if isinstance(w, ttk.Radiobutton):
                w.config(state=state)
        # Sweep rádiógombok + entry-k
        for w in self._sw_radios:
            w.config(state=state)
        for w in self._sw_widgets:
            w.config(state=state)
        # Hiba gomb
        try:
            self._err_btn.config(state=state)
        except Exception:
            pass

    # ─── Sweep ───────────────────────────────────────────────────────────────

    def _sw_on_type_change(self):
        """Egységcímkék frissítése típusváltáskor."""
        if self._sw_type_var.get() == 0:   # V sweep
            self._sw_unit1_var.set("V")
            self._sw_unit2_var.set("V")
            self._sw_start_var.set("0.0")
            self._sw_stop_var.set(f"{self._range['vmax']:.1f}")
        else:                               # I sweep
            self._sw_unit1_var.set("A")
            self._sw_unit2_var.set("A")
            self._sw_start_var.set("0.0")
            self._sw_stop_var.set(f"{self._range['imax']:.1f}")

    def _on_sweep_start(self):
        if not self._psu:
            return
        if not self._output_on:
            if not messagebox.askyesno(
                    "Kimenet ki van kapcsolva",
                    "A kimenet jelenleg KI van kapcsolva.\n"
                    "Bekapcsoljam a sweep indítása előtt?"):
                return
            # Kimenet bekapcsolás
            try:
                self._psu.output_on()
                self._output_on = True
                self._update_output_btn()
            except Exception as exc:
                messagebox.showerror("Hiba", f"Kimenet nem kapcsolható be: {exc}")
                return

        # Paraméter validálás
        try:
            start = float(self._sw_start_var.get())
            stop  = float(self._sw_stop_var.get())
            steps = int(self._sw_steps_var.get())
            delay = float(self._sw_delay_var.get())
        except ValueError:
            messagebox.showerror("Beviteli hiba", "Érvényes számokat adj meg!")
            return

        sweep_v = self._sw_type_var.get() == 0   # True = V sweep
        vmax = self._range["vmax"]
        imax = self._range["imax"]
        limit = vmax if sweep_v else imax
        unit  = "V"  if sweep_v else "A"

        if steps < 2:
            messagebox.showerror("Beviteli hiba", "Minimum 2 lépés szükséges!")
            return
        if not (0.0 <= start <= limit) or not (0.0 <= stop <= limit):
            messagebox.showerror("Beviteli hiba",
                                  f"Értékek 0–{limit} {unit} között legyenek!")
            return
        if delay < 0.05:
            messagebox.showerror("Beviteli hiba",
                                  "Minimum késleltetés 0.05 s (stabilizálódás)!")
            return

        # Sweep indítás
        self._sweep_data.clear()
        self._sweep_running = True
        self._stop_poll()                   # automata polling szünet

        self._sw_start_btn.config(state="disabled")
        self._sw_stop_btn.config(state="normal")
        self._sw_graph_btn.config(state="disabled")
        self._sw_csv_btn.config(state="disabled")
        self._sw_progress_var.set(0.0)
        self._sw_info_var.set("")

        # Grafikon ablak megnyitása ha matplotlib van
        if _HAS_MPL:
            self._open_graph_window()

        threading.Thread(
            target=self._sweep_worker,
            args=(sweep_v, start, stop, steps, delay),
            daemon=True
        ).start()

    def _sweep_worker(self, sweep_v: bool, start: float, stop: float,
                      steps: int, delay: float):
        """Háttérszálon fut. Lépésről lépésre haladja a tartományt."""
        values = [start + (stop - start) * k / (steps - 1)
                  for k in range(steps)]
        try:
            for idx, val in enumerate(values):
                if not self._sweep_running:
                    break
                # Setpoint küldése
                if sweep_v:
                    self._psu.send(f"VOLT {val:.4f}")
                else:
                    self._psu.send(f"CURR {val:.4f}")
                # Stabilizálódás
                time.sleep(delay)
                # Mérés
                u = self._psu.measure_voltage()
                i = self._psu.measure_current()
                progress = (idx + 1) / steps * 100
                # GUI frissítés a főszálra
                self.after(0, lambda u=u, i=i, p=progress, v=val, idx=idx:
                           self._on_sweep_step(u, i, p, v, sweep_v, idx, steps))
            self.after(0, self._on_sweep_done)
        except Exception as exc:
            self.after(0, lambda: self._on_sweep_error(str(exc)))

    def _on_sweep_step(self, u: float, i: float, progress: float,
                        set_val: float, sweep_v: bool, idx: int, total: int):
        """Főszálon: egy lépés eredményének feldolgozása."""
        self._sweep_data.append((u, i))
        self._sw_progress_var.set(progress)
        unit = "V" if sweep_v else "A"
        self._sw_info_var.set(
            f"{idx+1}/{total}  set={set_val:.3f}{unit}  "
            f"U={u:.4f}V  I={i:.4f}A")
        # Élő grafikon frissítés
        self._update_graph()

    def _on_sweep_done(self):
        """Sweep befejezve."""
        self._sweep_running = False
        self._sw_start_btn.config(state="normal")
        self._sw_stop_btn.config(state="disabled")
        self._sw_progress_var.set(100.0)
        n = len(self._sweep_data)
        self._sw_info_var.set(f"Kész – {n} pont mérve.")
        if self._sweep_data:
            self._sw_graph_btn.config(state="normal")
            self._sw_csv_btn.config(state="normal")
        self._set_status(f"Sweep befejezve – {n} adatpont.")
        if self._auto_poll_var.get():
            self._start_poll()

    def _on_sweep_stop(self):
        """Felhasználó leállítja a sweep-et."""
        self._sweep_running = False
        self._sw_stop_btn.config(state="disabled")
        self._sw_info_var.set("Leállítva.")
        self._set_status("Sweep megszakítva.")

    def _on_sweep_error(self, msg: str):
        self._sweep_running = False
        self._sw_start_btn.config(state="normal")
        self._sw_stop_btn.config(state="disabled")
        self._sw_info_var.set("Hiba!")
        self._set_status(f"Sweep hiba: {msg}")
        messagebox.showerror("Sweep hiba", msg)
        if self._auto_poll_var.get():
            self._start_poll()

    # ─── Grafikon ablak ───────────────────────────────────────────────────────

    def _open_graph_window(self):
        if not _HAS_MPL:
            messagebox.showinfo(
                "matplotlib hiányzik",
                "A grafikon megjelenítéshez a matplotlib csomag szükséges.\n"
                "Telepítés: pip install matplotlib")
            return

        if self._graph_win and self._graph_win.winfo_exists():
            self._graph_win.lift()
            self._update_graph()
            return

        win = tk.Toplevel(self)
        win.title("Sweep grafikon – E3632A")
        win.resizable(True, True)
        win.geometry("680x480")
        self._graph_win = win

        fig = Figure(figsize=(6.5, 4.5), dpi=100, tight_layout=True)
        self._graph_ax = fig.add_subplot(111)

        canvas = FigureCanvasTkAgg(fig, master=win)
        canvas.get_tk_widget().pack(fill="both", expand=True, padx=6, pady=6)
        self._graph_canvas = canvas

        btn_f = ttk.Frame(win)
        btn_f.pack(fill="x", padx=6, pady=(0, 6))
        ttk.Button(btn_f, text="💾 CSV export",
                   command=self._export_csv).pack(side="left", padx=4)
        ttk.Button(btn_f, text="Bezár",
                   command=win.destroy).pack(side="right", padx=4)

        self._update_graph()

    def _update_graph(self):
        if not _HAS_MPL or not self._graph_ax or not self._graph_canvas:
            return
        if not self._graph_win or not self._graph_win.winfo_exists():
            return

        ax = self._graph_ax
        ax.clear()

        data = self._sweep_data
        if data:
            vs = [p[0] for p in data]   # U_meas
            is_ = [p[1] for p in data]  # I_meas
            sweep_v = self._sw_type_var.get() == 0

            if sweep_v:
                ax.plot(vs, is_, color="#1565c0", linewidth=1.5,
                        marker="o", markersize=3, label="mért")
                ax.set_xlabel("Feszültség (V)", fontsize=10)
                ax.set_ylabel("Áram (A)", fontsize=10)
                ax.set_title("V-I karakterisztika – E3632A", fontsize=11)
            else:
                ax.plot(is_, vs, color="#b71c1c", linewidth=1.5,
                        marker="o", markersize=3, label="mért")
                ax.set_xlabel("Áram (A)", fontsize=10)
                ax.set_ylabel("Feszültség (V)", fontsize=10)
                ax.set_title("I-V karakterisztika – E3632A", fontsize=11)

            ax.grid(True, alpha=0.35)
            ax.legend(fontsize=9)

        try:
            self._graph_canvas.draw()
        except Exception:
            pass

    # ─── CSV export ───────────────────────────────────────────────────────────

    def _export_csv(self):
        if not self._sweep_data:
            messagebox.showinfo("Nincs adat", "Még nincs sweep adat exportáláshoz.")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV fájl", "*.csv"), ("Minden fájl", "*.*")],
            initialfile="e3632a_sweep.csv",
            title="CSV mentés")
        if not path:
            return
        sweep_v = self._sw_type_var.get() == 0
        try:
            with open(path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(["# E3632A sweep export"])
                writer.writerow(["# Típus", "Feszültség sweep" if sweep_v
                                 else "Áram sweep"])
                writer.writerow(["# Tartomány", self._range["code"]])
                writer.writerow(["U_meas (V)", "I_meas (A)"])
                writer.writerows(self._sweep_data)
            self._set_status(f"CSV mentve: {path}")
        except Exception as exc:
            messagebox.showerror("Mentési hiba", str(exc))

    def _on_error(self, msg: str):
        self._set_status(f"Hiba: {msg}")
        messagebox.showerror("Hiba", msg)
        # Gombok visszaállítása
        if self._psu:
            self._apply_btn.config(state="normal")
            self._outp_btn.config(state="normal")
            self._meas_btn.config(state="normal")

    def _set_status(self, text: str):
        self._status_var.set(text)

    def on_close(self):
        self._sweep_running = False
        self._stop_poll()
        if self._psu:
            self._psu.disconnect()
        self.destroy()


# ─── Belépési pont ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = App()
    app.protocol("WM_DELETE_WINDOW", app.on_close)
    app.mainloop()
