# Routen-Bot

Eigener Telegram-Bot für Monteure. Er hat **nichts mit den Gruppen oder dem Umsatz zu tun**. Alles läuft privat zwischen Monteur und Bot.

## Was der Bot macht

- Der Monteur **leitet einen Auftrag aus der Gruppe an den Bot weiter**. Der Bot liest Name, Adresse, Telefon, Info, Wartezeit und „30 Min. vorher anrufen“ aus.
- **Countdown ab Eingang:** Die Frist kommt aus „Wartezeit“ (2–3 Stunden → 3 Std.). Steht nichts drin, gelten 3 Std. Grün = über 2 Std. übrig, Gelb = unter 2 Std., Rot = unter 1 Std. oder Frist vorbei.
- **Zeit geben:** Mit den Buttons „+30 Min“ und „+1 Std“ verlängert der Monteur, und „Passt, Kunde weiß Bescheid“ schaltet die Warnungen für diesen Kunden ab.
- **Beste Reihenfolge:** Der Bot rechnet vom aktuellen Standort aus und achtet dabei auf kurze Wege und auf die Restzeit. Wer kaum noch Zeit hat, kommt vor, sonst wird der Reihe nach gefahren (Altona → Dammtor → Barmbek → Wandsbek → Jenfeld, nicht hin und her).
- **Eigene Reihenfolge:** Der Monteur kann „Als nächstes“ oder „Nach hinten“ wählen. Der Bot hält sich dann daran, sagt aber, wenn die automatische Reihenfolge deutlich besser wäre. Neue Aufträge sortiert er selbst an der besten Stelle ein, der gewählte nächste Auftrag bleibt aber vorne.
- **Push-Nachrichten:**
  - Restzeit unter 1 Std.
  - Frist vorbei
  - Anruf-Erinnerung bei „30 Min. vorher anrufen“, mit Nummer zum Antippen und Navi-Buttons
- **Weit weg:** Bei einer Strecke ab 60 km kommt der Hinweis „lieber abgeben?“.
- **Mini-App:** Alle Aufträge mit Live-Countdown. Tippt man einen an, sieht man Adresse, Info, wann er reinkam und die Frist. Dazu gibt es Buttons für Anrufen, Google Maps, Apple Karten, +30 Min, +1 Std, Abgeben und Fertig sowie die ganze Route in Google Maps.

## Was der Monteur macht

1. Den Bot einmal privat öffnen und auf **Start** tippen.
2. Den Auftrag in der Gruppe lange drücken → **Weiterleiten** → Routen-Bot. Mehrere auf einmal markieren geht auch.
   Wichtig: **weiterleiten, nicht kopieren**, sonst fehlt die Uhrzeit. Kopierter Text mit der Kopfzeile `Max, [6 Nov 2026 um 14:05]` funktioniert aber auch.
3. **📍 Standort senden** oder in der Mini-App **Standort neu** tippen.
4. **🗺 Route öffnen**, der Button sitzt auch unten links neben dem Eingabefeld.

## Einrichten

### 1. Bot anlegen
In Telegram **@BotFather** öffnen und `/newbot` schicken, dann Namen vergeben, zum Beispiel „Route Monteur“. Den **Token** kopieren.

### 2. Kostenlosen Karten-Key holen
Auf https://openrouteservice.org registrieren → Dashboard → Token erstellen (Standard/kostenlos).
Ohne Key läuft der Bot auch, die Fahrzeiten sind dann aber nur geschätzt (Luftlinie).

### 3. Auf Railway hochladen
1. Den Ordner als neues GitHub-Repo hochladen (oder per Railway CLI `railway up`).
2. In Railway: **New Project → Deploy from GitHub repo**.
3. **Settings → Networking → Generate Domain**. Die Adresse ist dann z. B. `https://routenbot-production.up.railway.app`.
4. **Volume** anlegen, damit die Aufträge einen Neustart überleben: Mount path `/data`.
5. **Variables** setzen:

| Variable | Wert |
|---|---|
| `BOT_TOKEN` | Token von BotFather |
| `WEBAPP_URL` | die Domain aus Schritt 3 (mit https://) |
| `ORS_API_KEY` | Key von openrouteservice |
| `DB_PATH` | `/data/routenbot.db` |
| `ALLOWED_USERS` | Telegram-IDs der Monteure, mit Komma getrennt |

Optional:
- `SERVICE_MIN`: Dauer pro Auftrag in Minuten (Standard 60)
- `DEFAULT_WAIT_H`: Frist ohne Angabe im Auftrag (Standard 3)
- `WARN_MIN`: ab wie viel Restzeit gewarnt wird (Standard 60)
- `FAR_KM`: ab wann ein Auftrag als „weit weg“ gilt (Standard 60)
- `LATE_WEIGHT`: wie stark Verspätung gegenüber Fahrzeit zählt (Standard 2, höher = Restzeit wichtiger)

**Telegram-ID herausfinden:** Der Monteur schickt dem Bot `/id`. Solange `ALLOWED_USERS` leer ist, darf jeder den Bot benutzen. Trag die IDs also ein, sobald alle drin sind.

### 4. Testen
Einen alten Auftrag an den Bot weiterleiten, dann den Standort senden und auf „Route öffnen“ tippen.

## Dateien

- `bot.py`: Start, Telegram-Chat, Erinnerungen
- `webapp.py`: Webserver und API für die Mini-App (mit Telegram-Signaturprüfung)
- `static/index.html`: die Mini-App
- `service.py`: Route, Countdown, Warnungen
- `planner.py`: Berechnung der Reihenfolge
- `parser.py`: liest die Aufträge aus
- `geo.py`: Adressen, Fahrzeiten, Google- und Apple-Links
- `db.py`: SQLite-Datenbank
- `tests/test_logik.py`: Tests (`python tests/test_logik.py`)

## Grenzen

- Stau kennt der Bot nicht (das kann später Google). Die Dauer pro Auftrag ist geschätzt (`SERVICE_MIN`).
- Google Maps nimmt höchstens 9 Zwischenstopps in einem Link.
- Apple Karten öffnet immer nur den nächsten Auftrag. Bei jedem einzelnen Auftrag gibt es aber eigene Navi-Buttons.
