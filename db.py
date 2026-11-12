"""SQLite-Datenbank: Monteure, Aufträge, Adress-Cache."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from config import DB_PATH

_con: sqlite3.Connection | None = None


def con() -> sqlite3.Connection:
    global _con
    if _con is None:
        _con = sqlite3.connect(DB_PATH, check_same_thread=False)
        _con.row_factory = sqlite3.Row
        _con.execute("PRAGMA journal_mode=WAL")
        _init(_con)
    return _con


def _init(c: sqlite3.Connection) -> None:
    c.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            chat_id INTEGER,
            name TEXT,
            lat REAL, lng REAL, loc_time TEXT,
            manual TEXT DEFAULT '[]',
            created TEXT
        );
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'offen',      -- offen | fertig | abgegeben
            name TEXT, street TEXT, plz TEXT, city TEXT, phone TEXT,
            info TEXT, extra TEXT,
            wait_min INTEGER, call_ahead_min INTEGER DEFAULT 0,
            received_at TEXT NOT NULL,                 -- UTC ISO
            time_source TEXT,                          -- weitergeleitet | kopfzeile | empfangen
            extend_min INTEGER DEFAULT 0,
            lat REAL, lng REAL, geo_ok INTEGER DEFAULT 0,
            done_at TEXT,
            warned_low INTEGER DEFAULT 0,
            warned_over INTEGER DEFAULT 0,
            reminded INTEGER DEFAULT 0,
            ack INTEGER DEFAULT 0,
            raw TEXT,
            created TEXT
        );
        CREATE INDEX IF NOT EXISTS ix_orders_user ON orders(user_id, status);
        CREATE TABLE IF NOT EXISTS geocache (
            address TEXT PRIMARY KEY, lat REAL, lng REAL
        );
        """
    )
    c.commit()


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def parse_iso(s: str | None) -> datetime | None:
    return datetime.fromisoformat(s) if s else None


# ---------- users ----------

def upsert_user(user_id: int, chat_id: int, name: str) -> None:
    c = con()
    c.execute(
        """INSERT INTO users(user_id, chat_id, name, created) VALUES(?,?,?,?)
           ON CONFLICT(user_id) DO UPDATE SET chat_id=excluded.chat_id, name=excluded.name""",
        (user_id, chat_id, name, iso(now_utc())),
    )
    c.commit()


def get_user(user_id: int):
    return con().execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()


def all_users():
    return con().execute("SELECT * FROM users").fetchall()


def set_location(user_id: int, lat: float, lng: float) -> None:
    c = con()
    c.execute("UPDATE users SET lat=?, lng=?, loc_time=? WHERE user_id=?",
              (lat, lng, iso(now_utc()), user_id))
    c.commit()


def get_manual(user_id: int) -> list[int]:
    u = get_user(user_id)
    try:
        return json.loads(u["manual"] or "[]") if u else []
    except ValueError:
        return []


def set_manual(user_id: int, ids: list[int]) -> None:
    c = con()
    c.execute("UPDATE users SET manual=? WHERE user_id=?", (json.dumps(ids), user_id))
    c.commit()


# ---------- orders ----------

def find_duplicate(user_id: int, name: str, street: str, received_at: datetime):
    """Gleicher Kunde + Straße innerhalb von 24 Std. = schon drin."""
    rows = con().execute(
        "SELECT * FROM orders WHERE user_id=? AND lower(name)=lower(?) AND lower(street)=lower(?)",
        (user_id, name, street),
    ).fetchall()
    for r in rows:
        if abs((parse_iso(r["received_at"]) - received_at).total_seconds()) < 86400:
            return r
    return None


def add_order(user_id: int, p, received_at: datetime, time_source: str) -> int:
    c = con()
    cur = c.execute(
        """INSERT INTO orders(user_id, name, street, plz, city, phone, info, extra, wait_min,
               call_ahead_min, received_at, time_source, raw, created)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (user_id, p.name, p.street, p.plz, p.city, p.phone, p.info, p.extra, p.wait_min,
         p.call_ahead_min, iso(received_at), time_source, p.raw, iso(now_utc())),
    )
    c.commit()
    return cur.lastrowid


def set_geo(order_id: int, lat: float | None, lng: float | None) -> None:
    c = con()
    c.execute("UPDATE orders SET lat=?, lng=?, geo_ok=? WHERE id=?",
              (lat, lng, 1 if lat is not None else 0, order_id))
    c.commit()


def get_order(user_id: int, order_id: int):
    return con().execute("SELECT * FROM orders WHERE id=? AND user_id=?",
                         (order_id, user_id)).fetchone()


def get_order_by_id(order_id: int):
    return con().execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()


def day_start(now: datetime | None = None) -> datetime:
    """Beginn des aktuellen Arbeitstags (RESET_HOUR Uhr, deutsche Zeit)."""
    from config import TZ, RESET_HOUR
    from datetime import timedelta
    local = (now or now_utc()).astimezone(TZ)
    start = local.replace(hour=RESET_HOUR, minute=0, second=0, microsecond=0)
    if local < start:
        start -= timedelta(days=1)
    return start.astimezone(timezone.utc)


def open_orders(user_id: int):
    """Nur offene Aufträge vom aktuellen Arbeitstag – jeden Tag frische Route."""
    return con().execute(
        "SELECT * FROM orders WHERE user_id=? AND status='offen' AND received_at>=? ORDER BY received_at",
        (user_id, iso(day_start())),
    ).fetchall()


def closed_since(user_id: int, since: datetime):
    return con().execute(
        """SELECT * FROM orders WHERE user_id=? AND status!='offen' AND done_at>=?
           ORDER BY done_at DESC""",
        (user_id, iso(since)),
    ).fetchall()


def update_order(order_id: int, **fields) -> None:
    if not fields:
        return
    keys = ", ".join(f"{k}=?" for k in fields)
    c = con()
    c.execute(f"UPDATE orders SET {keys} WHERE id=?", (*fields.values(), order_id))
    c.commit()


def geocache_get(address: str):
    r = con().execute("SELECT lat, lng FROM geocache WHERE address=?", (address.lower(),)).fetchone()
    return (r["lat"], r["lng"]) if r else None


def geocache_put(address: str, lat: float, lng: float) -> None:
    c = con()
    c.execute("INSERT OR REPLACE INTO geocache VALUES(?,?,?)", (address.lower(), lat, lng))
    c.commit()
