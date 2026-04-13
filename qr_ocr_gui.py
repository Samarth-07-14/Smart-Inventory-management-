#!/usr/bin/env python3
import os
os.environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "True"
os.environ["FLAGS_call_stack_level"] = "0"
"""
Atlas Copco x Vishwakarma University
QR Code Detection + PaddleOCR Integration GUI
Optimized for Raspberry Pi 5
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import cv2
import numpy as np
from PIL import Image, ImageTk
import threading
import time
import queue
import os
import sys
from datetime import datetime
import json

# ── Warehouse Logic Engine ───────────────────────────────────
from logic import WarehouseEngine

# ── QR / Barcode ─────────────────────────────────────────────
try:
    from pyzbar import pyzbar
    PYZBAR_OK = True
except ImportError:
    PYZBAR_OK = False

try:
    import qrcode
    QRCODE_OK = True
except ImportError:
    QRCODE_OK = False

# ── PaddleOCR ────────────────────────────────────────────────
try:
    from paddleocr import PaddleOCR
    PADDLE_AVAILABLE = True
except ImportError:
    PADDLE_AVAILABLE = False

# ── OpenCV built-in QR detector (fallback) ───────────────────
CV_QR_DETECTOR = cv2.QRCodeDetector()

# ─────────────────────────────────────────────────────────────
#  COLOUR PALETTE
# ─────────────────────────────────────────────────────────────
BG_DARK       = "#0D1117"
BG_CARD       = "#161B22"
BG_CARD2      = "#1C2128"
ACCENT_BLUE   = "#1D6FA4"   # Atlas Copco blue
ACCENT_GOLD   = "#F5A623"   # VU gold
TEXT_PRIMARY  = "#E6EDF3"
TEXT_SECONDARY= "#8B949E"
TEXT_GREEN    = "#3FB950"
TEXT_RED      = "#F85149"
TEXT_ORANGE   = "#F0883E"
TEXT_CYAN     = "#39D2C0"
BORDER_COLOR  = "#30363D"
HIGHLIGHT     = "#388BFD"

FONT_TITLE  = ("Helvetica Neue", 13, "bold")
FONT_BODY   = ("Helvetica Neue", 10)
FONT_MONO   = ("Courier New",    10)
FONT_SMALL  = ("Helvetica Neue",  9)
FONT_LARGE  = ("Helvetica Neue", 18, "bold")

# ─────────────────────────────────────────────────────────────
#  HELPERS
# ─────────────────────────────────────────────────────────────
def make_placeholder_logo(text: str, color: str, size=(120, 40)) -> ImageTk.PhotoImage:
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    from PIL import ImageDraw, ImageFont
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 14)
    except Exception:
        font = ImageFont.load_default()
    bbox = draw.textbbox((0, 0), text, font=font)
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x = (size[0] - w) // 2
    y = (size[1] - h) // 2
    hex_col = color.lstrip("#")
    r, g, b = int(hex_col[0:2], 16), int(hex_col[2:4], 16), int(hex_col[4:6], 16)
    draw.rounded_rectangle([2, 2, size[0]-3, size[1]-3], radius=6,
                           outline=(r, g, b, 255), width=2)
    draw.text((x, y), text, fill=(r, g, b, 255), font=font)
    return ImageTk.PhotoImage(img)


# ─────────────────────────────────────────────────────────────
#  MAIN APPLICATION
# ─────────────────────────────────────────────────────────────
class QRScannerApp(tk.Tk):
    def __init__(self):
        super().__init__()

        self.title("QR & OCR Scanner — Atlas Copco × Vishwakarma University")
        self.configure(bg=BG_DARK)
        self.state("zoomed") if sys.platform == "win32" else self.attributes("-zoomed", True)
        self.minsize(1100, 700)

        # ── State ────────────────────────────────────────────
        self.mode           = tk.StringVar(value="live")   # live | video | photo
        self.ocr_enabled    = tk.BooleanVar(value=False)
        self.running        = False
        self.cap            = None
        self.frame_queue    = queue.Queue(maxsize=2)
        self.result_queue   = queue.Queue(maxsize=20)

        self.qr_registry    : dict[str, dict] = {}   # data → {uid, first_seen, count}
        self.uid_counter    = 0
        self.detected_log   : list[dict] = []

        self.ocr_engine     = None
        self.ocr_ready      = False

        # ── Warehouse Engine (from logic.py + database.py) ────
        self.warehouse = WarehouseEngine(
            db_path="warehouse.db",
            cooldown=1.5
        )

        # ── Build UI ─────────────────────────────────────────
        self._build_header()
        self._build_main()
        self._build_status_bar()

        # ── Boot OCR in background ───────────────────────────
        if PADDLE_AVAILABLE:
            threading.Thread(target=self._init_ocr, daemon=True).start()

        # ── Poll result queue ─────────────────────────────────
        self.after(50, self._poll_results)

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ═══════════════════════════════════════════════════════════
    #  HEADER
    # ═══════════════════════════════════════════════════════════
    def _build_header(self):
        hdr = tk.Frame(self, bg=BG_CARD, height=64)
        hdr.pack(fill="x", side="top")
        hdr.pack_propagate(False)

        # left logo placeholder
        lf = tk.Frame(hdr, bg=BG_CARD)
        lf.pack(side="left", padx=16, pady=10)
        self._ac_logo = make_placeholder_logo("ATLAS COPCO", ACCENT_BLUE, (140, 44))
        tk.Label(lf, image=self._ac_logo, bg=BG_CARD).pack()

        # centre title
        cf = tk.Frame(hdr, bg=BG_CARD)
        cf.pack(side="left", expand=True)
        tk.Label(cf, text="QR CODE & OCR SCANNER",
                 font=("Helvetica Neue", 15, "bold"),
                 fg=TEXT_PRIMARY, bg=BG_CARD).pack(side="top")
        tk.Label(cf, text="Industrial Vision System  |  Raspberry Pi 5",
                 font=FONT_SMALL, fg=TEXT_SECONDARY, bg=BG_CARD).pack()

        # right logo placeholder
        rf = tk.Frame(hdr, bg=BG_CARD)
        rf.pack(side="right", padx=16, pady=10)
        self._vu_logo = make_placeholder_logo("VISHWAKARMA UNIV", ACCENT_GOLD, (155, 44))
        tk.Label(rf, image=self._vu_logo, bg=BG_CARD).pack()

        # separator
        tk.Frame(self, bg=BORDER_COLOR, height=1).pack(fill="x")

    # ═══════════════════════════════════════════════════════════
    #  MAIN AREA
    # ═══════════════════════════════════════════════════════════
    def _build_main(self):
        main = tk.Frame(self, bg=BG_DARK)
        main.pack(fill="both", expand=True, padx=10, pady=8)
        main.columnconfigure(0, weight=3)
        main.columnconfigure(1, weight=2)
        main.rowconfigure(0, weight=1)

        self._build_left_panel(main)
        self._build_right_panel(main)

    # ─── LEFT PANEL ──────────────────────────────────────────
    def _build_left_panel(self, parent):
        lf = tk.Frame(parent, bg=BG_DARK)
        lf.grid(row=0, column=0, sticky="nsew", padx=(0, 5))
        lf.rowconfigure(1, weight=1)
        lf.columnconfigure(0, weight=1)

        # Control bar
        ctrl = tk.Frame(lf, bg=BG_CARD, pady=8, padx=10)
        ctrl.grid(row=0, column=0, sticky="ew", pady=(0, 6))

        # Mode buttons
        tk.Label(ctrl, text="INPUT MODE:", font=FONT_SMALL,
                 fg=TEXT_SECONDARY, bg=BG_CARD).pack(side="left", padx=(0, 8))

        self._btn_live  = self._mode_btn(ctrl, "📷  LIVE",  "live")
        self._btn_video = self._mode_btn(ctrl, "🎬  VIDEO", "video")
        self._btn_photo = self._mode_btn(ctrl, "🖼  PHOTO", "photo")

        tk.Frame(ctrl, bg=BORDER_COLOR, width=1).pack(side="left", fill="y", padx=10)

        # OCR toggle
        tk.Label(ctrl, text="PADDLEOCR:", font=FONT_SMALL,
                 fg=TEXT_SECONDARY, bg=BG_CARD).pack(side="left", padx=(0, 4))
        self._ocr_switch = tk.Checkbutton(
            ctrl, variable=self.ocr_enabled,
            text="", onvalue=True, offvalue=False,
            bg=BG_CARD, activebackground=BG_CARD,
            selectcolor=ACCENT_BLUE, fg=TEXT_GREEN,
            command=self._on_ocr_toggle
        )
        self._ocr_switch.pack(side="left")
        self._ocr_label = tk.Label(ctrl, text="OFF", font=FONT_SMALL,
                                   fg=TEXT_RED, bg=BG_CARD)
        self._ocr_label.pack(side="left", padx=(0, 10))

        # Upload / Start buttons
        self._btn_upload = tk.Button(
            ctrl, text="Upload", font=FONT_SMALL, bg=ACCENT_BLUE, fg="white",
            relief="flat", padx=10, pady=4, cursor="hand2",
            command=self._upload_file
        )
        self._btn_upload.pack(side="left", padx=4)
        self._btn_upload.pack_forget()  # hidden until mode != live

        self._btn_start = tk.Button(
            ctrl, text="▶  Start Camera", font=FONT_SMALL,
            bg=TEXT_GREEN, fg=BG_DARK, relief="flat",
            padx=10, pady=4, cursor="hand2",
            command=self._toggle_camera
        )
        self._btn_start.pack(side="left", padx=4)

        self._btn_clear = tk.Button(
            ctrl, text="🗑  Clear Log", font=FONT_SMALL,
            bg=BG_CARD2, fg=TEXT_SECONDARY, relief="flat",
            padx=10, pady=4, cursor="hand2",
            command=self._clear_log
        )
        self._btn_clear.pack(side="right", padx=4)

        # Camera / image canvas
        cam_frame = tk.Frame(lf, bg=BG_CARD, bd=0)
        cam_frame.grid(row=1, column=0, sticky="nsew")

        self.canvas = tk.Canvas(cam_frame, bg="#000000",
                                highlightthickness=1,
                                highlightbackground=BORDER_COLOR)
        self.canvas.pack(fill="both", expand=True, padx=2, pady=2)
        self._show_idle_canvas()

    def _mode_btn(self, parent, label, mode):
        is_active = (self.mode.get() == mode)
        btn = tk.Button(
            parent, text=label, font=FONT_SMALL,
            bg=ACCENT_BLUE if is_active else BG_CARD2,
            fg=TEXT_PRIMARY, relief="flat",
            padx=8, pady=4, cursor="hand2",
            command=lambda m=mode: self._set_mode(m)
        )
        btn.pack(side="left", padx=2)
        return btn

    # ─── RIGHT PANEL ─────────────────────────────────────────
    def _build_right_panel(self, parent):
        rf = tk.Frame(parent, bg=BG_DARK)
        rf.grid(row=0, column=1, sticky="nsew", padx=(5, 0))
        rf.rowconfigure(0, weight=0)   # forklift status (fixed)
        rf.rowconfigure(1, weight=1)   # tabbed notebook (expands)
        rf.columnconfigure(0, weight=1)

        # ── Forklift Status Panel ────────────────────────────
        self._build_forklift_panel(rf)

        # ── Configure Treeview style (once, shared) ──────────
        style = ttk.Style()
        style.theme_use("default")
        style.configure("Dark.Treeview",
                        background=BG_DARK, foreground=TEXT_PRIMARY,
                        fieldbackground=BG_DARK, rowheight=22,
                        font=FONT_SMALL)
        style.configure("Dark.Treeview.Heading",
                        background=BG_CARD2, foreground=ACCENT_GOLD,
                        font=("Helvetica Neue", 9, "bold"), relief="flat")
        style.map("Dark.Treeview",
                  background=[("selected", ACCENT_BLUE)],
                  foreground=[("selected", "white")])

        # Notebook tab styling
        style.configure("Dark.TNotebook",
                        background=BG_DARK, borderwidth=0)
        style.configure("Dark.TNotebook.Tab",
                        background=BG_CARD2, foreground=TEXT_SECONDARY,
                        padding=[12, 5],
                        font=("Helvetica Neue", 9, "bold"))
        style.map("Dark.TNotebook.Tab",
                  background=[("selected", BG_CARD)],
                  foreground=[("selected", ACCENT_GOLD)])

        # ── Tabbed Notebook ──────────────────────────────────
        self.notebook = ttk.Notebook(rf, style="Dark.TNotebook")
        self.notebook.grid(row=1, column=0, sticky="nsew", pady=(6, 0))

        # Tab 1: Live Detection
        self._build_tab_live(self.notebook)

        # Tab 2: Inventory (Rack ↔ Box)
        self._build_tab_inventory(self.notebook)

        # Tab 3: Movement History
        self._build_tab_history(self.notebook)

        # Tab 4: Scan Log
        self._build_tab_scanlog(self.notebook)

        # Refresh DB tabs on switch
        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)

    # ─── TAB 1: LIVE DETECTION ───────────────────────────────
    def _build_tab_live(self, notebook):
        tab = tk.Frame(notebook, bg=BG_DARK)
        notebook.add(tab, text="  Live Detection  ")
        tab.rowconfigure(0, weight=2)
        tab.rowconfigure(1, weight=3)
        tab.columnconfigure(0, weight=1)

        # QR Data Text
        qr_frame = tk.Frame(tab, bg=BG_DARK,
                            highlightthickness=1,
                            highlightbackground=BORDER_COLOR)
        qr_frame.grid(row=0, column=0, sticky="nsew", pady=(0, 4))
        qr_frame.rowconfigure(0, weight=1)
        qr_frame.columnconfigure(0, weight=1)

        self.qr_text = tk.Text(
            qr_frame, bg=BG_DARK, fg=TEXT_GREEN,
            font=FONT_MONO, relief="flat", wrap="word",
            state="disabled", padx=8, pady=6,
            insertbackground=TEXT_GREEN,
            selectbackground=ACCENT_BLUE
        )
        qs = ttk.Scrollbar(qr_frame, command=self.qr_text.yview)
        self.qr_text.configure(yscrollcommand=qs.set)
        self.qr_text.grid(row=0, column=0, sticky="nsew")
        qs.grid(row=0, column=1, sticky="ns")

        # Detection Log Table
        log_frame = tk.Frame(tab, bg=BG_DARK,
                             highlightthickness=1,
                             highlightbackground=BORDER_COLOR)
        log_frame.grid(row=1, column=0, sticky="nsew")
        log_frame.rowconfigure(0, weight=1)
        log_frame.columnconfigure(0, weight=1)

        cols = ("uid", "type", "data", "time", "ocr")
        self.tree = ttk.Treeview(log_frame, columns=cols,
                                 show="headings", style="Dark.Treeview")
        self.tree.heading("uid",  text="#UID")
        self.tree.heading("type", text="Type")
        self.tree.heading("data", text="QR Data")
        self.tree.heading("time", text="Time")
        self.tree.heading("ocr",  text="OCR Text")
        self.tree.column("uid",  width=55,  stretch=False)
        self.tree.column("type", width=65,  stretch=False)
        self.tree.column("data", width=160, stretch=True)
        self.tree.column("time", width=75,  stretch=False)
        self.tree.column("ocr",  width=130, stretch=True)

        vsb = ttk.Scrollbar(log_frame, orient="vertical",   command=self.tree.yview)
        hsb = ttk.Scrollbar(log_frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")

        # right-click context
        self.tree.bind("<Button-3>", self._tree_context_menu)

    # ─── TAB 2: INVENTORY (Rack ↔ Box Relations) ─────────────
    def _build_tab_inventory(self, notebook):
        tab = tk.Frame(notebook, bg=BG_DARK)
        notebook.add(tab, text="  Inventory  ")
        tab.rowconfigure(0, weight=0)
        tab.rowconfigure(1, weight=1)
        tab.columnconfigure(0, weight=1)

        # Summary bar
        summary_bar = tk.Frame(tab, bg=BG_CARD, padx=10, pady=6)
        summary_bar.grid(row=0, column=0, sticky="ew", pady=(0, 4))

        self._lbl_inv_summary = tk.Label(
            summary_bar, text="Boxes: 0  |  Racks: 0  |  In Transit: 0",
            font=FONT_SMALL, fg=TEXT_CYAN, bg=BG_CARD
        )
        self._lbl_inv_summary.pack(side="left")

        btn_refresh = tk.Button(
            summary_bar, text="Refresh", font=FONT_SMALL,
            bg=BG_CARD2, fg=TEXT_PRIMARY, relief="flat",
            padx=8, pady=2, cursor="hand2",
            command=self._refresh_inventory
        )
        btn_refresh.pack(side="right")

        # Inventory treeview: hierarchical rack -> boxes
        inv_frame = tk.Frame(tab, bg=BG_DARK,
                             highlightthickness=1,
                             highlightbackground=BORDER_COLOR)
        inv_frame.grid(row=1, column=0, sticky="nsew")
        inv_frame.rowconfigure(0, weight=1)
        inv_frame.columnconfigure(0, weight=1)

        cols = ("item", "type", "location", "status", "moves")
        self.inv_tree = ttk.Treeview(inv_frame, columns=cols,
                                      show="tree headings",
                                      style="Dark.Treeview")
        self.inv_tree.heading("#0",       text="")
        self.inv_tree.heading("item",     text="ID")
        self.inv_tree.heading("type",     text="Type")
        self.inv_tree.heading("location", text="Location")
        self.inv_tree.heading("status",   text="Status")
        self.inv_tree.heading("moves",    text="Moves")
        self.inv_tree.column("#0",        width=30,  stretch=False)
        self.inv_tree.column("item",      width=100, stretch=True)
        self.inv_tree.column("type",      width=60,  stretch=False)
        self.inv_tree.column("location",  width=100, stretch=True)
        self.inv_tree.column("status",    width=75,  stretch=False)
        self.inv_tree.column("moves",     width=50,  stretch=False)

        vsb = ttk.Scrollbar(inv_frame, orient="vertical", command=self.inv_tree.yview)
        self.inv_tree.configure(yscrollcommand=vsb.set)
        self.inv_tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")

        # Tag colors for inventory
        self.inv_tree.tag_configure("rack",      foreground=TEXT_CYAN)
        self.inv_tree.tag_configure("box",       foreground=TEXT_GREEN)
        self.inv_tree.tag_configure("in_transit", foreground=ACCENT_GOLD)
        self.inv_tree.tag_configure("empty_rack", foreground=TEXT_SECONDARY)

    # ─── TAB 3: MOVEMENT HISTORY ─────────────────────────────
    def _build_tab_history(self, notebook):
        tab = tk.Frame(notebook, bg=BG_DARK)
        notebook.add(tab, text="  History  ")
        tab.rowconfigure(0, weight=1)
        tab.columnconfigure(0, weight=1)

        hist_frame = tk.Frame(tab, bg=BG_DARK,
                              highlightthickness=1,
                              highlightbackground=BORDER_COLOR)
        hist_frame.grid(row=0, column=0, sticky="nsew")
        hist_frame.rowconfigure(0, weight=1)
        hist_frame.columnconfigure(0, weight=1)

        cols = ("time", "box", "from_rack", "to_rack")
        self.hist_tree = ttk.Treeview(hist_frame, columns=cols,
                                       show="headings",
                                       style="Dark.Treeview")
        self.hist_tree.heading("time",      text="Time")
        self.hist_tree.heading("box",       text="Box")
        self.hist_tree.heading("from_rack", text="From")
        self.hist_tree.heading("to_rack",   text="To")
        self.hist_tree.column("time",      width=140, stretch=True)
        self.hist_tree.column("box",       width=100, stretch=True)
        self.hist_tree.column("from_rack", width=100, stretch=True)
        self.hist_tree.column("to_rack",   width=100, stretch=True)

        vsb = ttk.Scrollbar(hist_frame, orient="vertical", command=self.hist_tree.yview)
        self.hist_tree.configure(yscrollcommand=vsb.set)
        self.hist_tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")

        self.hist_tree.tag_configure("move", foreground=TEXT_GREEN)

    # ─── TAB 4: SCAN LOG (Audit Trail) ──────────────────────
    def _build_tab_scanlog(self, notebook):
        tab = tk.Frame(notebook, bg=BG_DARK)
        notebook.add(tab, text="  Scan Log  ")
        tab.rowconfigure(0, weight=1)
        tab.columnconfigure(0, weight=1)

        log_frame = tk.Frame(tab, bg=BG_DARK,
                             highlightthickness=1,
                             highlightbackground=BORDER_COLOR)
        log_frame.grid(row=0, column=0, sticky="nsew")
        log_frame.rowconfigure(0, weight=1)
        log_frame.columnconfigure(0, weight=1)

        cols = ("time", "qr_data", "type", "action", "message")
        self.scanlog_tree = ttk.Treeview(log_frame, columns=cols,
                                          show="headings",
                                          style="Dark.Treeview")
        self.scanlog_tree.heading("time",    text="Time")
        self.scanlog_tree.heading("qr_data", text="QR Data")
        self.scanlog_tree.heading("type",    text="Type")
        self.scanlog_tree.heading("action",  text="Action")
        self.scanlog_tree.heading("message", text="Message")
        self.scanlog_tree.column("time",    width=140, stretch=False)
        self.scanlog_tree.column("qr_data", width=90,  stretch=True)
        self.scanlog_tree.column("type",    width=60,  stretch=False)
        self.scanlog_tree.column("action",  width=70,  stretch=False)
        self.scanlog_tree.column("message", width=200, stretch=True)

        vsb = ttk.Scrollbar(log_frame, orient="vertical", command=self.scanlog_tree.yview)
        self.scanlog_tree.configure(yscrollcommand=vsb.set)
        self.scanlog_tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")

        # Color-code actions
        self.scanlog_tree.tag_configure("source_set",       foreground=TEXT_CYAN)
        self.scanlog_tree.tag_configure("overwrite_source",  foreground=TEXT_ORANGE)
        self.scanlog_tree.tag_configure("pickup",           foreground=ACCENT_GOLD)
        self.scanlog_tree.tag_configure("drop",             foreground=TEXT_GREEN)
        self.scanlog_tree.tag_configure("error",            foreground=TEXT_RED)
        self.scanlog_tree.tag_configure("ignored",          foreground=TEXT_SECONDARY)

    # ─── TAB SWITCH HANDLER ──────────────────────────────────
    def _on_tab_changed(self, event):
        """Refresh data when switching to a DB-backed tab."""
        tab_idx = self.notebook.index(self.notebook.select())
        if tab_idx == 1:    # Inventory
            self._refresh_inventory()
        elif tab_idx == 2:  # History
            self._refresh_history()
        elif tab_idx == 3:  # Scan Log
            self._refresh_scanlog()

    # ─── REFRESH: INVENTORY ──────────────────────────────────
    def _refresh_inventory(self):
        """Reload rack-box relationships from DB."""
        self.inv_tree.delete(*self.inv_tree.get_children())

        try:
            all_racks = self.warehouse.get_all_racks()
            all_boxes = self.warehouse.get_all_boxes()
            summary   = self.warehouse.get_summary()

            self._lbl_inv_summary.config(
                text=(f"Boxes: {summary.get('total_boxes', 0)}  |  "
                      f"Racks: {summary.get('total_racks', 0)}  |  "
                      f"In Transit: {summary.get('boxes_in_transit', 0)}  |  "
                      f"Movements: {summary.get('total_movements', 0)}")
            )

            # Insert rack nodes with their boxes
            for rack_id in sorted(all_racks.keys()):
                box_ids = all_racks[rack_id]
                count = len(box_ids)
                tag = "rack" if count > 0 else "empty_rack"
                rack_node = self.inv_tree.insert(
                    "", "end", text="",
                    values=(rack_id, "RACK", f"{count} box(es)", "", ""),
                    tags=(tag,), open=True
                )
                for box_id in box_ids:
                    box_info = all_boxes.get(box_id, {})
                    move_count = len(box_info.get("history", []))
                    self.inv_tree.insert(
                        rack_node, "end", text="",
                        values=(box_id, "BOX", rack_id,
                                box_info.get("status", "stored"),
                                str(move_count)),
                        tags=("box",)
                    )

            # Show boxes in transit (on forklift)
            transit_boxes = [
                (bid, info) for bid, info in all_boxes.items()
                if info.get("status") == "in_transit"
            ]
            if transit_boxes:
                fl_node = self.inv_tree.insert(
                    "", "end", text="",
                    values=("FORKLIFT", "VEHICLE",
                            f"{len(transit_boxes)} box(es)", "", ""),
                    tags=("in_transit",), open=True
                )
                for box_id, info in transit_boxes:
                    self.inv_tree.insert(
                        fl_node, "end", text="",
                        values=(box_id, "BOX", "FORKLIFT", "in_transit",
                                str(len(info.get("history", [])))),
                        tags=("in_transit",)
                    )

        except Exception as e:
            print(f"[UI] Inventory refresh error: {e}")

    # ─── REFRESH: MOVEMENT HISTORY ───────────────────────────
    def _refresh_history(self):
        """Reload movement history from DB."""
        self.hist_tree.delete(*self.hist_tree.get_children())

        try:
            history = self.warehouse.get_movement_history(limit=200)
            for h in history:
                self.hist_tree.insert(
                    "", "end",
                    values=(h["time"], h["box_id"], h["from"], h["to"]),
                    tags=("move",)
                )
        except Exception as e:
            print(f"[UI] History refresh error: {e}")

    # ─── REFRESH: SCAN LOG ───────────────────────────────────
    def _refresh_scanlog(self):
        """Reload scan audit log from DB."""
        self.scanlog_tree.delete(*self.scanlog_tree.get_children())

        try:
            logs = self.warehouse.get_scan_log(limit=300)
            for log in logs:
                action = log.get("action", "")
                self.scanlog_tree.insert(
                    "", "end",
                    values=(log.get("scanned_at", ""),
                            log.get("qr_data", ""),
                            log.get("qr_type", ""),
                            action,
                            log.get("message", "")),
                    tags=(action,)
                )
        except Exception as e:
            print(f"[UI] Scan log refresh error: {e}")

    # ─── FORKLIFT STATUS PANEL ────────────────────────────────
    def _build_forklift_panel(self, parent):
        """Live forklift state indicator panel."""
        wrapper = tk.Frame(parent, bg=BG_DARK)
        wrapper.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        wrapper.columnconfigure(0, weight=1)

        hdr = tk.Frame(wrapper, bg=BG_CARD2, pady=5, padx=10)
        hdr.pack(fill="x")
        tk.Label(hdr, text="🚜  FORKLIFT STATUS", font=FONT_TITLE,
                 fg=ACCENT_GOLD, bg=BG_CARD2).pack(side="left")

        body = tk.Frame(wrapper, bg=BG_CARD,
                        highlightthickness=1,
                        highlightbackground=BORDER_COLOR,
                        padx=10, pady=8)
        body.pack(fill="x")

        # Row 1: State + Current box
        r1 = tk.Frame(body, bg=BG_CARD)
        r1.pack(fill="x", pady=(0, 4))

        tk.Label(r1, text="STATE:", font=FONT_SMALL,
                 fg=TEXT_SECONDARY, bg=BG_CARD).pack(side="left")
        self._lbl_fl_state = tk.Label(r1, text="● IDLE", font=("Helvetica Neue", 11, "bold"),
                                      fg=TEXT_GREEN, bg=BG_CARD)
        self._lbl_fl_state.pack(side="left", padx=(6, 20))

        tk.Label(r1, text="BOX:", font=FONT_SMALL,
                 fg=TEXT_SECONDARY, bg=BG_CARD).pack(side="left")
        self._lbl_fl_box = tk.Label(r1, text="—", font=FONT_SMALL,
                                    fg=TEXT_PRIMARY, bg=BG_CARD)
        self._lbl_fl_box.pack(side="left", padx=(4, 0))

        # Row 2: Source rack + Target rack
        r2 = tk.Frame(body, bg=BG_CARD)
        r2.pack(fill="x", pady=(0, 4))

        tk.Label(r2, text="SOURCE:", font=FONT_SMALL,
                 fg=TEXT_SECONDARY, bg=BG_CARD).pack(side="left")
        self._lbl_fl_src = tk.Label(r2, text="—", font=FONT_SMALL,
                                    fg=TEXT_CYAN, bg=BG_CARD)
        self._lbl_fl_src.pack(side="left", padx=(4, 20))

        tk.Label(r2, text="TARGET:", font=FONT_SMALL,
                 fg=TEXT_SECONDARY, bg=BG_CARD).pack(side="left")
        self._lbl_fl_tgt = tk.Label(r2, text="—", font=FONT_SMALL,
                                    fg=TEXT_CYAN, bg=BG_CARD)
        self._lbl_fl_tgt.pack(side="left", padx=(4, 0))

        # Row 3: Last action message
        r3 = tk.Frame(body, bg=BG_CARD)
        r3.pack(fill="x")

        tk.Label(r3, text="LAST:", font=FONT_SMALL,
                 fg=TEXT_SECONDARY, bg=BG_CARD).pack(side="left")
        self._lbl_fl_msg = tk.Label(r3, text="Waiting for scan…", font=FONT_SMALL,
                                    fg=TEXT_SECONDARY, bg=BG_CARD, anchor="w")
        self._lbl_fl_msg.pack(side="left", padx=(4, 0), fill="x", expand=True)

    def _update_forklift_ui(self, result):
        """Update forklift status panel from a ScanResult."""
        f = self.warehouse.get_forklift_state()
        state = f["state"]

        # State label color coding
        state_colors = {
            "IDLE":     TEXT_GREEN,
            "WAIT_BOX": TEXT_ORANGE,
            "LOADED":   HIGHLIGHT,
        }
        self._lbl_fl_state.config(
            text=f"● {state}",
            fg=state_colors.get(state, TEXT_SECONDARY)
        )

        # Box
        box = f.get("current_box")
        self._lbl_fl_box.config(text=box if box else "—")

        # Source / Target
        src = f.get("source_rack")
        self._lbl_fl_src.config(text=src if src else "—")

        tgt = result.target_rack if result else None
        self._lbl_fl_tgt.config(text=tgt if tgt else "—")

        # Last action message with color
        if result and result.accepted:
            action_colors = {
                "source_set":       TEXT_CYAN,
                "overwrite_source": TEXT_ORANGE,
                "pickup":           ACCENT_GOLD,
                "drop":             TEXT_GREEN,
            }
            self._lbl_fl_msg.config(
                text=result.message,
                fg=action_colors.get(result.action, TEXT_PRIMARY)
            )
        elif result and not result.accepted:
            self._lbl_fl_msg.config(
                text=result.message,
                fg=TEXT_RED if result.action == "error" else TEXT_SECONDARY
            )

    # ═══════════════════════════════════════════════════════════
    #  STATUS BAR
    # ═══════════════════════════════════════════════════════════
    def _build_status_bar(self):
        tk.Frame(self, bg=BORDER_COLOR, height=1).pack(fill="x")
        sb = tk.Frame(self, bg=BG_CARD, height=26)
        sb.pack(fill="x", side="bottom")
        sb.pack_propagate(False)

        self._lbl_status = tk.Label(sb, text="● IDLE", font=FONT_SMALL,
                                    fg=TEXT_SECONDARY, bg=BG_CARD)
        self._lbl_status.pack(side="left", padx=12)

        self._lbl_fps = tk.Label(sb, text="FPS: —", font=FONT_SMALL,
                                 fg=TEXT_SECONDARY, bg=BG_CARD)
        self._lbl_fps.pack(side="left", padx=12)

        self._lbl_count = tk.Label(sb, text="Detected: 0", font=FONT_SMALL,
                                   fg=TEXT_SECONDARY, bg=BG_CARD)
        self._lbl_count.pack(side="left", padx=12)

        self._lbl_ocr_status = tk.Label(
            sb,
            text="PaddleOCR: Loading…" if PADDLE_AVAILABLE else "PaddleOCR: Not installed",
            font=FONT_SMALL,
            fg=ACCENT_GOLD if PADDLE_AVAILABLE else TEXT_RED,
            bg=BG_CARD
        )
        self._lbl_ocr_status.pack(side="right", padx=12)

        ts = tk.Label(sb, text=datetime.now().strftime("%Y-%m-%d"),
                      font=FONT_SMALL, fg=TEXT_SECONDARY, bg=BG_CARD)
        ts.pack(side="right", padx=12)
        self._update_clock(ts)

    def _update_clock(self, lbl):
        lbl.config(text=datetime.now().strftime("%H:%M:%S  %Y-%m-%d"))
        self.after(1000, lambda: self._update_clock(lbl))

    # ═══════════════════════════════════════════════════════════
    #  IDLE CANVAS PLACEHOLDER
    # ═══════════════════════════════════════════════════════════
    def _show_idle_canvas(self):
        self.canvas.delete("all")
        w = self.canvas.winfo_width()  or 640
        h = self.canvas.winfo_height() or 480
        cx, cy = w // 2, h // 2
        self.canvas.create_rectangle(0, 0, w, h, fill="#000000", outline="")
        self.canvas.create_text(cx, cy - 20, text="[ No Feed ]",
                                fill=TEXT_SECONDARY, font=("Courier New", 16))
        self.canvas.create_text(cx, cy + 16,
                                text="Select a mode and press ▶  Start Camera",
                                fill=TEXT_SECONDARY, font=FONT_SMALL)

    # ═══════════════════════════════════════════════════════════
    #  OCR INIT
    # ═══════════════════════════════════════════════════════════
    def _init_ocr(self):
        try:
            import os
            os.environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "True"
            os.environ["FLAGS_call_stack_level"] = "0"
            self._lbl_ocr_status.config(text="PaddleOCR: Initializing…", fg=ACCENT_GOLD)
            self.ocr_engine = PaddleOCR(
                use_angle_cls=False,
                lang="en",
                show_log=False,
                use_gpu=False,
                enable_mkldnn=False,
                cpu_threads=2,
            )
            self.ocr_ready = True
            self._lbl_ocr_status.config(text="PaddleOCR: Ready ✓", fg=TEXT_GREEN)
        except Exception as e:
            self._lbl_ocr_status.config(text=f"PaddleOCR: Error — {e}", fg=TEXT_RED)

    # ═══════════════════════════════════════════════════════════
    #  MODE & CONTROLS
    # ═══════════════════════════════════════════════════════════
    def _set_mode(self, mode: str):
        self._stop_stream()
        self.mode.set(mode)
        for btn, m in [(self._btn_live, "live"),
                       (self._btn_video, "video"),
                       (self._btn_photo, "photo")]:
            btn.config(bg=ACCENT_BLUE if mode == m else BG_CARD2)

        if mode == "live":
            self._btn_upload.pack_forget()
            self._btn_start.config(text="▶  Start Camera", state="normal")
        else:
            self._btn_upload.pack(side="left", padx=4, before=self._btn_start)
            self._btn_start.config(
                text=("▶  Start Video" if mode == "video" else "▶  Scan Photo"),
                state="disabled"
            )
        self._show_idle_canvas()
        self._set_status("IDLE", TEXT_SECONDARY)

    def _on_ocr_toggle(self):
        if self.ocr_enabled.get():
            if not PADDLE_AVAILABLE:
                messagebox.showwarning("PaddleOCR",
                    "PaddleOCR is not installed.\nRun: pip install paddleocr paddlepaddle")
                self.ocr_enabled.set(False)
                return
            if not self.ocr_ready:
                messagebox.showinfo("PaddleOCR", "OCR engine is still initializing. Please wait.")
                self.ocr_enabled.set(False)
                return
            self._ocr_label.config(text="ON", fg=TEXT_GREEN)
        else:
            self._ocr_label.config(text="OFF", fg=TEXT_RED)

    def _upload_file(self):
        mode = self.mode.get()
        if mode == "video":
            path = filedialog.askopenfilename(
                title="Select Video File",
                filetypes=[("Video files", "*.mp4 *.avi *.mov *.mkv *.webm"), ("All", "*.*")]
            )
            if path:
                self._btn_start.config(state="normal")
                self._video_path = path
                self._set_status(f"Video: {os.path.basename(path)}", ACCENT_GOLD)
        else:
            path = filedialog.askopenfilename(
                title="Select Image File",
                filetypes=[("Images", "*.jpg *.jpeg *.png *.bmp *.tiff"), ("All", "*.*")]
            )
            if path:
                self._btn_start.config(state="normal")
                self._photo_path = path
                self._set_status(f"Photo: {os.path.basename(path)}", ACCENT_GOLD)

    def _toggle_camera(self):
        if self.running:
            self._stop_stream()
        else:
            self._start_stream()

    def _start_stream(self):
        mode = self.mode.get()
        self.running = True
        self._btn_start.config(text="⏹  Stop", bg=TEXT_RED, fg="white")
        self._set_status("RUNNING", TEXT_GREEN)

        if mode == "live":
            t = threading.Thread(target=self._live_loop, daemon=True)
        elif mode == "video":
            t = threading.Thread(target=self._video_loop,
                                 args=(getattr(self, "_video_path", ""),),
                                 daemon=True)
        else:
            self._process_photo(getattr(self, "_photo_path", ""))
            self._stop_stream()
            return
        t.start()

    def _stop_stream(self):
        self.running = False
        if self.cap:
            self.cap.release()
            self.cap = None
        self._btn_start.config(text="▶  Start Camera", bg=TEXT_GREEN, fg=BG_DARK)
        self._set_status("STOPPED", TEXT_SECONDARY)

    # ═══════════════════════════════════════════════════════════
    #  FRAME LOOPS
    # ═══════════════════════════════════════════════════════════
    def _live_loop(self):
        self.cap = cv2.VideoCapture(0)
        if not self.cap.isOpened():
            self.result_queue.put({"type": "error", "msg": "Cannot open camera."})
            self.running = False
            return
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH,  1280)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT,  720)
        self._stream_loop()

    def _video_loop(self, path: str):
        if not path or not os.path.exists(path):
            self.result_queue.put({"type": "error", "msg": "Video file not found."})
            self.running = False
            return
        self.cap = cv2.VideoCapture(path)
        self._stream_loop()

    def _stream_loop(self):
        fps_t = time.time()
        frame_count = 0
        while self.running and self.cap and self.cap.isOpened():
            ret, frame = self.cap.read()
            if not ret:
                # video ended — loop back
                if self.mode.get() == "video":
                    self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    continue
                break

            # ── RPi5 optimization: resize to 640×480 ──
            frame = cv2.resize(frame, (640, 480))

            frame_count += 1
            elapsed = time.time() - fps_t
            if elapsed >= 1.0:
                fps = frame_count / elapsed
                self.result_queue.put({"type": "fps", "value": fps})
                frame_count = 0
                fps_t = time.time()

            annotated, detections = self._detect_qr(frame)
            for det in detections:
                self._register_detection(det, frame)

            rgb = cv2.cvtColor(annotated, cv2.COLOR_BGR2RGB)
            self.result_queue.put({"type": "frame", "frame": rgb})
            time.sleep(0.066)  # ~15 FPS target for RPi5

        self.running = False

    def _process_photo(self, path: str):
        if not path or not os.path.exists(path):
            messagebox.showerror("Error", "Image file not found.")
            return
        frame = cv2.imread(path)
        if frame is None:
            messagebox.showerror("Error", "Cannot read image.")
            return
        annotated, detections = self._detect_qr(frame)
        for det in detections:
            self._register_detection(det, frame)
        rgb = cv2.cvtColor(annotated, cv2.COLOR_BGR2RGB)
        self.result_queue.put({"type": "frame", "frame": rgb})

    # ═══════════════════════════════════════════════════════════
    #  QR DETECTION
    # ═══════════════════════════════════════════════════════════
    def _detect_qr(self, frame: np.ndarray):
        annotated = frame.copy()
        detections = []

        # pyzbar (preferred — supports QR, Code128, EAN, etc.)
        if PYZBAR_OK:
            barcodes = pyzbar.decode(frame)
            for bc in barcodes:
                data = bc.data.decode("utf-8", errors="replace")
                pts  = np.array(bc.polygon, np.int32).reshape((-1, 1, 2))
                cv2.polylines(annotated, [pts], True, (0, 255, 80), 2)
                x, y, w, h = bc.rect
                cv2.rectangle(annotated, (x, y), (x+w, y+h), (0, 200, 255), 1)
                cv2.putText(annotated, data[:30], (x, y - 8),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 80), 1)
                roi = frame[max(0,y-5):y+h+5, max(0,x-5):x+w+5]
                detections.append({"data": data, "type": bc.type, "roi": roi,
                                   "bbox": (x, y, w, h)})
        else:
            # OpenCV fallback — QR only
            data, pts, _ = CV_QR_DETECTOR.detectAndDecode(frame)
            if data and pts is not None:
                pts = pts.astype(np.int32).reshape((-1, 1, 2))
                cv2.polylines(annotated, [pts], True, (0, 255, 80), 2)
                x, y, w, h = cv2.boundingRect(pts)
                cv2.putText(annotated, data[:30], (x, y - 8),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 80), 1)
                roi = frame[max(0,y-5):y+h+5, max(0,x-5):x+w+5]
                detections.append({"data": data, "type": "QRCODE", "roi": roi,
                                   "bbox": (x, y, w, h)})

        return annotated, detections

    # ═══════════════════════════════════════════════════════════
    #  REGISTER & UNIQUE ID
    # ═══════════════════════════════════════════════════════════
    def _register_detection(self, det: dict, frame: np.ndarray):
        data = det["data"]
        now  = datetime.now().strftime("%H:%M:%S")

        # ── Process forklift state machine ──
        scan_result = self.warehouse.process_scan(data)

        if data not in self.qr_registry:
            self.uid_counter += 1
            uid = f"QR-{self.uid_counter:04d}"
            self.qr_registry[data] = {"uid": uid, "first_seen": now, "count": 1}
        else:
            self.qr_registry[data]["count"] += 1
            uid = self.qr_registry[data]["uid"]

        # OCR on ROI — only when a NEW QR is detected (not every frame)
        ocr_text = ""
        if (self.ocr_enabled.get() and self.ocr_ready and self.ocr_engine
                and self.qr_registry[data]["count"] == 1):  # first detection only
            try:
                roi = det.get("roi")
                if roi is not None and roi.size > 0:
                    result = self.ocr_engine.ocr(roi, cls=True)
                    if result and result[0]:
                        ocr_text = " | ".join(
                            line[1][0] for line in result[0] if line and line[1]
                        )
            except Exception:
                ocr_text = "OCR error"

        entry = {
            "uid":      uid,
            "type":     det["type"],
            "data":     data,
            "time":     now,
            "ocr":      ocr_text,
            "count":    self.qr_registry[data]["count"],
            "_scan_result": scan_result,  # pass for UI update
        }
        self.result_queue.put({"type": "detection", "entry": entry})

    # ═══════════════════════════════════════════════════════════
    #  RESULT QUEUE POLLING (main thread)
    # ═══════════════════════════════════════════════════════════
    def _poll_results(self):
        try:
            while True:
                msg = self.result_queue.get_nowait()
                mt  = msg["type"]

                if mt == "frame":
                    self._render_frame(msg["frame"])

                elif mt == "detection":
                    entry = msg["entry"]
                    self._update_detection_ui(entry)
                    # Update forklift status panel
                    scan_result = entry.pop("_scan_result", None)
                    if scan_result:
                        # Handle unknown QR -> registration dialog
                        if scan_result.needs_registration:
                            self._show_registration_dialog(scan_result.raw_data)
                        else:
                            self._update_forklift_ui(scan_result)
                            # Auto-refresh DB tabs on meaningful state changes
                            if scan_result.action in ("pickup", "drop"):
                                self._refresh_inventory()
                                self._refresh_history()

                elif mt == "fps":
                    self._lbl_fps.config(text=f"FPS: {msg['value']:.1f}")

                elif mt == "error":
                    messagebox.showerror("Error", msg["msg"])
                    self._stop_stream()

        except queue.Empty:
            pass
        self.after(30, self._poll_results)

    # ─── Render frame onto canvas ─────────────────────────────
    def _render_frame(self, rgb_array: np.ndarray):
        cw = self.canvas.winfo_width()
        ch = self.canvas.winfo_height()
        if cw < 10 or ch < 10:
            return
        ih, iw = rgb_array.shape[:2]
        scale = min(cw / iw, ch / ih)
        nw, nh = int(iw * scale), int(ih * scale)
        img = Image.fromarray(rgb_array).resize((nw, nh), Image.BILINEAR)
        self._tk_img = ImageTk.PhotoImage(img)
        self.canvas.delete("all")
        self.canvas.create_image(cw // 2, ch // 2, anchor="center",
                                 image=self._tk_img)

    # ─── Update QR data box + table ──────────────────────────
    def _update_detection_ui(self, entry: dict):
        # Only add new unique QRs to table; update count for duplicates
        uid  = entry["uid"]
        data = entry["data"]

        # check if already in tree
        existing = None
        for iid in self.tree.get_children():
            if self.tree.item(iid, "values")[0] == uid:
                existing = iid
                break

        tag = "new" if existing is None else "dup"
        row_vals = (uid, entry["type"], data[:60], entry["time"], entry["ocr"][:40])

        if existing is None:
            iid = self.tree.insert("", 0, values=row_vals, tags=(tag,))
            self.tree.tag_configure("new", foreground=TEXT_GREEN)
            self.tree.tag_configure("dup", foreground=TEXT_SECONDARY)
            self.detected_log.append(entry)
            # Update big text display
            self._append_qr_text(entry)
        else:
            self.tree.item(existing, values=row_vals)

        self._lbl_count.config(text=f"Detected: {len(self.qr_registry)}")

    def _append_qr_text(self, entry: dict):
        self.qr_text.config(state="normal")
        ts = f"[{entry['time']}] "
        uid_str  = f"{entry['uid']}  "
        type_str = f"({entry['type']})\n"
        data_str = f"  DATA : {entry['data']}\n"
        ocr_str  = f"  OCR  : {entry['ocr']}\n" if entry.get("ocr") else ""

        # Show warehouse action if present
        scan_r = entry.get("_scan_result")
        action_str = ""
        if scan_r and scan_r.accepted:
            action_str = f"  ⚡ {scan_r.action.upper()}: {scan_r.message}\n"
        elif scan_r and scan_r.action == "error":
            action_str = f"  ❌ {scan_r.message}\n"

        sep = "─" * 44 + "\n"

        self.qr_text.insert("end", ts,       "ts")
        self.qr_text.insert("end", uid_str,  "uid")
        self.qr_text.insert("end", type_str, "typ")
        self.qr_text.insert("end", data_str, "dat")
        if ocr_str:
            self.qr_text.insert("end", ocr_str, "ocr")
        if action_str:
            tag = "action_ok" if (scan_r and scan_r.accepted) else "action_err"
            self.qr_text.insert("end", action_str, tag)
        self.qr_text.insert("end", sep, "sep")
        self.qr_text.tag_config("ts",  foreground=TEXT_SECONDARY)
        self.qr_text.tag_config("uid", foreground=ACCENT_GOLD)
        self.qr_text.tag_config("typ", foreground=TEXT_SECONDARY)
        self.qr_text.tag_config("dat", foreground=TEXT_GREEN)
        self.qr_text.tag_config("ocr", foreground=HIGHLIGHT)
        self.qr_text.tag_config("action_ok",  foreground=TEXT_CYAN)
        self.qr_text.tag_config("action_err", foreground=TEXT_RED)
        self.qr_text.tag_config("sep", foreground=BORDER_COLOR)
        self.qr_text.see("end")
        self.qr_text.config(state="disabled")

    # ═══════════════════════════════════════════════════════════
    #  MISC UI
    # ═══════════════════════════════════════════════════════════
    def _clear_log(self):
        if messagebox.askyesno("Clear", "Clear all detected QR logs?"):
            self.qr_registry.clear()
            self.detected_log.clear()
            self.uid_counter = 0
            self.tree.delete(*self.tree.get_children())
            self.qr_text.config(state="normal")
            self.qr_text.delete("1.0", "end")
            self.qr_text.config(state="disabled")
            self._lbl_count.config(text="Detected: 0")

    def _set_status(self, text: str, color: str):
        dot = "●"
        self._lbl_status.config(text=f"{dot} {text}", fg=color)

    def _tree_context_menu(self, event):
        iid = self.tree.identify_row(event.y)
        if not iid:
            return
        self.tree.selection_set(iid)
        vals = self.tree.item(iid, "values")
        menu = tk.Menu(self, tearoff=False, bg=BG_CARD2, fg=TEXT_PRIMARY,
                       activebackground=ACCENT_BLUE)
        menu.add_command(label="Copy Data",
                         command=lambda: self._copy_to_clipboard(vals[2]))
        menu.add_command(label="Copy OCR Text",
                         command=lambda: self._copy_to_clipboard(vals[4]))
        menu.add_separator()
        menu.add_command(label="Delete Row",
                         command=lambda: self.tree.delete(iid))
        menu.post(event.x_root, event.y_root)

    def _copy_to_clipboard(self, text: str):
        self.clipboard_clear()
        self.clipboard_append(text)

    # ═══════════════════════════════════════════════════════════
    #  QR REGISTRATION DIALOG
    # ═══════════════════════════════════════════════════════════
    def _show_registration_dialog(self, qr_data: str):
        """Show a popup to classify an unknown QR code as rack or box."""
        dialog = tk.Toplevel(self)
        dialog.title("Register QR Code")
        dialog.configure(bg=BG_CARD)
        dialog.geometry("400x220")
        dialog.resizable(False, False)
        dialog.transient(self)
        dialog.grab_set()

        # Center on parent
        dialog.update_idletasks()
        x = self.winfo_x() + (self.winfo_width() - 400) // 2
        y = self.winfo_y() + (self.winfo_height() - 220) // 2
        dialog.geometry(f"+{x}+{y}")

        # Header
        tk.Label(dialog, text="New QR Code Detected",
                 font=("Helvetica Neue", 13, "bold"),
                 fg=ACCENT_GOLD, bg=BG_CARD).pack(pady=(16, 4))

        # QR data display
        tk.Label(dialog, text=f'"{qr_data}"',
                 font=FONT_MONO, fg=TEXT_GREEN, bg=BG_CARD).pack(pady=(0, 8))

        tk.Label(dialog, text="What is this QR code?",
                 font=FONT_BODY, fg=TEXT_SECONDARY, bg=BG_CARD).pack(pady=(0, 12))

        # Buttons
        btn_frame = tk.Frame(dialog, bg=BG_CARD)
        btn_frame.pack(pady=(0, 16))

        def register_as(item_type):
            self.warehouse.register_qr(qr_data, item_type, qr_data)
            dialog.destroy()
            # Re-process the scan now that it's registered
            result = self.warehouse.process_scan(qr_data)
            if result:
                self._update_forklift_ui(result)
                if result.action in ("pickup", "drop"):
                    self._refresh_inventory()
                    self._refresh_history()

        tk.Button(btn_frame, text="  RACK / Location  ",
                  font=("Helvetica Neue", 11, "bold"),
                  bg=TEXT_CYAN, fg=BG_DARK, relief="flat",
                  padx=16, pady=8, cursor="hand2",
                  command=lambda: register_as("rack")
                  ).pack(side="left", padx=10)

        tk.Button(btn_frame, text="  BOX / Item  ",
                  font=("Helvetica Neue", 11, "bold"),
                  bg=ACCENT_GOLD, fg=BG_DARK, relief="flat",
                  padx=16, pady=8, cursor="hand2",
                  command=lambda: register_as("box")
                  ).pack(side="left", padx=10)

        # Skip button
        tk.Button(dialog, text="Skip", font=FONT_SMALL,
                  bg=BG_CARD2, fg=TEXT_SECONDARY, relief="flat",
                  padx=10, pady=3, cursor="hand2",
                  command=dialog.destroy
                  ).pack(pady=(0, 10))

    def _on_close(self):
        self.running = False
        self.warehouse.save_on_exit()  # persist warehouse state
        if self.cap:
            self.cap.release()
        self.destroy()


# ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app = QRScannerApp()
    app.mainloop()
