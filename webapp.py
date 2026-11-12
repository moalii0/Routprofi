"""Webserver für die Mini-App: liefert die Seite und eine kleine API.
Jeder Aufruf wird mit Telegrams initData geprüft – nur der echte Monteur sieht seine Aufträge."""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from pathlib import Path
from urllib.parse import parse_qsl

from aiohttp import web

import db
import service
from config import BOT_TOKEN, ALLOWED_USERS

log = logging.getLogger(__name__)
STATIC = Path(__file__).parent / "static"


def check_init_data(init_data: str, max_age: int = 7 * 86400) -> dict | None:
    """Prüft die Signatur von Telegram (https://core.telegram.org/bots/webapps)."""
    if not init_data or not BOT_TOKEN:
        return None
    pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = pairs.pop("hash", None)
    if not received_hash:
        return None
    check_string = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    calc = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calc, received_hash):
        return None
    try:
        if time.time() - int(pairs.get("auth_date", "0")) > max_age:
            return None
        return json.loads(pairs.get("user", "{}"))
    except ValueError:
        return None


def _auth(request: web.Request) -> int:
    user = check_init_data(request.headers.get("X-Init-Data", ""))
    if not user or "id" not in user:
        raise web.HTTPUnauthorized(text="Bitte die Mini-App aus Telegram öffnen.")
    uid = int(user["id"])
    if ALLOWED_USERS and uid not in ALLOWED_USERS:
        raise web.HTTPForbidden(text="Nicht freigeschaltet.")
    if not db.get_user(uid):
        db.upsert_user(uid, uid, user.get("first_name", ""))
    return uid


async def index(request: web.Request):
    return web.FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})


async def api_state(request: web.Request):
    uid = _auth(request)
    return web.json_response(await service.build_state(uid))


async def api_action(request: web.Request):
    uid = _auth(request)
    try:
        body = await request.json()
    except ValueError:
        raise web.HTTPBadRequest(text="Ungültige Anfrage")
    action = body.get("action")
    oid = body.get("id")
    oid = int(oid) if isinstance(oid, (int, str)) and str(oid).isdigit() else None

    # Standort kann bei jeder Aktion mitgeschickt werden
    lat, lng = body.get("lat"), body.get("lng")
    if isinstance(lat, (int, float)) and isinstance(lng, (int, float)) and -90 <= lat <= 90 and -180 <= lng <= 180:
        db.set_location(uid, float(lat), float(lng))

    ok = True
    if action == "done":
        ok = service.mark(uid, oid, "fertig")
    elif action == "undo":
        ok = service.mark(uid, oid, "offen")
    elif action == "handoff":
        ok = service.mark(uid, oid, "abgegeben")
    elif action == "extend":
        minutes = body.get("minutes")
        ok = isinstance(minutes, int) and service.extend(uid, oid, minutes)
    elif action == "ack":
        ok = service.ack(uid, oid)
    elif action == "order":
        ids = body.get("ids") or []
        ok = service.set_order(uid, [int(i) for i in ids if str(i).isdigit()])
    elif action == "auto":
        ok = service.set_order(uid, [])
    elif action == "call":
        o = db.get_order(uid, oid) if oid else None
        if o and o["phone"]:
            # Telefonnummern im Chat sind antippbar – sicherer Weg zum Anrufen
            await request.app["bot"].send_message(uid, f"📞 {o['name']} ({o['city'] or o['plz']})\n{o['phone']}")
        else:
            ok = False
    elif action != "location":
        raise web.HTTPBadRequest(text="Unbekannte Aktion")

    state = await service.build_state(uid)
    state["ok"] = ok
    return web.json_response(state)


def make_app(bot) -> web.Application:
    app = web.Application()
    app["bot"] = bot
    app.router.add_get("/", index)
    app.router.add_get("/api/state", api_state)
    app.router.add_post("/api/action", api_action)
    app.router.add_get("/health", lambda r: web.Response(text="ok"))
    return app
