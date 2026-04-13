#!/usr/bin/env python3
"""
Warehouse Forklift State Machine Engine
Atlas Copco x Vishwakarma University

Manages:
  - Forklift state machine with flexible scan order
  - QR registry: arbitrary UIDs mapped to rack/box type via DB
  - Box locations and transit status
  - Rack contents and movement history
  - SQLite persistence via database.py
  - Duplicate scan filtering (cooldown-based debounce)

Scan Order Flexibility:
  - RACK first, then BOX -> pickup from that rack
  - BOX first, then RACK -> pickup (source = last known location)
  Both flows end with: scan RACK -> drop at destination

Usage:
  from logic import WarehouseEngine
  engine = WarehouseEngine()
  result = engine.process_scan("some-uid-123")
"""

import time
from datetime import datetime
from typing import Optional

from database import WarehouseDB


# ─────────────────────────────────────────────────────────────
#  QR TYPE CONSTANTS
# ─────────────────────────────────────────────────────────────
QR_TYPE_BOX         = "box"
QR_TYPE_RACK        = "rack"
QR_TYPE_UNKNOWN     = "unknown"
QR_TYPE_UNREGISTERED = "unregistered"


def classify_qr_by_prefix(data: str) -> str:
    """Fallback: classify QR code by prefix convention.
    Works for: BOX_<id>, RACK_<id>, GROUND_<id>
    Returns QR_TYPE_UNKNOWN if no prefix match.
    """
    if not isinstance(data, str) or not data:
        return QR_TYPE_UNKNOWN
    upper = data.strip().upper()
    if upper.startswith("BOX_") and len(upper) > 4:
        return QR_TYPE_BOX
    elif upper.startswith("RACK_") and len(upper) > 5:
        return QR_TYPE_RACK
    elif upper.startswith("GROUND_") and len(upper) > 7:
        return QR_TYPE_RACK  # ground locations treated as racks
    return QR_TYPE_UNKNOWN


# ─────────────────────────────────────────────────────────────
#  SCAN RESULT (returned to caller for UI updates)
# ─────────────────────────────────────────────────────────────
class ScanResult:
    """Lightweight result object from a scan event."""
    __slots__ = ("accepted", "action", "message", "state",
                 "box", "source_rack", "target_rack", "qr_type",
                 "needs_registration", "raw_data")

    def __init__(self, accepted: bool, action: str, message: str,
                 state: str, box: Optional[str] = None,
                 source_rack: Optional[str] = None,
                 target_rack: Optional[str] = None,
                 qr_type: str = QR_TYPE_UNKNOWN,
                 needs_registration: bool = False,
                 raw_data: str = ""):
        self.accepted     = accepted
        self.action       = action
        self.message      = message
        self.state        = state
        self.box          = box
        self.source_rack  = source_rack
        self.target_rack  = target_rack
        self.qr_type      = qr_type
        self.needs_registration = needs_registration  # True = UI should show registration dialog
        self.raw_data     = raw_data                  # original QR data for registration

    def __repr__(self):
        return f"ScanResult({self.action}: {self.message})"


# ─────────────────────────────────────────────────────────────
#  WAREHOUSE ENGINE
# ─────────────────────────────────────────────────────────────
class WarehouseEngine:
    """
    Core warehouse tracking engine backed by SQLite.

    QR Classification:
      1. Check DB registry (qr_registry table) first
      2. Fall back to prefix convention (BOX_, RACK_, GROUND_)
      3. If still unknown -> return needs_registration=True for UI dialog

    State Machine (flexible order):
      IDLE      -> scan RACK -> set source, move to WAIT_BOX
                -> scan BOX  -> pickup (source = last known), move to LOADED
      WAIT_BOX  -> scan BOX  -> pickup from source rack, move to LOADED
                -> scan RACK -> overwrite source rack
      LOADED    -> scan RACK -> drop box at rack, reset to IDLE
    """

    STATE_IDLE     = "IDLE"
    STATE_WAIT_BOX = "WAIT_BOX"
    STATE_LOADED   = "LOADED"

    DEFAULT_COOLDOWN = 1.5  # seconds

    def __init__(self, db_path: str = "warehouse.db",
                 cooldown: float = DEFAULT_COOLDOWN,
                 **kwargs):
        self.cooldown = cooldown

        # ── SQLite Database ──
        self.db = WarehouseDB(db_path=db_path)

        # ── Forklift State (in-memory, resets on restart) ──
        self.forklift = {
            "state":       self.STATE_IDLE,
            "current_box": None,
            "source_rack": None,
            "target_rack": None,
        }

        # ── Debounce Memory ──
        self._last_seen: dict[str, float] = {}

        summary = self.db.get_summary()
        print(f"[INIT] Warehouse DB: {summary['total_boxes']} boxes, "
              f"{summary['total_racks']} racks, "
              f"{summary['total_movements']} movements")

    # ═══════════════════════════════════════════════════════════
    #  QR CLASSIFICATION (DB first, then prefix fallback)
    # ═══════════════════════════════════════════════════════════
    def classify_qr(self, data: str) -> str:
        """
        Classify QR data:
          1. Look up in DB registry
          2. Fall back to prefix (BOX_/RACK_/GROUND_)
          3. Return 'unregistered' if neither works
        """
        # DB lookup first
        entry = self.db.lookup_qr(data)
        if entry:
            return entry["item_type"]  # "rack" or "box"

        # Prefix fallback
        prefix_type = classify_qr_by_prefix(data)
        if prefix_type != QR_TYPE_UNKNOWN:
            # Auto-register prefix-based QR codes
            self.db.register_qr(data, prefix_type, data)
            return prefix_type

        return QR_TYPE_UNREGISTERED

    def register_qr(self, qr_data: str, item_type: str, display_name: str = None):
        """Register a QR code as rack or box (called from UI dialog)."""
        self.db.register_qr(qr_data, item_type, display_name)

    # ═══════════════════════════════════════════════════════════
    #  MAIN SCAN PROCESSOR
    # ═══════════════════════════════════════════════════════════
    def process_scan(self, data: str) -> ScanResult:
        """
        Main entry point. Feed raw QR data string.
        Returns a ScanResult describing what happened.
        """
        data = data.strip()
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # ── Debounce duplicate scans ──
        if self._is_duplicate(data):
            return ScanResult(
                accepted=False, action="duplicate",
                message=f"Duplicate scan filtered: {data}",
                state=self.forklift["state"],
                qr_type=QR_TYPE_UNKNOWN, raw_data=data
            )

        # ── Classify QR ──
        qr_type = self.classify_qr(data)

        # ── Unregistered QR -> ask UI to register ──
        if qr_type == QR_TYPE_UNREGISTERED:
            self.db.log_scan(data, "unregistered", "needs_registration",
                             f"Unknown QR, needs registration: {data}", now_str)
            return ScanResult(
                accepted=False, action="needs_registration",
                message=f"Unknown QR: {data} - Please classify as Rack or Box",
                state=self.forklift["state"],
                qr_type=QR_TYPE_UNREGISTERED,
                needs_registration=True,
                raw_data=data
            )

        is_rack = (qr_type == QR_TYPE_RACK)
        is_box  = (qr_type == QR_TYPE_BOX)
        state   = self.forklift["state"]

        # =========================================================
        #  STATE: IDLE
        # =========================================================
        if state == self.STATE_IDLE:

            if is_rack:
                # Rack scanned first -> set as source, wait for box
                self.forklift["source_rack"] = data
                self.forklift["state"]       = self.STATE_WAIT_BOX

                self.db.log_scan(data, qr_type, "source_set",
                                 f"Source rack set: {data}", now_str)
                return ScanResult(
                    accepted=True, action="source_set",
                    message=f"Source rack set: {data}",
                    state=self.STATE_WAIT_BOX,
                    source_rack=data, qr_type=qr_type
                )

            elif is_box:
                # BOX scanned first -> pickup directly
                # Source = last known location from DB (or "DIRECT")
                box_info = self.db.get_box(data)
                source = box_info["location"] if box_info else "DIRECT"

                self.db.pickup_box(data, source)

                self.forklift["current_box"]  = data
                self.forklift["source_rack"]  = source
                self.forklift["state"]        = self.STATE_LOADED

                self.db.log_scan(data, qr_type, "pickup",
                                 f"PICKED UP {data} (from {source})", now_str)
                return ScanResult(
                    accepted=True, action="pickup",
                    message=f"PICKED UP {data} (from {source})",
                    state=self.STATE_LOADED,
                    box=data, source_rack=source, qr_type=qr_type
                )

        # =========================================================
        #  STATE: WAIT_BOX (rack was scanned first)
        # =========================================================
        elif state == self.STATE_WAIT_BOX:

            if is_box:
                box  = data
                rack = self.forklift["source_rack"]

                self.db.pickup_box(box, rack)

                self.forklift["current_box"] = box
                self.forklift["state"]       = self.STATE_LOADED

                self.db.log_scan(data, qr_type, "pickup",
                                 f"PICKED UP {box} from {rack}", now_str)
                return ScanResult(
                    accepted=True, action="pickup",
                    message=f"PICKED UP {box} from {rack}",
                    state=self.STATE_LOADED,
                    box=box, source_rack=rack, qr_type=qr_type
                )

            elif is_rack:
                old = self.forklift["source_rack"]
                self.forklift["source_rack"] = data

                self.db.log_scan(data, qr_type, "overwrite_source",
                                 f"Source rack updated: {old} -> {data}", now_str)
                return ScanResult(
                    accepted=True, action="overwrite_source",
                    message=f"Source rack updated: {old} -> {data}",
                    state=self.STATE_WAIT_BOX,
                    source_rack=data, qr_type=qr_type
                )

        # =========================================================
        #  STATE: LOADED (waiting for destination rack)
        # =========================================================
        elif state == self.STATE_LOADED:

            if is_rack:
                box      = self.forklift["current_box"]
                new_rack = data
                old_rack = self.forklift["source_rack"]

                self.db.drop_box(box, old_rack, new_rack, now_str)

                self.db.log_scan(data, qr_type, "drop",
                                 f"DROPPED {box} at {new_rack} (from {old_rack})",
                                 now_str)

                result = ScanResult(
                    accepted=True, action="drop",
                    message=f"DROPPED {box} at {new_rack} (from {old_rack})",
                    state=self.STATE_IDLE,
                    box=box, source_rack=old_rack, target_rack=new_rack,
                    qr_type=qr_type
                )

                self._reset_forklift()
                return result

            elif is_box:
                self.db.log_scan(data, qr_type, "error",
                                 f"Already loaded with {self.forklift['current_box']}",
                                 now_str)
                return ScanResult(
                    accepted=False, action="error",
                    message=f"Forklift loaded with {self.forklift['current_box']}. Scan a RACK to drop.",
                    state=self.STATE_LOADED, box=data, qr_type=qr_type
                )

        # Fallback
        return ScanResult(
            accepted=False, action="ignored",
            message=f"Unhandled: {state} / {qr_type}",
            state=self.forklift["state"], qr_type=qr_type
        )

    # ═══════════════════════════════════════════════════════════
    #  PUBLIC API (query methods)
    # ═══════════════════════════════════════════════════════════
    def get_forklift_state(self) -> dict:
        return dict(self.forklift)

    def get_box_info(self, box_id: str) -> Optional[dict]:
        return self.db.get_box(box_id)

    def get_rack_contents(self, rack_id: str) -> list:
        return self.db.get_rack_contents(rack_id)

    def get_all_boxes(self) -> dict:
        return self.db.get_all_boxes()

    def get_all_racks(self) -> dict:
        return self.db.get_all_racks()

    def get_summary(self) -> dict:
        summary = self.db.get_summary()
        summary["forklift"] = dict(self.forklift)
        return summary

    def get_movement_history(self, box_id=None, limit=100) -> list:
        return self.db.get_movement_history(box_id, limit)

    def get_scan_log(self, limit=200) -> list:
        return self.db.get_scan_log(limit)

    def get_all_registered_qr(self) -> list:
        return self.db.get_all_registered()

    # ═══════════════════════════════════════════════════════════
    #  DEBOUNCE
    # ═══════════════════════════════════════════════════════════
    def _is_duplicate(self, data: str) -> bool:
        now = time.time()
        if data in self._last_seen:
            if now - self._last_seen[data] < self.cooldown:
                return True
        self._last_seen[data] = now
        return False

    # ═══════════════════════════════════════════════════════════
    #  FORKLIFT RESET
    # ═══════════════════════════════════════════════════════════
    def _reset_forklift(self):
        self.forklift = {
            "state":       self.STATE_IDLE,
            "current_box": None,
            "source_rack": None,
            "target_rack": None,
        }

    # ═══════════════════════════════════════════════════════════
    #  CLEANUP
    # ═══════════════════════════════════════════════════════════
    def save_on_exit(self):
        if self.db:
            self.db.close()
