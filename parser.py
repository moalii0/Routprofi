"""Liest Aufträge aus dem Text, den Max in die Gruppe schickt.

Funktioniert mit
- einer weitergeleiteten Nachricht (ein Auftrag),
- kopiertem Text mit mehreren Aufträgen hintereinander,
  inkl. Kopfzeile wie "Max, [6 Nov 2026 um 14:05]" (dann wird diese Uhrzeit genommen).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

from config import TZ, DEFAULT_WAIT_H

MONTHS = {
    "jan": 1, "januar": 1, "feb": 2, "februar": 2, "mär": 3, "mar": 3, "märz": 3, "maerz": 3,
    "apr": 4, "april": 4, "mai": 5, "may": 5, "jun": 6, "juni": 6, "jul": 7, "juli": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9, "okt": 10, "oct": 10,
    "oktober": 10, "nov": 11, "november": 11, "dez": 12, "dec": 12, "dezember": 12,
}

# "[6 Nov 2026 um 14:05]" oder "[06.11.2026 14:05]"
HEADER_RE = re.compile(
    r"\[(\d{1,2})\.?\s*([A-Za-zÄÖÜäöü]+\.?|\d{1,2}\.)\s*(\d{4}),?\s*(?:um\s+)?(\d{1,2}):(\d{2})\]"
)

KEYS = {
    "auftragseingang": "eingang",
    "name": "name",
    "kunde": "name",
    "straße": "street", "strasse": "street", "str": "street", "adresse": "street",
    "plz": "plz", "ort": "city",
    "telefon": "phone", "tel": "phone", "handy": "phone", "mobil": "phone",
    "info": "info",
    "wartezeit": "wait",
    "sonstige info": "extra", "sonstiges": "extra", "sonstige infos": "extra", "hinweis": "extra",
    "anfahrt": "anfahrt",
}
KEY_RE = re.compile(
    r"^\s*(" + "|".join(sorted((re.escape(k) for k in KEYS), key=len, reverse=True)) + r")\.?\s*:\s*(.*)$",
    re.IGNORECASE,
)


@dataclass
class ParsedOrder:
    name: str = ""
    street: str = ""
    plz: str = ""
    city: str = ""
    phone: str = ""
    info: str = ""
    extra: str = ""
    anfahrt: str = ""
    wait_min: int = int(DEFAULT_WAIT_H * 60)
    call_ahead_min: int = 0
    header_time: datetime | None = None  # Uhrzeit aus Kopfzeile (falls kopiert)
    raw: str = ""
    fields: dict = field(default_factory=dict)

    @property
    def address(self) -> str:
        parts = [self.street, f"{self.plz} {self.city}".strip()]
        return ", ".join(p for p in parts if p)

    @property
    def valid(self) -> bool:
        return bool(self.name and (self.street or self.plz))


def parse_header_time(line: str) -> datetime | None:
    m = HEADER_RE.search(line)
    if not m:
        return None
    day, mon, year, hh, mm = m.groups()
    mon = mon.strip(".").lower()
    month = int(mon) if mon.isdigit() else MONTHS.get(mon) or MONTHS.get(mon[:3])
    if not month:
        return None
    try:
        return datetime(int(year), month, int(day), int(hh), int(mm), tzinfo=TZ)
    except ValueError:
        return None


def parse_wait(text: str) -> int:
    """'2-3 Stunden' -> 180, '90 Min' -> 90, '1,5 Std' -> 90. Nimmt immer den höchsten Wert."""
    t = text.lower().replace(",", ".")
    nums = [float(n) for n in re.findall(r"\d+(?:\.\d+)?", t)]
    if not nums:
        return int(DEFAULT_WAIT_H * 60)
    val = max(nums)
    if re.search(r"min", t) and not re.search(r"std|stunde|h\b", t):
        return int(val)
    return int(val * 60)


def parse_call_ahead(text: str) -> int:
    t = text.lower()
    if "anruf" not in t and "anzuruf" not in t and "melden" not in t:
        return 0
    m = re.search(r"(\d+)\s*(?:min|minuten)\.?\s*(?:vorher|davor|bevor)", t)
    if m:
        return int(m.group(1))
    m = re.search(r"(\d+)\s*(?:std|stunde)\w*\.?\s*(?:vorher|davor|bevor)", t)
    if m:
        return int(m.group(1)) * 60
    if "vorher" in t or "bevor" in t or "davor" in t:
        return 30
    return 0


def _fill(order: ParsedOrder, key: str, value: str) -> None:
    value = value.strip()
    order.fields[key] = value
    if key == "name":
        order.name = value
    elif key == "street":
        order.street = value
    elif key == "plz":
        m = re.match(r"(\d{5})\s*(.*)", value)
        if m:
            order.plz, rest = m.group(1), m.group(2).strip()
            if rest:
                order.city = rest
        else:
            order.city = order.city or value
    elif key == "city":
        order.city = value
    elif key == "phone":
        order.phone = value
    elif key == "info":
        order.info = value
    elif key == "extra":
        order.extra = value
    elif key == "wait":
        order.wait_min = parse_wait(value)
    elif key == "anfahrt":
        order.anfahrt = value


def parse_orders(text: str) -> list[ParsedOrder]:
    if not text:
        return []
    orders: list[ParsedOrder] = []
    cur: ParsedOrder | None = None
    last_key = None
    pending_time = None

    for line in text.splitlines():
        ht = parse_header_time(line)
        if ht:
            pending_time = ht
            line = HEADER_RE.sub("", line)
            # Rest der Zeile ("Max, ") ist nur der Absender
            if not KEY_RE.match(line):
                continue
        m = KEY_RE.match(line)
        if m:
            key = KEYS[m.group(1).lower().rstrip(".")]
            if key == "eingang" or (key == "name" and cur is not None and cur.name):
                if cur is not None:
                    orders.append(cur)
                cur = ParsedOrder(header_time=pending_time)
                pending_time = None
                if key == "eingang":
                    last_key = key
                    continue
            if cur is None:
                cur = ParsedOrder(header_time=pending_time)
                pending_time = None
            _fill(cur, key, m.group(2))
            last_key = key
        elif cur is not None and line.strip() and last_key in ("info", "extra"):
            # mehrzeilige Info
            if last_key == "info":
                cur.info = (cur.info + " " + line.strip()).strip()
            else:
                cur.extra = (cur.extra + " " + line.strip()).strip()
        if cur is not None:
            cur.raw += line + "\n"
    if cur is not None:
        orders.append(cur)

    result = []
    for o in orders:
        o.call_ahead_min = parse_call_ahead(o.extra) or parse_call_ahead(o.info)
        o.raw = o.raw.strip()
        if o.valid:
            result.append(o)
    return result
