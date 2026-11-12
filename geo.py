"""Adressen → Koordinaten, Fahrzeiten, Karten-Links.

Mit ORS_API_KEY: OpenRouteService (echte Straßen-Fahrzeiten, ohne Stau).
Ohne Key: Adressen über OpenStreetMap/Nominatim, Fahrzeit nur geschätzt (Luftlinie).
"""
from __future__ import annotations

import logging
import math
from urllib.parse import quote

import httpx

from config import ORS_API_KEY

log = logging.getLogger(__name__)
UA = {"User-Agent": "Routenbot/1.0 (Telegram Monteur-Routen)"}


async def geocode(address: str) -> tuple[float, float] | None:
    """Gibt (lat, lng) zurück oder None."""
    async with httpx.AsyncClient(timeout=15, headers=UA) as c:
        try:
            if ORS_API_KEY:
                r = await c.get(
                    "https://api.openrouteservice.org/geocode/search",
                    params={"api_key": ORS_API_KEY, "text": address,
                            "boundary.country": "DE", "size": 1},
                )
                r.raise_for_status()
                feats = r.json().get("features") or []
                if feats:
                    lng, lat = feats[0]["geometry"]["coordinates"][:2]
                    return lat, lng
            else:
                r = await c.get(
                    "https://nominatim.openstreetmap.org/search",
                    params={"q": address, "format": "json", "limit": 1, "countrycodes": "de"},
                )
                r.raise_for_status()
                js = r.json()
                if js:
                    return float(js[0]["lat"]), float(js[0]["lon"])
        except Exception as e:  # noqa: BLE001
            log.warning("Geocoding fehlgeschlagen für %s: %s", address, e)
    return None


def haversine_km(a, b) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


def estimate_matrix(points):
    """Schätzung ohne Routing-Dienst: Luftlinie × 1,35, Ø 50 km/h + 3 Min."""
    n = len(points)
    dur = [[0.0] * n for _ in range(n)]
    dist = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(n):
            if i != j and points[i] and points[j]:
                km = haversine_km(points[i], points[j]) * 1.35
                dist[i][j] = km
                dur[i][j] = km / 50 * 60 + 3
    return dur, dist


async def route_matrix(points: list[tuple[float, float]]):
    """Fahrminuten- und km-Matrix. Gibt (dauer, distanz, echt?) zurück."""
    if ORS_API_KEY and len(points) >= 2:
        try:
            async with httpx.AsyncClient(timeout=20, headers=UA) as c:
                r = await c.post(
                    "https://api.openrouteservice.org/v2/matrix/driving-car",
                    headers={"Authorization": ORS_API_KEY},
                    json={"locations": [[p[1], p[0]] for p in points],
                          "metrics": ["duration", "distance"], "units": "km"},
                )
                r.raise_for_status()
                js = r.json()
                est_dur, est_dist = estimate_matrix(points)
                dur = [[(v / 60 if v is not None else est_dur[i][j]) for j, v in enumerate(row)]
                       for i, row in enumerate(js["durations"])]
                dist = [[(v if v is not None else est_dist[i][j]) for j, v in enumerate(row)]
                        for i, row in enumerate(js["distances"])]
                return dur, dist, True
        except Exception as e:  # noqa: BLE001
            log.warning("Routing fehlgeschlagen, nehme Schätzung: %s", e)
    dur, dist = estimate_matrix(points)
    return dur, dist, False


# ---------- Karten-Links ----------

def _pt(p) -> str:
    return f"{p[0]:.6f},{p[1]:.6f}"


def google_nav(dest, address: str = "") -> str:
    target = _pt(dest) if dest else address
    return f"https://www.google.com/maps/dir/?api=1&destination={quote(target)}&travelmode=driving"


def apple_nav(dest, address: str = "") -> str:
    target = _pt(dest) if dest else address
    return f"https://maps.apple.com/?daddr={quote(target)}&dirflg=d"


def google_route(origin, dests: list) -> str:
    """Ganze Route. Google nimmt max. 9 Zwischenstopps."""
    dests = [d for d in dests if d][:10]
    if not dests:
        return ""
    url = "https://www.google.com/maps/dir/?api=1&travelmode=driving"
    if origin:
        url += "&origin=" + quote(_pt(origin))
    url += "&destination=" + quote(_pt(dests[-1]))
    if len(dests) > 1:
        url += "&waypoints=" + quote("|".join(_pt(d) for d in dests[:-1]))
    return url
