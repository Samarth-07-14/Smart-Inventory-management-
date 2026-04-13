#!/usr/bin/env python3
"""
SQLite Database Layer for Warehouse Forklift Tracking System
Atlas Copco x Vishwakarma University

Tables:
  - qr_registry: Maps arbitrary QR data to item type (rack/box)
  - boxes:       Box inventory with current location and status
  - racks:       Rack-to-box many-to-many mapping
  - movements:   Full movement history (from -> to with timestamp)
  - scan_log:    Raw scan event log for auditing

Thread-safe: uses check_same_thread=False for Tkinter integration.
"""

import sqlite3
import os
from datetime import datetime
from typing import Optional


DEFAULT_DB_PATH = "warehouse.db"


class WarehouseDB:
    """SQLite-backed warehouse persistence layer."""

    def __init__(self, db_path: str = DEFAULT_DB_PATH):
        self.db_path = db_path
        self.conn: Optional[sqlite3.Connection] = None
        self._connect()
        self._create_tables()
        self._migrate_from_json()

    # ═══════════════════════════════════════════════════════════
    #  CONNECTION
    # ═══════════════════════════════════════════════════════════
    def _connect(self):
        """Open a thread-safe SQLite connection with WAL mode."""
        self.conn = sqlite3.connect(
            self.db_path,
            check_same_thread=False,   # safe for Tkinter + worker threads
            timeout=10.0
        )
        self.conn.execute("PRAGMA journal_mode=WAL")      # concurrent reads
        self.conn.execute("PRAGMA foreign_keys=ON")        # enforce FK
        self.conn.execute("PRAGMA busy_timeout=5000")      # wait on locks
        self.conn.row_factory = sqlite3.Row                # dict-like rows

    # ═══════════════════════════════════════════════════════════
    #  SCHEMA
    # ═══════════════════════════════════════════════════════════
    def _create_tables(self):
        """Create tables if they don't exist."""
        cur = self.conn.cursor()

        # QR Registry: maps arbitrary QR data to item type
        cur.execute("""
            CREATE TABLE IF NOT EXISTS qr_registry (
                qr_data       TEXT    PRIMARY KEY,
                item_type     TEXT    NOT NULL
                    CHECK(item_type IN ('rack', 'box')),
                display_name  TEXT,
                registered_at TEXT    NOT NULL
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS boxes (
                box_id      TEXT    PRIMARY KEY,
                location    TEXT    NOT NULL,
                status      TEXT    NOT NULL DEFAULT 'stored'
                    CHECK(status IN ('stored', 'in_transit'))
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS racks (
                rack_id     TEXT    NOT NULL,
                box_id      TEXT    NOT NULL,
                PRIMARY KEY (rack_id, box_id),
                FOREIGN KEY (box_id) REFERENCES boxes(box_id)
                    ON DELETE CASCADE
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS movements (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                box_id      TEXT    NOT NULL,
                from_rack   TEXT    NOT NULL,
                to_rack     TEXT    NOT NULL,
                moved_at    TEXT    NOT NULL,
                FOREIGN KEY (box_id) REFERENCES boxes(box_id)
                    ON DELETE CASCADE
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS scan_log (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                qr_data     TEXT    NOT NULL,
                qr_type     TEXT    NOT NULL,
                action      TEXT    NOT NULL,
                message     TEXT,
                scanned_at  TEXT    NOT NULL
            )
        """)

        # Indexes for common queries
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_movements_box
            ON movements(box_id)
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_racks_rack
            ON racks(rack_id)
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_scan_log_time
            ON scan_log(scanned_at)
        """)

        self.conn.commit()

    # ═══════════════════════════════════════════════════════════
    #  JSON MIGRATION (one-time import from old warehouse_state.json)
    # ═══════════════════════════════════════════════════════════
    def _migrate_from_json(self):
        """Import existing JSON state into SQLite on first run."""
        json_path = "warehouse_state.json"
        if not os.path.exists(json_path):
            return

        # Only migrate if DB is empty
        row = self.conn.execute("SELECT COUNT(*) FROM boxes").fetchone()
        if row[0] > 0:
            return

        try:
            import json
            with open(json_path, "r") as f:
                state = json.load(f)

            boxes = state.get("boxes", {})
            racks = state.get("racks", {})

            cur = self.conn.cursor()

            for box_id, info in boxes.items():
                cur.execute(
                    "INSERT OR IGNORE INTO boxes (box_id, location, status) VALUES (?, ?, ?)",
                    (box_id, info.get("location", "UNKNOWN"), info.get("status", "stored"))
                )
                # Import history
                for h in info.get("history", []):
                    cur.execute(
                        "INSERT INTO movements (box_id, from_rack, to_rack, moved_at) VALUES (?, ?, ?, ?)",
                        (box_id, h.get("from", ""), h.get("to", ""), h.get("time", ""))
                    )

            for rack_id, box_list in racks.items():
                for box_id in box_list:
                    # Ensure box exists
                    cur.execute(
                        "INSERT OR IGNORE INTO boxes (box_id, location, status) VALUES (?, ?, ?)",
                        (box_id, rack_id, "stored")
                    )
                    cur.execute(
                        "INSERT OR IGNORE INTO racks (rack_id, box_id) VALUES (?, ?)",
                        (rack_id, box_id)
                    )

            self.conn.commit()
            print(f"[DB] Migrated JSON state: {len(boxes)} boxes, {len(racks)} racks")

            # Rename old JSON to prevent re-migration
            os.rename(json_path, json_path + ".bak")
            print(f"[DB] Renamed {json_path} -> {json_path}.bak")

        except Exception as e:
            print(f"[DB WARN] JSON migration failed: {e}")

    # ═══════════════════════════════════════════════════════════
    #  QR REGISTRY (maps arbitrary QR data -> rack/box type)
    # ═══════════════════════════════════════════════════════════
    def register_qr(self, qr_data: str, item_type: str,
                    display_name: str = None):
        """Register a QR code as rack or box."""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.conn.execute(
            "INSERT OR REPLACE INTO qr_registry "
            "(qr_data, item_type, display_name, registered_at) "
            "VALUES (?, ?, ?, ?)",
            (qr_data, item_type, display_name or qr_data, now)
        )
        self.conn.commit()

    def lookup_qr(self, qr_data: str) -> Optional[dict]:
        """Look up a QR code in the registry. Returns None if not registered."""
        row = self.conn.execute(
            "SELECT qr_data, item_type, display_name, registered_at "
            "FROM qr_registry WHERE qr_data = ?",
            (qr_data,)
        ).fetchone()
        if row:
            return dict(row)
        return None

    def get_all_registered(self) -> list:
        """Get all registered QR codes."""
        rows = self.conn.execute(
            "SELECT qr_data, item_type, display_name, registered_at "
            "FROM qr_registry ORDER BY item_type, qr_data"
        ).fetchall()
        return [dict(r) for r in rows]

    def delete_qr(self, qr_data: str):
        """Remove a QR code from the registry."""
        self.conn.execute(
            "DELETE FROM qr_registry WHERE qr_data = ?",
            (qr_data,)
        )
        self.conn.commit()

    # ═══════════════════════════════════════════════════════════
    #  BOX OPERATIONS
    # ═══════════════════════════════════════════════════════════
    def ensure_box(self, box_id: str, location: str = "UNKNOWN", status: str = "stored"):
        """Create box if it doesn't exist."""
        self.conn.execute(
            "INSERT OR IGNORE INTO boxes (box_id, location, status) VALUES (?, ?, ?)",
            (box_id, location, status)
        )
        self.conn.commit()

    def update_box(self, box_id: str, location: str, status: str):
        """Update box location and status."""
        self.conn.execute(
            "UPDATE boxes SET location = ?, status = ? WHERE box_id = ?",
            (location, status, box_id)
        )
        self.conn.commit()

    def get_box(self, box_id: str) -> Optional[dict]:
        """Get box info with movement history."""
        row = self.conn.execute(
            "SELECT box_id, location, status FROM boxes WHERE box_id = ?",
            (box_id,)
        ).fetchone()
        if not row:
            return None

        history = self.conn.execute(
            "SELECT from_rack, to_rack, moved_at FROM movements WHERE box_id = ? ORDER BY id",
            (box_id,)
        ).fetchall()

        return {
            "box_id":   row["box_id"],
            "location": row["location"],
            "status":   row["status"],
            "history":  [{"from": h["from_rack"], "to": h["to_rack"], "time": h["moved_at"]}
                         for h in history]
        }

    def get_all_boxes(self) -> dict:
        """Get all boxes as a dict keyed by box_id."""
        rows = self.conn.execute("SELECT box_id, location, status FROM boxes").fetchall()
        result = {}
        for r in rows:
            bid = r["box_id"]
            history = self.conn.execute(
                "SELECT from_rack, to_rack, moved_at FROM movements WHERE box_id = ? ORDER BY id",
                (bid,)
            ).fetchall()
            result[bid] = {
                "location": r["location"],
                "status":   r["status"],
                "history":  [{"from": h["from_rack"], "to": h["to_rack"], "time": h["moved_at"]}
                             for h in history]
            }
        return result

    # ═══════════════════════════════════════════════════════════
    #  RACK OPERATIONS
    # ═══════════════════════════════════════════════════════════
    def add_box_to_rack(self, rack_id: str, box_id: str):
        """Link a box to a rack."""
        self.conn.execute(
            "INSERT OR IGNORE INTO racks (rack_id, box_id) VALUES (?, ?)",
            (rack_id, box_id)
        )
        self.conn.commit()

    def remove_box_from_rack(self, rack_id: str, box_id: str):
        """Remove a box from a rack."""
        self.conn.execute(
            "DELETE FROM racks WHERE rack_id = ? AND box_id = ?",
            (rack_id, box_id)
        )
        self.conn.commit()

    def get_rack_contents(self, rack_id: str) -> list:
        """Get sorted list of box IDs at a rack."""
        rows = self.conn.execute(
            "SELECT box_id FROM racks WHERE rack_id = ? ORDER BY box_id",
            (rack_id,)
        ).fetchall()
        return [r["box_id"] for r in rows]

    def get_all_racks(self) -> dict:
        """Get all racks as { rack_id: [box_ids] }."""
        rows = self.conn.execute(
            "SELECT DISTINCT rack_id FROM racks ORDER BY rack_id"
        ).fetchall()
        result = {}
        for r in rows:
            rid = r["rack_id"]
            result[rid] = self.get_rack_contents(rid)
        return result

    # ═══════════════════════════════════════════════════════════
    #  MOVEMENT HISTORY
    # ═══════════════════════════════════════════════════════════
    def record_movement(self, box_id: str, from_rack: str, to_rack: str,
                        moved_at: Optional[str] = None):
        """Record a box movement in history."""
        if moved_at is None:
            moved_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.conn.execute(
            "INSERT INTO movements (box_id, from_rack, to_rack, moved_at) VALUES (?, ?, ?, ?)",
            (box_id, from_rack, to_rack, moved_at)
        )
        self.conn.commit()

    def get_movement_history(self, box_id: Optional[str] = None,
                             limit: int = 100) -> list:
        """Get movement history, optionally filtered by box."""
        if box_id:
            rows = self.conn.execute(
                "SELECT box_id, from_rack, to_rack, moved_at FROM movements "
                "WHERE box_id = ? ORDER BY id DESC LIMIT ?",
                (box_id, limit)
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT box_id, from_rack, to_rack, moved_at FROM movements "
                "ORDER BY id DESC LIMIT ?",
                (limit,)
            ).fetchall()
        return [{"box_id": r["box_id"], "from": r["from_rack"],
                 "to": r["to_rack"], "time": r["moved_at"]} for r in rows]

    # ═══════════════════════════════════════════════════════════
    #  SCAN LOG
    # ═══════════════════════════════════════════════════════════
    def log_scan(self, qr_data: str, qr_type: str, action: str,
                 message: str = "", scanned_at: Optional[str] = None):
        """Record a raw scan event for audit trail."""
        if scanned_at is None:
            scanned_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.conn.execute(
            "INSERT INTO scan_log (qr_data, qr_type, action, message, scanned_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (qr_data, qr_type, action, message, scanned_at)
        )
        self.conn.commit()

    def get_scan_log(self, limit: int = 200) -> list:
        """Get recent scan log entries."""
        rows = self.conn.execute(
            "SELECT qr_data, qr_type, action, message, scanned_at "
            "FROM scan_log ORDER BY id DESC LIMIT ?",
            (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    # ═══════════════════════════════════════════════════════════
    #  SUMMARY / STATS
    # ═══════════════════════════════════════════════════════════
    def get_summary(self) -> dict:
        """Get a summary snapshot for dashboard display."""
        total_boxes = self.conn.execute("SELECT COUNT(*) FROM boxes").fetchone()[0]
        in_transit  = self.conn.execute(
            "SELECT COUNT(*) FROM boxes WHERE status = 'in_transit'"
        ).fetchone()[0]
        total_racks = self.conn.execute(
            "SELECT COUNT(DISTINCT rack_id) FROM racks"
        ).fetchone()[0]
        total_moves = self.conn.execute("SELECT COUNT(*) FROM movements").fetchone()[0]
        total_scans = self.conn.execute("SELECT COUNT(*) FROM scan_log").fetchone()[0]

        return {
            "total_boxes":    total_boxes,
            "boxes_in_transit": in_transit,
            "boxes_stored":   total_boxes - in_transit,
            "total_racks":    total_racks,
            "total_movements": total_moves,
            "total_scans":    total_scans,
        }

    # ═══════════════════════════════════════════════════════════
    #  PICKUP / DROP — Atomic Operations
    # ═══════════════════════════════════════════════════════════
    def pickup_box(self, box_id: str, from_rack: str):
        """Atomic: remove box from rack, mark in_transit."""
        cur = self.conn.cursor()
        try:
            # Ensure box record exists
            cur.execute(
                "INSERT OR IGNORE INTO boxes (box_id, location, status) VALUES (?, ?, ?)",
                (box_id, from_rack, "stored")
            )
            # Remove from rack
            cur.execute(
                "DELETE FROM racks WHERE rack_id = ? AND box_id = ?",
                (from_rack, box_id)
            )
            # Mark in transit
            cur.execute(
                "UPDATE boxes SET location = 'FORKLIFT', status = 'in_transit' WHERE box_id = ?",
                (box_id,)
            )
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    def drop_box(self, box_id: str, from_rack: str, to_rack: str,
                 moved_at: Optional[str] = None):
        """Atomic: add box to new rack, update location, record history."""
        if moved_at is None:
            moved_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cur = self.conn.cursor()
        try:
            # Add to destination rack
            cur.execute(
                "INSERT OR IGNORE INTO racks (rack_id, box_id) VALUES (?, ?)",
                (to_rack, box_id)
            )
            # Update box location
            cur.execute(
                "UPDATE boxes SET location = ?, status = 'stored' WHERE box_id = ?",
                (to_rack, box_id)
            )
            # Record movement
            cur.execute(
                "INSERT INTO movements (box_id, from_rack, to_rack, moved_at) VALUES (?, ?, ?, ?)",
                (box_id, from_rack, to_rack, moved_at)
            )
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    # ═══════════════════════════════════════════════════════════
    #  CLEANUP
    # ═══════════════════════════════════════════════════════════
    def close(self):
        """Close the database connection."""
        if self.conn:
            self.conn.close()
            self.conn = None
