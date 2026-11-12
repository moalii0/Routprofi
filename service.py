"""Kernlogik: Aufträge anlegen, Route berechnen, Warnungen/Erinnerungen bestimmen.
Wird vom Bot (Chat) und von der Mini-App (API) gleichermaßen benutzt."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import db
import geo
from config import TZ, SERVICE_MIN, LATE_WEIGHT, WARN_MIN, FAR_KM
from planner import Stop, plan_route, best_insert

log = logging.getLogger(__name__)

TIME_SOURCE_LABEL = {
    "kopfzeile": "Uhrzeit aus dem Auftrag",
    "weitergeleitet": "Uhrzeit vom Weiterleiten",
    "empfangen": "Uhrzeit geschätzt (kopiert, nicht weitergeleitet)",
}

_matrix_cache: dict = {}
LOC_MAX_AGE_H = 12


def hm(minutes: float) -> str:
    """125 -> '2:05', -20 -> '-0:20'"""
    sign = "-" if minutes < 0 else ""
    m = int(abs(round(minutes)))
    return f"{sign}{m // 60}:{m % 60:02d}"


def local_hm(dt: datetime) -> str:
    return dt.astimezone(TZ).strftime("%H:%M")


def deadline_of(o) -> datetime:
    return db.parse_iso(o["received_at"]) + timedelta(minutes=(o["wait_min"] or 180) + (o["extend_min"] or 0))


def place(o) -> str:
    return o["city"] or o["plz"] or ""


# ---------- Aufträge anlegen ----------

async def geocode_order(order_id: int, o) -> bool:
    address = ", ".join(x for x in [o.street, f"{o.plz} {o.city}".strip()] if x)
    pt = db.geocache_get(address)
    if not pt:
        pt = await geo.geocode(address)
        if not pt and o.plz:  # Straße unbekannt -> wenigstens PLZ/Ort
            pt = await geo.geocode(f"{o.plz} {o.city}".strip())
        if pt:
            db.geocache_put(address, *pt)
    db.set_geo(order_id, *(pt if pt else (None, None)))
    return bool(pt)


async def add_orders(user_id: int, parsed: list, forward_date: datetime | None, msg_date: datetime):
    """Gibt Liste von (order_id, 'neu'|'doppelt', geo_ok) zurück."""
    out = []
    for p in parsed:
        if p.header_time:
            received, src = p.header_time, "kopfzeile"
        elif forward_date:
            received, src = forward_date, "weitergeleitet"
        else:
            received, src = msg_date, "empfangen"
        received = received.astimezone(timezone.utc)
        dup = db.find_duplicate(user_id, p.name, p.street, received)
        if dup:
            out.append((dup["id"], "doppelt", bool(dup["geo_ok"])))
            continue
        oid = db.add_order(user_id, p, received, src)
        ok = await geocode_order(oid, p)
        out.append((oid, "neu", ok))
    return out


# ---------- Route berechnen ----------

async def _matrix(points):
    key = tuple((round(p[0], 3), round(p[1], 3)) for p in points)
    if key in _matrix_cache:
        return _matrix_cache[key]
    res = await geo.route_matrix(points)
    if len(_matrix_cache) > 300:
        _matrix_cache.clear()
    _matrix_cache[key] = res
    return res


async def build_state(user_id: int) -> dict:
    now = db.now_utc()
    user = db.get_user(user_id)
    orders = db.open_orders(user_id)
    by_id = {o["id"]: o for o in orders}

    start = None
    loc_age = None
    if user and user["lat"] is not None and user["loc_time"]:
        loc_age = (now - db.parse_iso(user["loc_time"])).total_seconds() / 60
        if loc_age <= LOC_MAX_AGE_H * 60:
            start = (user["lat"], user["lng"])

    geo_orders = [o for o in orders if o["geo_ok"]]
    no_geo = [o for o in orders if not o["geo_ok"]]

    stops = [Stop(o["id"], (deadline_of(o) - now).total_seconds() / 60, SERVICE_MIN) for o in geo_orders]
    pts = [(o["lat"], o["lng"]) for o in geo_orders]
    real = False
    if pts:
        if start:
            dur, dist, real = await _matrix([start] + pts)
        else:
            # ohne Standort: Startpunkt mit 0 Minuten zu allen -> freie Wahl des ersten Auftrags
            d0, k0, real = await _matrix(pts) if len(pts) > 1 else ([[0.0]], [[0.0]], False)
            n = len(pts)
            dur = [[0.0] * (n + 1)] + [[0.0] + d0[i] for i in range(n)]
            dist = [[0.0] * (n + 1)] + [[0.0] + k0[i] for i in range(n)]
    else:
        dur = dist = [[0.0]]

    # Eigene Reihenfolge des Monteurs
    manual = [i for i in db.get_manual(user_id) if i in by_id and by_id[i]["geo_ok"]]
    if manual:
        for s in stops:  # neue Aufträge selbstständig an der besten Stelle einfügen
            if s.id not in manual:
                manual = best_insert(manual, s.id, stops, dur, LATE_WEIGHT)
        db.set_manual(user_id, manual)

    hint = ""
    if stops:
        plan = plan_route(stops, dur, LATE_WEIGHT, pinned=manual)
        if manual:
            auto = plan_route(stops, dur, LATE_WEIGHT)
            if auto.order != plan.order:
                parts = []
                if plan.drive_min - auto.drive_min >= 5:
                    parts.append(f"{round(plan.drive_min - auto.drive_min)} Min weniger Fahrt")
                if plan.late_min - auto.late_min >= 5:
                    parts.append(f"{round(plan.late_min - auto.late_min)} Min weniger Verspätung")
                if parts:
                    hint = "Automatisch wäre besser: " + ", ".join(parts) + "."
    else:
        plan = None

    idx = {s.id: k + 1 for k, s in enumerate(stops)}
    out_stops = []
    warnings = []
    prev = 0
    route_pts = []
    for pos, leg in enumerate(plan.legs if plan else [], start=1):
        o = by_id[leg.stop_id]
        km = dist[prev][idx[o["id"]]]
        prev = idx[o["id"]]
        dl = deadline_of(o)
        far = km >= FAR_KM
        d = _stop_dict(o, pos, now, dl)
        d.update(
            drive_min=round(leg.drive_min) if start or pos > 1 else None,
            km=round(km, 1) if start or pos > 1 else None,
            eta_hm=local_hm(now + timedelta(minutes=leg.arrive_min)) if start else None,
            arrive_min=round(leg.arrive_min) if start else None,
            late_min=round(leg.late_min) if start else 0,
            far=far,
        )
        if start and leg.late_min >= 10 and not o["ack"]:
            warnings.append(f"{o['name']} ({place(o)}): kommt ca. {hm(leg.late_min)} nach der Frist an.")
        if far:
            warnings.append(f"{o['name']} ({place(o)}): {round(km)} km Strecke – lieber abgeben?")
        out_stops.append(d)
        route_pts.append((o["lat"], o["lng"]))
    for o in no_geo:
        d = _stop_dict(o, None, now, deadline_of(o))
        d.update(drive_min=None, km=None, eta_hm=None, arrive_min=None, late_min=0, far=False)
        warnings.append(f"{o['name']}: Adresse nicht gefunden – bitte prüfen.")
        out_stops.append(d)

    if orders and not start:
        warnings.insert(0, "Standort fehlt – tipp auf „Standort neu“, dann wird die Route genau.")

    closed = [
        {"id": o["id"], "name": o["name"], "city": place(o), "status": o["status"],
         "done_hm": local_hm(db.parse_iso(o["done_at"]))}
        for o in db.closed_since(user_id, db.day_start(now))
    ]

    first = out_stops[0] if out_stops else None
    live_off = bool(user and user["live_off"])
    live_until = db.parse_iso(user["live_until"]) if user and user["live_until"] else None
    # Live gilt als aktiv, wenn geteilt, nicht abgelaufen und letztes Update < 15 Min
    live_active = bool(live_until and live_until > now and loc_age is not None and loc_age <= 15 and not live_off)
    return {
        "now": db.iso(now),
        "live": {"active": live_active, "off": live_off, "shared": bool(live_until and live_until > now)},
        "has_location": bool(start),
        "loc_age_min": round(loc_age) if loc_age is not None else None,
        "route_real": real,
        "mode": "manual" if manual else "auto",
        "hint": hint,
        "warnings": warnings,
        "google_route": geo.google_route(start, route_pts),
        "apple_next": geo.apple_nav((first["lat"], first["lng"]) if first and first["lat"] else None,
                                    first["address"] if first else "") if first else "",
        "stops": out_stops,
        "closed": closed,
        "counts": {"open": len(orders), "done": sum(1 for c in closed if c["status"] == "fertig")},
    }


def _stop_dict(o, pos, now, dl) -> dict:
    received = db.parse_iso(o["received_at"])
    address = ", ".join(x for x in [o["street"], f"{o['plz']} {o['city']}".strip()] if x)
    pt = (o["lat"], o["lng"]) if o["geo_ok"] else None
    return {
        "id": o["id"], "pos": pos, "name": o["name"], "street": o["street"], "plz": o["plz"],
        "city": o["city"], "address": address, "phone": o["phone"], "info": o["info"],
        "extra": o["extra"], "lat": o["lat"], "lng": o["lng"], "geo_ok": bool(o["geo_ok"]),
        "received_at": db.iso(received), "received_hm": local_hm(received),
        "time_source": o["time_source"], "time_source_label": TIME_SOURCE_LABEL.get(o["time_source"], ""),
        "deadline": db.iso(dl), "deadline_hm": local_hm(dl),
        "remaining_min": round((dl - now).total_seconds() / 60),
        "waiting_min": round((now - received).total_seconds() / 60),
        "wait_min": o["wait_min"], "extend_min": o["extend_min"],
        "call_ahead_min": o["call_ahead_min"], "ack": bool(o["ack"]),
        "google": geo.google_nav(pt, address), "apple": geo.apple_nav(pt, address),
    }


# ---------- Aktionen ----------

def _open_ids(user_id):
    return {o["id"] for o in db.open_orders(user_id)}


def mark(user_id: int, order_id: int, status: str) -> bool:
    o = db.get_order(user_id, order_id)
    if not o:
        return False
    if status == "offen":
        db.update_order(order_id, status="offen", done_at=None)
    else:
        db.update_order(order_id, status=status, done_at=db.iso(db.now_utc()))
        db.set_manual(user_id, [i for i in db.get_manual(user_id) if i != order_id])
    return True


def extend(user_id: int, order_id: int, minutes: int) -> bool:
    o = db.get_order(user_id, order_id)
    if not o or minutes <= 0 or minutes > 24 * 60:
        return False
    new_ext = (o["extend_min"] or 0) + minutes
    fields = {"extend_min": new_ext}
    remaining = (deadline_of(o) + timedelta(minutes=minutes) - db.now_utc()).total_seconds() / 60
    if remaining >= WARN_MIN:
        fields["warned_low"] = 0
    if remaining >= 0:
        fields["warned_over"] = 0
    db.update_order(order_id, **fields)
    return True


def ack(user_id: int, order_id: int) -> bool:
    if not db.get_order(user_id, order_id):
        return False
    db.update_order(order_id, ack=1, warned_low=1, warned_over=1)
    return True


def set_order(user_id: int, ids: list[int]) -> bool:
    open_ids = _open_ids(user_id)
    clean = []
    for i in ids:
        if isinstance(i, int) and i in open_ids and i not in clean:
            clean.append(i)
    db.set_manual(user_id, clean)
    return True


# ---------- Benachrichtigungen ----------

def due_notifications(state: dict) -> list[dict]:
    """Was soll jetzt per Chat rausgehen? (Warnung Restzeit, Frist vorbei, Anruf-Erinnerung)"""
    out = []
    for s in state["stops"]:
        o = db.get_order_by_id(s["id"])
        if not o or o["status"] != "offen":
            continue
        rem = s["remaining_min"]
        if not o["ack"]:
            if rem < 0 and not o["warned_over"]:
                out.append({"kind": "over", "stop": s})
            elif 0 <= rem < WARN_MIN and not o["warned_low"]:
                out.append({"kind": "low", "stop": s})
        if (s["call_ahead_min"] and not o["reminded"] and s["arrive_min"] is not None
                and s["arrive_min"] <= s["call_ahead_min"] + 5):
            out.append({"kind": "call", "stop": s})
    return out


def mark_notified(order_id: int, kind: str) -> None:
    field = {"over": "warned_over", "low": "warned_low", "call": "reminded"}[kind]
    fields = {field: 1}
    if kind == "over":
        fields["warned_low"] = 1
    db.update_order(order_id, **fields)


# ---------- Text für den Chat ----------

NUMS = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]


def clock_text(rem: float) -> str:
    icon = "🟢" if rem >= 120 else "🟡" if rem >= 60 else "🔴"
    return f"{icon} noch {hm(rem)}" if rem >= 0 else f"🔴 {hm(-rem)} drüber"


def short_name(name: str) -> str:
    """'Frau Broverdan Gaststätte' -> 'Broverdan Gaststätte'"""
    parts = name.split()
    if len(parts) > 1 and parts[0].lower() in ("frau", "herr", "fr.", "hr.", "firma"):
        return " ".join(parts[1:])
    return name


def format_list(state: dict) -> str:
    """Kurze Übersicht für den Chat – alles Weitere steht in der Mini-App."""
    if not state["stops"]:
        return "Keine offenen Aufträge 👍"
    n = len(state["stops"])
    head = f"🚐 {n} offen" + (" · eigene Reihenfolge" if state["mode"] == "manual" else "")
    lines = [head, ""]
    for s in state["stops"]:
        nr = NUMS[s["pos"] - 1] if s["pos"] and s["pos"] <= 10 else "❔"
        flag = " ⚠️" if (s["far"] or s["late_min"] >= 10 or not s["geo_ok"]) and not s["ack"] else ""
        call = " 📞" if s["call_ahead_min"] else ""
        lines.append(f"{nr} {short_name(s['name'])} · {s['city'] or s['plz']}{call}{flag}")
        info = [clock_text(s["remaining_min"])]
        if s["eta_hm"]:
            info.append(f"an {s['eta_hm']}")
        if s["km"] is not None:
            info.append(f"{round(s['km'])} km")
        lines.append("      " + "  ·  ".join(info))
    if not state["has_location"]:
        lines += ["", "📍 Standort fehlt"]
    return "\n".join(lines)
