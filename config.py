"""Einstellungen – alles über Umgebungsvariablen (Railway → Variables)."""
import os
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Berlin")

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
# Öffentliche HTTPS-Adresse der Mini-App, z. B. https://routenbot.up.railway.app
WEBAPP_URL = os.environ.get("WEBAPP_URL", "").rstrip("/")
# Kostenloser Key von openrouteservice.org (Adressen + Fahrzeiten)
ORS_API_KEY = os.environ.get("ORS_API_KEY", "")
PORT = int(os.environ.get("PORT", "8080"))
DB_PATH = os.environ.get("DB_PATH", "routenbot.db")

# Telegram-IDs der Monteure, die den Bot nutzen dürfen (Komma-getrennt).
# Leer = jeder darf. Die eigene ID zeigt der Bot bei /start an.
ALLOWED_USERS = {
    int(x) for x in os.environ.get("ALLOWED_USERS", "").replace(" ", "").split(",") if x
}

# Wie lange ein Auftrag vor Ort ungefähr dauert (Minuten)
SERVICE_MIN = int(os.environ.get("SERVICE_MIN", "60"))
# Frist, wenn im Auftrag keine Wartezeit steht (Stunden)
DEFAULT_WAIT_H = float(os.environ.get("DEFAULT_WAIT_H", "3"))
# Wie stark Verspätung gegenüber Fahrzeit zählt (1 Min zu spät = X Min Fahrt)
LATE_WEIGHT = float(os.environ.get("LATE_WEIGHT", "2"))
# Ab wie viel Restzeit gewarnt wird (Minuten)
WARN_MIN = int(os.environ.get("WARN_MIN", "60"))
# Ab dieser Strecke (km) gilt ein Auftrag als "weit weg"
FAR_KM = float(os.environ.get("FAR_KM", "60"))
