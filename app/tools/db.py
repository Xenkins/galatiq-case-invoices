from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Dict, List, Optional


DEFAULT_SEED = {
    "WidgetA": 15,
    "WidgetB": 10,
    "GadgetX": 5,
    "FakeItem": 0,
}


def ensure_inventory_db(db_path: str) -> None:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        cur = conn.cursor()
        cur.execute(
            "CREATE TABLE IF NOT EXISTS inventory (item TEXT PRIMARY KEY, stock INTEGER NOT NULL)"
        )
        cur.execute("SELECT COUNT(1) FROM inventory")
        count = cur.fetchone()[0]
        if count == 0:
            cur.executemany(
                "INSERT INTO inventory(item, stock) VALUES(?, ?)",
                list(DEFAULT_SEED.items()),
            )
        conn.commit()
    finally:
        conn.close()


def fetch_inventory(db_path: str) -> Dict[str, int]:
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute("SELECT item, stock FROM inventory")
        rows = cur.fetchall()
        return {str(item): int(stock) for item, stock in rows}
    finally:
        conn.close()


def get_stock(db_path: str, item: str) -> Optional[int]:
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute("SELECT stock FROM inventory WHERE item = ?", (item,))
        row = cur.fetchone()
        return int(row[0]) if row else None
    finally:
        conn.close()


def upsert_inventory_items(db_path: str, items: List[tuple[str, int]]) -> None:
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        cur.executemany(
            """
            INSERT INTO inventory(item, stock) VALUES(?, ?)
            ON CONFLICT(item) DO UPDATE SET stock=excluded.stock
            """,
            items,
        )
        conn.commit()
    finally:
        conn.close()
