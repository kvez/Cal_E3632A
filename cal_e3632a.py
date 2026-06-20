"""
Keysight E3632A DC tápegység automatikus kalibrációs program
4 önálló kalibrációs blokk – szabadon futtatható

Kapcsolatok:
  Tápegység (PSU): RS-232, null-modem (DTE-DTE) kábel
  Multiméter (DMM): TCP socket, port 5025 (Keysight 34465A)

Forrás: Keysight E3632A Service Guide (9018-01459) – 43–58. oldal
        Table 1-4: Parameters for calibration
        Gyári Keysight BASIC kalibrációs program (56–58. oldal)

Kalibrációs sorrend (Table 1-4 alapján):
  Blokk A – DAC DNL korrekció + Feszültség kalibráció (V LO / V MI / V HI)
             Bekötés: DMM a PSU (+/−) kivezetéseire, terhelés NÉLKÜL
             Idő: DAC ~30 s + 3 × 2 s mérési várakozás
  Blokk B – OVP (túlfeszültség védelem) kalibráció
             Bekötés: azonos a Blokk A-val (kimenet nyitott!)
             Idő: ~9 s automatikus
  Blokk C – Áram kalibráció (I LO / I MI / I HI)
             Bekötés: 0,01 Ω sönt a PSU kimenetein, DMM a sönt végein
             Idő: 3 × 2 s mérési várakozás
  Blokk D – OCP (túláram védelem) kalibráció
             Bekötés: sönt marad a kimeneteken (rövidzár!)
             Idő: ~9 s automatikus

Előkészítés minden blokkhoz:
  · OVP és OCP kikapcsolva (Service Guide előírás – Error 717 elkerülése)
  · CAL:SEC:STAT OFF (biztonsági kód szükséges, gyári: HP003632)

Befejezés (minden blokk után, "Lezárás" gombbal):
  · CAL:STR – kalibrációs üzenet írása (dátum + sönt értéke)
  · CAL:SEC:STAT ON – újra zárolás
  · OVP és OCP visszakapcsolva
"""

import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
import threading
import time
import datetime
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from psu_e3632a import PSU
from dmm_34465a import DMM

SCPI_PORT = 5025

# ─── Bekötési utasítások ─────────────────────────────────────────────────────

CONN_A = """\
BEKÖTÉS – Blokk A: DAC korrekció + Feszültség kalibráció
══════════════════════════════════════════════════════════

  Tápegység kimenetek:
    (+) ──▶  DMM  HI bemenet
    (−) ──▶  DMM  LO bemenet

  Terhelés: NINCS (kimenet nyitott!)

  Nominal kalibrációs pontok:
    V LO ≈   0.5 V
    V MI ≈  15.0 V
    V HI ≈  29.5 V

  DMM: DC feszültség mérés (autorange, 10 NPLC)
       – a program automatikusan konfigurálja

  Idő: DAC korrekció ~30 s + feszültségmérések ~6 s

⚠  Ellenőrizd a bekötést, majd nyomj "Kész, folytatás"-t!
"""

CONN_B = """\
BEKÖTÉS – Blokk B: OVP kalibráció
════════════════════════════════════

  Bekötés AZONOS a Blokk A-val:
    PSU (+) ──▶ DMM HI
    PSU (−) ──▶ DMM LO

  Terhelés: NINCS (kimenet nyitott!)

  Az OVP kalibrációhoz a kimenet nyitottnak kell lennie.

  Idő: ~9 s automatikus (CAL:VOLT:PROT)

⚠  Ellenőrizd a bekötést, majd nyomj "Kész, folytatás"-t!
"""

CONN_C = """\
BEKÖTÉS – Blokk C: Áram kalibráció
════════════════════════════════════

  Sönt ellenállás: 0,01 Ω (pontosság ≤ 0,01%, min. 10 W)

  Tápegység kimenetek:
    (+) ──▶ Sönt (+) vége ──┐
    (−) ──▶ Sönt (−) vége ──┘  (sönt rövidre zárja a kimenetet!)

  DMM a sönt érzékelő kivezetéseire (Kelvin / 4-sark):
    DMM HI = sönt (+) érzékelő (PSU (+) oldal)
    DMM LO = sönt (−) érzékelő (PSU (−) oldal)

  Nominal kalibrációs pontok:
    I LO ≈ 0.20 A  →  V_sönt ≈  2,0 mV
    I MI ≈ 3.50 A  →  V_sönt ≈ 35,0 mV
    I HI ≈ 6.90 A  →  V_sönt ≈ 69,0 mV

  DMM: DC feszültség, 100 mV tartomány, 10 NPLC
       – a program automatikusan konfigurálja
  Áram = V_sönt / R_sönt

⚠  Ellenőrizd a bekötést, majd nyomj "Kész, folytatás"-t!
"""

CONN_D = """\
BEKÖTÉS – Blokk D: OCP kalibráció
════════════════════════════════════

  A Blokk C bekötése MARAD változatlanul:
    PSU (+/−) → sönt → visszazár a (−)-ra

  A sönt rövidzárja a kimenetet – ez szükséges az OCP kalibrációhoz!

  ⚠  FIGYELEM: Kalibráció alatt a tápegység max. áramot ad le a sönten!
     A sönt teljesítménytűrése elegendő kell legyen:
       7 A × 7 A × 0,01 Ω ≈ 0,5 W (0,01 Ω söntnél jó)
     Tipikus sönt: Burster 1280-1 vagy hasonló.

  Idő: ~9 s automatikus (CAL:CURR:PROT)

⚠  Ellenőrizd a bekötést, majd nyomj "Kész, folytatás"-t!
"""

# ─── Blokk definíciók ────────────────────────────────────────────────────────

BLOCKS = [
    {"id": "A", "label": "A – DAC + Feszültség", "conn": CONN_A},
    {"id": "B", "label": "B – OVP kalibráció",   "conn": CONN_B},
    {"id": "C", "label": "C – Áram kalibráció",  "conn": CONN_C},
    {"id": "D", "label": "D – OCP kalibráció",   "conn": CONN_D},
]

# ─── Alkalmazás ──────────────────────────────────────────────────────────────

class CalApp(tk.Tk):

    def __init__(self):
        super().__init__()
        self.title("E3632A automatikus kalibráció")
        self.resizable(False, False)

        self._psu: PSU | None = None
        self._dmm: DMM | None = None
        self._running = False          # fut-e kalibráció?
        self._wait_event = threading.Event()  # "Kész" gomb jelzése

        self._build_ui()
        self._refresh_ports()

    # ─── UI ──────────────────────────────────────────────────────────────────

    def _build_ui(self):
        pad = {"padx": 10, "pady": 6}

        # ── Kapcsolat frame ──────────────────────────────────────────────────
        conn_frame = ttk.LabelFrame(self, text="Kapcsolat")
        conn_frame.grid(row=0, column=0, columnspan=2, sticky="ew", **pad)

        # PSU – RS-232
        ttk.Label(conn_frame, text="PSU port:").grid(
            row=0, column=0, sticky="w", padx=6, pady=4)
        self._port_var = tk.StringVar()
        self._port_cb = ttk.Combobox(conn_frame, textvariable=self._port_var,
                                      state="readonly", width=10)
        self._port_cb.grid(row=0, column=1, padx=4, pady=4)
        ttk.Button(conn_frame, text="⟳", width=3,
                   command=self._refresh_ports).grid(row=0, column=2, padx=2)

        ttk.Label(conn_frame, text="Baud:").grid(
            row=0, column=3, sticky="w", padx=(10, 4))
        self._baud_var = tk.IntVar(value=9600)
        ttk.Combobox(conn_frame, textvariable=self._baud_var,
                     values=[300, 600, 1200, 2400, 4800, 9600],
                     state="readonly", width=7).grid(row=0, column=4, padx=4)

        # DMM – TCP
        ttk.Label(conn_frame, text="DMM IP:").grid(
            row=0, column=5, sticky="w", padx=(20, 4))
        self._dmm_ip_var = tk.StringVar(value="192.168.1.100")
        ttk.Entry(conn_frame, textvariable=self._dmm_ip_var, width=16).grid(
            row=0, column=6, padx=4)

        self._conn_btn = ttk.Button(conn_frame, text="Csatlakozás",
                                     command=self._on_connect, width=14)
        self._conn_btn.grid(row=0, column=7, padx=12, pady=4)

        # IDN kijelzők
        ttk.Label(conn_frame, text="PSU:").grid(
            row=1, column=0, sticky="w", padx=6)
        self._psu_idn_var = tk.StringVar(value="—")
        ttk.Label(conn_frame, textvariable=self._psu_idn_var,
                  foreground="navy").grid(row=1, column=1, columnspan=4,
                                          sticky="w", padx=4)

        ttk.Label(conn_frame, text="DMM:").grid(
            row=1, column=5, sticky="w", padx=(20, 4))
        self._dmm_idn_var = tk.StringVar(value="—")
        ttk.Label(conn_frame, textvariable=self._dmm_idn_var,
                  foreground="navy").grid(row=1, column=6, columnspan=2,
                                          sticky="w", padx=4)

        # Sönt + biztonsági kód
        ttk.Label(conn_frame, text="Sönt (Ω):").grid(
            row=2, column=0, sticky="w", padx=6, pady=4)
        self._shunt_var = tk.StringVar(value="0.01")
        ttk.Entry(conn_frame, textvariable=self._shunt_var,
                  width=8).grid(row=2, column=1, padx=4, sticky="w")

        ttk.Label(conn_frame, text="Cal kód:").grid(
            row=2, column=3, sticky="w", padx=(10, 4))
        self._calcode_var = tk.StringVar(value="HP003632")
        ttk.Entry(conn_frame, textvariable=self._calcode_var,
                  width=12).grid(row=2, column=4, padx=4, sticky="w")

        ttk.Label(conn_frame,
                  text="(gyári kód: HP003632)",
                  foreground="gray").grid(row=2, column=5, columnspan=3,
                                           sticky="w", padx=(20, 4))

        # ── Blokk választó frame ─────────────────────────────────────────────
        blk_frame = ttk.LabelFrame(self, text="Kalibrációs blokkok")
        blk_frame.grid(row=1, column=0, sticky="nsew", **pad)

        self._blk_btns: list[ttk.Button] = []
        for i, blk in enumerate(BLOCKS):
            btn = ttk.Button(blk_frame, text=blk["label"],
                             command=lambda b=blk: self._on_block_select(b),
                             state="disabled", width=24)
            btn.grid(row=i, column=0, padx=8, pady=4, sticky="ew")
            self._blk_btns.append(btn)

        ttk.Separator(blk_frame).grid(row=len(BLOCKS), column=0,
                                       sticky="ew", pady=4)
        self._finish_btn = ttk.Button(blk_frame, text="✔  Lezárás (re-secure)",
                                       command=self._on_finish,
                                       state="disabled", width=24)
        self._finish_btn.grid(row=len(BLOCKS)+1, column=0, padx=8, pady=4,
                               sticky="ew")

        # Cal count kijelző
        self._cal_count_var = tk.StringVar(value="Cal count: —")
        ttk.Label(blk_frame, textvariable=self._cal_count_var,
                  foreground="gray").grid(row=len(BLOCKS)+2, column=0,
                                          padx=8, pady=(4, 6), sticky="w")

        # ── Bekötési utasítás + Log frame ────────────────────────────────────
        right_frame = ttk.Frame(self)
        right_frame.grid(row=1, column=1, sticky="nsew", **pad)

        ttk.Label(right_frame, text="Bekötési utasítás:",
                  font=("TkDefaultFont", 9, "bold")).pack(anchor="w")
        self._conn_text = tk.Text(right_frame, width=60, height=12,
                                   state="disabled", bg="#f8f8f8",
                                   font=("Courier New", 9), relief="sunken", bd=1)
        self._conn_text.pack(fill="x")

        ttk.Label(right_frame, text="Napló:",
                  font=("TkDefaultFont", 9, "bold")).pack(anchor="w",
                                                           pady=(8, 0))
        self._log = scrolledtext.ScrolledText(
            right_frame, width=60, height=14, state="disabled",
            font=("Courier New", 9), bg="#1a1a1a", fg="#cccccc",
            relief="sunken", bd=1)
        self._log.pack(fill="both", expand=True)
        self._log.tag_config("ok",    foreground="#55ff55")
        self._log.tag_config("err",   foreground="#ff5555")
        self._log.tag_config("warn",  foreground="#ffaa00")
        self._log.tag_config("head",  foreground="#88ccff",
                              font=("Courier New", 9, "bold"))
        self._log.tag_config("value", foreground="#ffffff",
                              font=("Courier New", 9, "bold"))

        # ── Folytatás gomb + progress ─────────────────────────────────────────
        ctrl_frame = ttk.Frame(self)
        ctrl_frame.grid(row=2, column=0, columnspan=2, sticky="ew", **pad)

        self._ready_btn = ttk.Button(ctrl_frame, text="✔  Kész, folytatás",
                                      command=self._on_ready,
                                      state="disabled", width=20)
        self._ready_btn.pack(side="left", padx=4)

        self._abort_btn = ttk.Button(ctrl_frame, text="✖  Megszakítás",
                                      command=self._on_abort,
                                      state="disabled", width=16)
        self._abort_btn.pack(side="left", padx=4)

        self._progress_var = tk.DoubleVar(value=0.0)
        ttk.Progressbar(ctrl_frame, variable=self._progress_var,
                         maximum=100, length=250).pack(side="left", padx=12)

        self._prog_label_var = tk.StringVar(value="")
        ttk.Label(ctrl_frame, textvariable=self._prog_label_var,
                  width=30).pack(side="left", padx=4)

        # ── Státuszsor ───────────────────────────────────────────────────────
        self._status_var = tk.StringVar(value="Nincs kapcsolat.")
        ttk.Label(self, textvariable=self._status_var,
                  relief="sunken", anchor="w").grid(
            row=3, column=0, columnspan=2, sticky="ew", padx=4, pady=(0, 4))

        self.columnconfigure(1, weight=1)

    # ─── Port lista ───────────────────────────────────────────────────────────

    def _refresh_ports(self):
        import serial.tools.list_ports
        ports = [p.device for p in serial.tools.list_ports.comports()]
        self._port_cb["values"] = ports
        if ports and not self._port_var.get():
            self._port_var.set(ports[0])

    # ─── Kapcsolat ────────────────────────────────────────────────────────────

    def _on_connect(self):
        if self._psu is not None:
            self._disconnect()
            return

        port = self._port_var.get().strip()
        ip   = self._dmm_ip_var.get().strip()
        baud = self._baud_var.get()

        if not port:
            messagebox.showerror("Hiba", "Válassz soros portot!")
            return
        if not ip:
            messagebox.showerror("Hiba", "Add meg a DMM IP-címét!")
            return

        self._conn_btn.config(state="disabled")
        self._set_status("Csatlakozás …")

        def do_connect():
            try:
                psu = PSU(port, baudrate=baud, timeout=8.0)
                psu_idn = psu.connect()
                dmm = DMM(ip, timeout=15.0)
                dmm_idn = dmm.connect()
                cal_count = psu.query("CAL:COUNT?")
                self._psu = psu
                self._dmm = dmm
                self.after(0, lambda: self._on_connected(psu_idn, dmm_idn,
                                                          cal_count))
            except Exception as exc:
                self.after(0, lambda: self._on_connect_error(str(exc)))

        threading.Thread(target=do_connect, daemon=True).start()

    def _on_connected(self, psu_idn: str, dmm_idn: str, cal_count: str):
        self._psu_idn_var.set(psu_idn)
        self._dmm_idn_var.set(dmm_idn)
        self._cal_count_var.set(f"Cal count: {cal_count}")
        self._conn_btn.config(text="Lecsatlakozás", state="normal")
        for btn in self._blk_btns:
            btn.config(state="normal")
        self._set_status("Kapcsolódva. Válassz kalibrációs blokkot!")
        self._log_clear()
        self._log_write("Kapcsolat OK\n", "ok")
        self._log_write(f"  PSU: {psu_idn}\n")
        self._log_write(f"  DMM: {dmm_idn}\n")
        self._log_write(f"  Cal count: {cal_count}\n")
        self._log_write("─" * 55 + "\n")

    def _on_connect_error(self, msg: str):
        self._conn_btn.config(state="normal")
        self._set_status(f"Kapcsolódási hiba: {msg}")
        messagebox.showerror("Kapcsolódási hiba", msg)

    def _disconnect(self):
        if self._psu:
            try:
                self._psu.disconnect()
            except Exception:
                pass
            self._psu = None
        if self._dmm:
            try:
                self._dmm.disconnect()
            except Exception:
                pass
            self._dmm = None
        self._conn_btn.config(text="Csatlakozás", state="normal")
        self._psu_idn_var.set("—")
        self._dmm_idn_var.set("—")
        self._cal_count_var.set("Cal count: —")
        for btn in self._blk_btns:
            btn.config(state="disabled")
        self._finish_btn.config(state="disabled")
        self._set_status("Kapcsolat bontva.")

    # ─── Blokk választás ─────────────────────────────────────────────────────

    def _on_block_select(self, blk: dict):
        if self._running:
            return
        self._show_conn_instructions(blk["conn"])
        self._set_status(f"Blokk {blk['id']} kiválasztva. Ellenőrizd a bekötést!")
        self._ready_btn.config(state="normal",
                               command=lambda b=blk: self._start_block(b))

    def _show_conn_instructions(self, text: str):
        self._conn_text.config(state="normal")
        self._conn_text.delete("1.0", "end")
        self._conn_text.insert("end", text)
        self._conn_text.config(state="disabled")

    # ─── Blokk végrehajtás ───────────────────────────────────────────────────

    def _start_block(self, blk: dict):
        """Blokk indítása háttérszálon, bekötés jóváhagyása után."""
        self._running = True
        self._ready_btn.config(state="disabled")
        self._abort_btn.config(state="normal")
        for btn in self._blk_btns:
            btn.config(state="disabled")
        self._finish_btn.config(state="disabled")

        fn = {
            "A": self._run_block_a,
            "B": self._run_block_b,
            "C": self._run_block_c,
            "D": self._run_block_d,
        }[blk["id"]]

        threading.Thread(target=fn, daemon=True).start()

    def _block_done(self, success: bool = True):
        """Blokk befejezésekor hívódik (bármely szálról, after()-rel)."""
        self._running = False
        self._abort_btn.config(state="disabled")
        self._ready_btn.config(state="disabled")
        for btn in self._blk_btns:
            btn.config(state="normal" if self._psu else "disabled")
        self._finish_btn.config(state="normal" if self._psu else "disabled")
        self._progress_var.set(100.0 if success else 0.0)
        self._prog_label_var.set("Kész." if success else "Megszakítva.")

    # ─── Blokk A: DAC korrekció + Feszültség kalibráció ──────────────────────

    def _run_block_a(self):
        self._log_write("\n╔══ Blokk A: DAC + Feszültség kalibráció ══╗\n",
                        "head")
        try:
            self._prepare_cal()

            # ── DAC DNL hiba korrekció (~30 s) ─────────────────────────────
            self._log_write("\n[1/5] DAC DNL error correction …\n")
            self._progress(5, "DAC korrekció …")
            self._psu.send("OUTP ON")
            self._psu.send("CAL:DAC:ERR")
            self._countdown(31, "DAC korrekció")     # gyári BASIC: WAIT 29
            self._psu.send("OUTP OFF")
            self._check_error("DAC DNL")
            self._log_write("  DAC korrekció OK\n", "ok")

            # ── Feszültség kalibráció – 3 pont ──────────────────────────────
            self._log_write("\n[2/5] Feszültség kalibráció (kimenet BE) …\n")
            self._progress(20, "Feszültség – V LO …")
            self._psu.send("OUTP ON")

            # DMM konfigurálás: DC feszültség, autorange, 10 NPLC
            self._dmm.configure_dcv(10)

            for step, level, pct, label in [
                ("MIN", 35, "V LO"),
                ("MID", 55, "V MI"),
                ("MAX", 70, "V HI"),
            ]:
                self._progress(pct, f"Feszültség – {label} …")
                self._log_write(f"  {label}: CAL:VOLT:LEV {step} ", "")
                self._psu.send(f"CAL:VOLT:LEV {step}")
                time.sleep(2.5)          # stabilizálódás (gyári: 2 s)
                v = self._dmm.read_once()
                self._log_write(f"→ DMM = ", "")
                self._log_write(f"{v:.6f} V", "value")
                self._psu.send(f"CAL:VOLT:DATA {v:.6f}")
                self._log_write("  → adatpont elküldve\n", "ok")

            self._psu.send("OUTP OFF")
            self._check_error("Feszültség kalibráció")
            self._log_write("  Feszültség kalibráció OK\n", "ok")
            self._progress(85, "Blokk A kész.")

        except _Aborted:
            self._log_write("\n  !! Megszakítva !!\n", "warn")
            self.after(0, lambda: self._block_done(False))
            return
        except Exception as exc:
            self._log_write(f"\n  HIBA: {exc}\n", "err")
            self.after(0, lambda: self._block_done(False))
            return

        self._log_write("╚══ Blokk A befejezve ══╝\n", "head")
        self.after(0, lambda: self._block_done(True))

    # ─── Blokk B: OVP kalibráció ─────────────────────────────────────────────

    def _run_block_b(self):
        self._log_write("\n╔══ Blokk B: OVP kalibráció ══╗\n", "head")
        try:
            self._prepare_cal()

            self._log_write("\n[1/1] OVP kalibráció (~9 s) …\n")
            self._progress(10, "OVP kalibráció …")
            self._psu.send("OUTP ON")
            self._psu.send("CAL:VOLT:PROT")
            self._countdown(10, "OVP kalibráció")    # gyári BASIC: WAIT 9
            self._psu.send("OUTP OFF")
            self._check_error("OVP kalibráció")
            self._log_write("  OVP kalibráció OK\n", "ok")
            self._progress(100, "Blokk B kész.")

        except _Aborted:
            self._log_write("\n  !! Megszakítva !!\n", "warn")
            self.after(0, lambda: self._block_done(False))
            return
        except Exception as exc:
            self._log_write(f"\n  HIBA: {exc}\n", "err")
            self.after(0, lambda: self._block_done(False))
            return

        self._log_write("╚══ Blokk B befejezve ══╝\n", "head")
        self.after(0, lambda: self._block_done(True))

    # ─── Blokk C: Áram kalibráció ────────────────────────────────────────────

    def _run_block_c(self):
        self._log_write("\n╔══ Blokk C: Áram kalibráció ══╗\n", "head")
        try:
            shunt = float(self._shunt_var.get())
        except ValueError:
            self._log_write("  HIBA: érvénytelen sönt érték!\n", "err")
            self.after(0, lambda: self._block_done(False))
            return

        self._log_write(f"  Sönt: {shunt} Ω\n")
        try:
            self._prepare_cal()

            self._log_write("\n[1/3] Áram kalibráció (kimenet BE) …\n")
            self._progress(20, "Áram – I LO …")
            self._psu.send("OUTP ON")

            # DMM: DC feszültség, 100 mV tartomány, 10 NPLC
            # A sönt feszültsége max ~69 mV → 100 mV tartomány optimális
            self._dmm._send("*RST")
            self._dmm._send("CONFigure:VOLTage:DC 0.1,DEF")
            self._dmm._send("SENSe:VOLTage:DC:NPLC 10")

            for step, pct, label in [
                ("MIN", 35, "I LO"),
                ("MID", 60, "I MI"),
                ("MAX", 80, "I HI"),
            ]:
                self._progress(pct, f"Áram – {label} …")
                self._log_write(f"  {label}: CAL:CURR:LEV {step} ", "")
                self._psu.send(f"CAL:CURR:LEV {step}")
                time.sleep(3.0)          # stabilizálódás (gyári: 2 s + margó)
                v_shunt = self._dmm.read_once()
                i_meas  = v_shunt / shunt
                self._log_write(f"→ V_sönt = ", "")
                self._log_write(f"{v_shunt*1000:.4f} mV", "value")
                self._log_write(f"  → I = ", "")
                self._log_write(f"{i_meas:.6f} A", "value")
                self._psu.send(f"CAL:CURR:DATA {i_meas:.6f}")
                self._log_write("  → adatpont elküldve\n", "ok")

            self._psu.send("OUTP OFF")
            self._check_error("Áram kalibráció")
            self._log_write("  Áram kalibráció OK\n", "ok")
            self._progress(95, "Blokk C kész.")

        except _Aborted:
            self._log_write("\n  !! Megszakítva !!\n", "warn")
            self.after(0, lambda: self._block_done(False))
            return
        except Exception as exc:
            self._log_write(f"\n  HIBA: {exc}\n", "err")
            self.after(0, lambda: self._block_done(False))
            return

        self._log_write("╚══ Blokk C befejezve ══╝\n", "head")
        self.after(0, lambda: self._block_done(True))

    # ─── Blokk D: OCP kalibráció ─────────────────────────────────────────────

    def _run_block_d(self):
        self._log_write("\n╔══ Blokk D: OCP kalibráció ══╗\n", "head")
        try:
            self._prepare_cal()

            self._log_write("\n[1/1] OCP kalibráció (~9 s) …\n")
            self._progress(10, "OCP kalibráció …")
            self._psu.send("OUTP ON")
            self._psu.send("CAL:CURR:PROT")
            self._countdown(10, "OCP kalibráció")
            self._psu.send("OUTP OFF")
            self._check_error("OCP kalibráció")
            self._log_write("  OCP kalibráció OK\n", "ok")
            self._progress(100, "Blokk D kész.")

        except _Aborted:
            self._log_write("\n  !! Megszakítva !!\n", "warn")
            self.after(0, lambda: self._block_done(False))
            return
        except Exception as exc:
            self._log_write(f"\n  HIBA: {exc}\n", "err")
            self.after(0, lambda: self._block_done(False))
            return

        self._log_write("╚══ Blokk D befejezve ══╝\n", "head")
        self.after(0, lambda: self._block_done(True))

    # ─── Lezárás (re-secure) ─────────────────────────────────────────────────

    def _on_finish(self):
        """Cal üzenet írás + re-secure + OVP/OCP visszakapcsolás."""
        if not self._psu:
            return
        code = self._calcode_var.get().strip()
        if not code:
            messagebox.showerror("Hiba", "Add meg a biztonsági kódot!")
            return

        def do_finish():
            try:
                shunt = self._shunt_var.get()
                ts = datetime.datetime.now().strftime("%Y-%m-%d")
                cal_msg = f"Cal:{ts} shunt:{shunt}"[:40]
                self._log_write(f"\n[Lezárás] CAL:STR = \"{cal_msg}\"\n")
                self._psu.send(f'CAL:STR "{cal_msg}"')
                self._psu.send(f'CAL:SEC:STAT ON,{code}')
                self._psu.send("VOLT:PROT:STAT ON")
                self._psu.send("CURR:PROT:STAT ON")
                # Cal count frissítése
                cnt = self._psu.query("CAL:COUNT?")
                self.after(0, lambda: self._on_finish_done(cnt))
            except Exception as exc:
                self.after(0, lambda: self._log_write(
                    f"  Lezárás hiba: {exc}\n", "err"))

        threading.Thread(target=do_finish, daemon=True).start()

    def _on_finish_done(self, cal_count: str):
        self._cal_count_var.set(f"Cal count: {cal_count}")
        self._log_write("  Kalibráció lezárva. OVP/OCP visszakapcsolva.\n",
                        "ok")
        self._log_write(f"  Új cal count: {cal_count}\n", "value")
        self._finish_btn.config(state="disabled")
        self._set_status("Kalibráció lezárva – tápegység újra zárolva.")

    # ─── Közös segédmetódusok ─────────────────────────────────────────────────

    def _prepare_cal(self):
        """OVP/OCP le, unsecure – minden blokk elején."""
        code = self._calcode_var.get().strip()
        self._log_write(f"[Előkészítés]\n")
        self._psu.send("*CLS")
        self._psu.send("*RST")
        time.sleep(0.3)
        self._psu.send("SYST:REM")     # RST után újra remote módba
        time.sleep(0.15)
        self._log_write("  OVP/OCP kikapcsolva …\n")
        self._psu.send("VOLT:PROT:STAT OFF")
        self._psu.send("CURR:PROT:STAT OFF")
        self._log_write(f"  Unsecure ({code}) …\n")
        self._psu.send(f"CAL:SEC:STAT OFF,{code}")
        time.sleep(0.2)
        secured = self._psu.query("CAL:SEC:STAT?")
        if secured == "1":
            raise RuntimeError("Nem sikerült a biztonsági zárat feloldani! "
                               "Ellenőrizd a kódot.")
        self._log_write("  Kalibráció engedélyezve.\n", "ok")

    def _check_error(self, ctx: str):
        """Hibakód lekérdezés – ha nem +0, kivételt dob."""
        err = self._psu.query("SYST:ERR?")
        if not err.startswith("+0"):
            raise RuntimeError(f"{ctx}: {err}")
        self._log_write(f"  Hiba-ellenőrzés OK ({err})\n")

    def _countdown(self, secs: int, label: str):
        """Visszaszámláló várakozás, abort figyeléssel."""
        for i in range(secs):
            if not self._running:
                raise _Aborted()
            remaining = secs - i
            self.after(0, lambda r=remaining, l=label:
                       self._prog_label_var.set(f"{l}: {r} s …"))
            time.sleep(1.0)

    def _progress(self, pct: float, label: str = ""):
        self.after(0, lambda: self._progress_var.set(pct))
        if label:
            self.after(0, lambda: self._prog_label_var.set(label))

    # ─── Gombok ──────────────────────────────────────────────────────────────

    def _on_ready(self):
        """'Kész, folytatás' gomb – háttérszál várja ezt."""
        self._wait_event.set()

    def _on_abort(self):
        self._running = False
        self._log_write("\n  Megszakítás kérve …\n", "warn")
        self._set_status("Megszakítás …")
        if self._psu:
            try:
                self._psu.send("OUTP OFF")
            except Exception:
                pass

    # ─── Napló segédmetódusok ─────────────────────────────────────────────────

    def _log_write(self, text: str, tag: str = ""):
        def _do():
            self._log.config(state="normal")
            if tag:
                self._log.insert("end", text, tag)
            else:
                self._log.insert("end", text)
            self._log.see("end")
            self._log.config(state="disabled")
        self.after(0, _do)

    def _log_clear(self):
        self._log.config(state="normal")
        self._log.delete("1.0", "end")
        self._log.config(state="disabled")

    def _set_status(self, text: str):
        self.after(0, lambda: self._status_var.set(text))

    # ─── Ablak bezárás ────────────────────────────────────────────────────────

    def on_close(self):
        self._running = False
        if self._psu:
            try:
                self._psu.disconnect()
            except Exception:
                pass
        if self._dmm:
            try:
                self._dmm.disconnect()
            except Exception:
                pass
        self.destroy()


# ─── Belső kivétel: megszakítás jelzése ──────────────────────────────────────

class _Aborted(Exception):
    pass


# ─── Belépési pont ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = CalApp()
    app.protocol("WM_DELETE_WINDOW", app.on_close)
    app.mainloop()
