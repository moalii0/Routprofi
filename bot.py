"""Routen-Bot – startet den Telegram-Bot und den Webserver für die Mini-App.

Der Monteur schreibt nur privat mit dem Bot:
  - Auftrag aus der Gruppe weiterleiten  → wird eingelesen, Countdown läuft
  - 📍 Standort senden                     → Route wird neu berechnet
  - 🗺 Route öffnen                        → Mini-App mit allen Aufträgen
"""
from __future__ import annotations

import asyncio
import logging
import re

from aiohttp import web
from telegram import (
    InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, MenuButtonWebApp,
    ReplyKeyboardMarkup, Update, WebAppInfo,
)
from telegram.ext import (
    Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters,
)

import db
import service
import webapp
from config import ALLOWED_USERS, BOT_TOKEN, PORT, WEBAPP_URL
from parser import parse_orders

logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("routenbot")

PRIVATE = filters.ChatType.PRIVATE


def allowed(uid: int) -> bool:
    return not ALLOWED_USERS or uid in ALLOWED_USERS


def tel_intl(phone: str) -> str:
    """'0174 9901892' -> '+491749901892' (damit Telegram die Nummer antippbar macht)."""
    p = re.sub(r"[^\d+]", "", phone or "")
    if p.startswith("00"):
        p = "+" + p[2:]
    elif p.startswith("0"):
        p = "+49" + p[1:]
    return p


# ---------- Tastaturen ----------

def main_keyboard() -> ReplyKeyboardMarkup:
    row = [KeyboardButton("📍 Standort senden", request_location=True)]
    if WEBAPP_URL:
        row.append(KeyboardButton("🗺 Route öffnen", web_app=WebAppInfo(WEBAPP_URL)))
    return ReplyKeyboardMarkup([row, [KeyboardButton("📋 Liste")]], resize_keyboard=True)


def list_markup(state: dict) -> InlineKeyboardMarkup | None:
    rows = []
    for s in state["stops"][:8]:
        n = s["pos"] or "?"
        rows.append([
            InlineKeyboardButton(f"✅ {n} fertig", callback_data=f"d:{s['id']}"),
            InlineKeyboardButton("+30 Min", callback_data=f"e30:{s['id']}"),
            InlineKeyboardButton("+1 Std", callback_data=f"e60:{s['id']}"),
        ])
    nav = []
    if state["google_route"]:
        nav.append(InlineKeyboardButton("Google Maps", url=state["google_route"]))
    if state["apple_next"]:
        nav.append(InlineKeyboardButton("Apple Karten", url=state["apple_next"]))
    if nav:
        rows.append(nav)
    if WEBAPP_URL:
        rows.append([InlineKeyboardButton("🗺 Route öffnen", web_app=WebAppInfo(WEBAPP_URL))])
    return InlineKeyboardMarkup(rows) if rows else None


def time_markup(oid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("+30 Min", callback_data=f"e30:{oid}"),
         InlineKeyboardButton("+1 Std", callback_data=f"e60:{oid}")],
        [InlineKeyboardButton("👍 Passt, Kunde weiß Bescheid", callback_data=f"ack:{oid}")],
    ])


# ---------- Befehle ----------

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    db.upsert_user(u.id, update.effective_chat.id, u.first_name or "")
    if not allowed(u.id):
        await update.message.reply_text(
            f"Hallo {u.first_name}! Du bist noch nicht freigeschaltet.\n"
            f"Deine ID: {u.id}\nSchick die ID an die Zentrale."
        )
        return
    await update.message.reply_text(
        f"Hallo {u.first_name}! 👋\n\n"
        "So geht's:\n"
        "1️⃣ Auftrag in der Gruppe lange drücken → Weiterleiten → an mich\n"
        "   (mehrere auf einmal markieren geht auch)\n"
        "2️⃣ 📍 Standort senden – dann rechne ich die beste Route\n"
        "3️⃣ 🗺 Route öffnen – alle Aufträge mit Countdown, Navi, Anrufen, Fertig\n\n"
        "Wichtig: Weiterleiten, nicht kopieren – sonst fehlt mir die Uhrzeit.\n"
        "Ich melde mich, wenn die Zeit knapp wird oder du vorher anrufen musst.",
        reply_markup=main_keyboard(),
    )


async def cmd_id(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"Deine Telegram-ID: {update.effective_user.id}")


async def show_list(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not allowed(uid):
        return
    db.upsert_user(uid, update.effective_chat.id, update.effective_user.first_name or "")
    state = await service.build_state(uid)
    await update.effective_message.reply_text(service.format_list(state), reply_markup=list_markup(state))


async def on_location(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    uid = update.effective_user.id
    if not allowed(uid) or not msg.location:
        return
    db.upsert_user(uid, update.effective_chat.id, update.effective_user.first_name or "")
    db.set_location(uid, msg.location.latitude, msg.location.longitude)
    if update.edited_message:  # Live-Standort: nur still aktualisieren
        return
    state = await service.build_state(uid)
    await msg.reply_text("📍 Standort da, Route neu berechnet.\n\n" + service.format_list(state),
                         reply_markup=list_markup(state))


async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    uid = update.effective_user.id
    if not allowed(uid):
        await msg.reply_text(f"Nicht freigeschaltet. Deine ID: {uid}")
        return
    text = msg.text or msg.caption or ""
    if text.strip() == "📋 Liste":
        return await show_list(update, ctx)

    parsed = parse_orders(text)
    if not parsed:
        await msg.reply_text(
            "Das sieht nicht wie ein Auftrag aus 🤔\n"
            "Leite mir den Auftrag aus der Gruppe einfach weiter.",
            reply_markup=main_keyboard(),
        )
        return
    db.upsert_user(uid, update.effective_chat.id, update.effective_user.first_name or "")
    fwd = msg.forward_origin.date if msg.forward_origin else None
    results = await service.add_orders(uid, parsed, fwd, msg.date)
    state = await service.build_state(uid)
    by_id = {s["id"]: s for s in state["stops"]}

    lines = []
    for oid, status, geo_ok in results:
        s = by_id.get(oid)
        if status == "doppelt":
            name = s["name"] if s else "Auftrag"
            lines.append(f"↩️ {name} ist schon drin.")
            continue
        if not s:
            continue
        rem = s["remaining_min"]
        clock = f"noch {service.hm(rem)}" if rem >= 0 else f"schon {service.hm(-rem)} drüber"
        line = (f"✅ {s['name']} – {s['city'] or s['plz']}\n"
                f"   rein {s['received_hm']} · Frist {s['deadline_hm']} · {clock}")
        if s["pos"]:
            line += f"\n   Platz {s['pos']} von {len(state['stops'])}"
        if s["time_source"] == "empfangen":
            line += "\n   ⚠️ Uhrzeit geschätzt – bitte weiterleiten statt kopieren"
        if not geo_ok:
            line += "\n   ⚠️ Adresse nicht gefunden – bitte prüfen"
        lines.append(line)
    if not state["has_location"]:
        lines.append("\n📍 Schick mir noch deinen Standort, dann wird die Route genau.")
    await msg.reply_text("\n".join(lines), reply_markup=list_markup(state))


async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    uid = q.from_user.id
    if not allowed(uid):
        await q.answer("Nicht freigeschaltet")
        return
    kind, _, raw_id = (q.data or "").partition(":")
    if not raw_id.isdigit():
        await q.answer()
        return
    oid = int(raw_id)
    o = db.get_order(uid, oid)
    if not o:
        await q.answer("Auftrag nicht gefunden")
        return

    if kind == "d":
        service.mark(uid, oid, "fertig")
        note = f"✅ {o['name']} erledigt"
    elif kind == "e30":
        service.extend(uid, oid, 30)
        note = f"⏱ {o['name']}: +30 Min"
    elif kind == "e60":
        service.extend(uid, oid, 60)
        note = f"⏱ {o['name']}: +1 Std"
    elif kind == "ack":
        service.ack(uid, oid)
        note = f"👍 {o['name']}: alles klar, keine Warnung mehr"
    else:
        await q.answer()
        return
    await q.answer(note)

    state = await service.build_state(uid)
    text = getattr(q.message, "text", "") or ""
    try:
        if text.startswith("🚐") or text.startswith("📍") or text.startswith("✅"):
            await q.edit_message_text(service.format_list(state), reply_markup=list_markup(state))
        else:  # Warnung/Erinnerung: nur abhaken
            await q.edit_message_text(text + "\n\n" + note)
    except Exception as e:  # noqa: BLE001 – "message is not modified" o. ä.
        log.debug("Edit fehlgeschlagen: %s", e)


# ---------- Erinnerungen & Warnungen ----------

async def notify_loop(app: Application):
    while True:
        try:
            for u in db.all_users():
                if not allowed(u["user_id"]) or not db.open_orders(u["user_id"]):
                    continue
                state = await service.build_state(u["user_id"])
                for n in service.due_notifications(state):
                    s = n["stop"]
                    where = s["city"] or s["plz"]
                    if n["kind"] == "low":
                        text = (f"⏰ {s['name']} ({where}): nur noch {service.hm(s['remaining_min'])} "
                                f"bis zur Frist ({s['deadline_hm']}).\nZeit geben oder Bescheid sagen?")
                        markup = time_markup(s["id"])
                    elif n["kind"] == "over":
                        text = (f"🔴 {s['name']} ({where}): Frist ist seit {service.hm(-s['remaining_min'])} "
                                f"vorbei.\nKunde anrufen und Zeit geben?")
                        if s["phone"]:
                            text += f"\nTel: {tel_intl(s['phone'])}"
                        markup = time_markup(s["id"])
                    else:  # Anruf-Erinnerung
                        text = (f"📞 Jetzt anrufen: {s['name']} ({where})\n"
                                f"Du bist in ca. {s['arrive_min']} Min da.\n"
                                f"Tel: {tel_intl(s['phone'])}")
                        markup = InlineKeyboardMarkup([[
                            InlineKeyboardButton("Google Maps", url=s["google"]),
                            InlineKeyboardButton("Apple Karten", url=s["apple"]),
                        ]])
                    try:
                        await app.bot.send_message(u["chat_id"], text, reply_markup=markup)
                    except Exception as e:  # noqa: BLE001
                        log.warning("Senden an %s fehlgeschlagen: %s", u["user_id"], e)
                    service.mark_notified(s["id"], n["kind"])
        except Exception:  # noqa: BLE001
            log.exception("Fehler im Erinnerungs-Loop")
        await asyncio.sleep(60)


# ---------- Start ----------

async def run():
    if not BOT_TOKEN:
        raise SystemExit("BOT_TOKEN fehlt (Railway → Variables).")
    db.con()
    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start, filters=PRIVATE))
    application.add_handler(CommandHandler("id", cmd_id, filters=PRIVATE))
    application.add_handler(CommandHandler(["liste", "route"], show_list, filters=PRIVATE))
    application.add_handler(MessageHandler(PRIVATE & filters.LOCATION, on_location))
    application.add_handler(MessageHandler(PRIVATE & (filters.TEXT | filters.CAPTION) & ~filters.COMMAND
                                           & ~filters.UpdateType.EDITED_MESSAGE, on_text))
    application.add_handler(CallbackQueryHandler(on_callback))

    runner = web.AppRunner(webapp.make_app(application.bot))
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    log.info("Webserver läuft auf Port %s", PORT)

    async with application:
        if WEBAPP_URL:
            await application.bot.set_chat_menu_button(
                menu_button=MenuButtonWebApp("Route", WebAppInfo(WEBAPP_URL)))
        await application.start()
        await application.updater.start_polling(allowed_updates=Update.ALL_TYPES)
        task = asyncio.create_task(notify_loop(application))
        log.info("Routen-Bot läuft")
        try:
            await asyncio.Event().wait()
        finally:
            task.cancel()
            await application.updater.stop()
            await application.stop()
            await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(run())
