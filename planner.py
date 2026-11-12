"""Reihenfolge berechnen.

Ziel: möglichst wenig fahren, aber keiner soll über seine Frist warten.
Kosten = Fahrminuten + LATE_WEIGHT × Minuten zu spät (über alle Aufträge).

Beispiel Hamburg: Monteur in Altona, Aufträge Dammtor, Barmbek, Wandsbek, Jenfeld
→ fährt der Reihe nach raus (kürzeste Strecke), außer z. B. Jenfeld hat
nur noch wenig Zeit – dann lohnt sich der Umweg und Jenfeld kommt zuerst.
"""
from __future__ import annotations

from dataclasses import dataclass
from itertools import permutations

BRUTE_FORCE_MAX = 8  # bis 8 freie Aufträge: alle Reihenfolgen durchprobieren


@dataclass
class Stop:
    id: int
    deadline_min: float  # Minuten ab jetzt bis Frist (kann negativ sein)
    service_min: float


@dataclass
class Leg:
    stop_id: int
    drive_min: float
    arrive_min: float  # Minuten ab jetzt
    late_min: float


@dataclass
class Plan:
    order: list[int]  # Stop-IDs
    legs: list[Leg]
    drive_min: float
    late_min: float
    cost: float


def evaluate(seq: list[int], stops: dict[int, Stop], idx: dict[int, int],
             matrix: list[list[float]], late_weight: float) -> Plan:
    """seq = Stop-IDs. Knoten 0 in der Matrix ist der Startpunkt."""
    t = 0.0
    cur = 0
    drive = late = 0.0
    legs = []
    for sid in seq:
        d = matrix[cur][idx[sid]]
        t += d
        drive += d
        s = stops[sid]
        l = max(0.0, t - s.deadline_min)
        late += l
        legs.append(Leg(sid, d, t, l))
        t += s.service_min
        cur = idx[sid]
    return Plan(list(seq), legs, drive, late, drive + late_weight * late)


def _improve_2opt(seq, fixed, stops, idx, matrix, w):
    best = evaluate(fixed + seq, stops, idx, matrix, w)
    improved = True
    while improved:
        improved = False
        for i in range(len(seq) - 1):
            for j in range(i + 1, len(seq)):
                cand = seq[:i] + seq[i:j + 1][::-1] + seq[j + 1:]
                p = evaluate(fixed + cand, stops, idx, matrix, w)
                if p.cost < best.cost - 1e-9:
                    best, seq, improved = p, cand, True
        # einzelne Aufträge verschieben (or-opt)
        for i in range(len(seq)):
            for j in range(len(seq)):
                if i == j:
                    continue
                cand = seq[:i] + seq[i + 1:]
                cand.insert(j, seq[i])
                p = evaluate(fixed + cand, stops, idx, matrix, w)
                if p.cost < best.cost - 1e-9:
                    best, seq, improved = p, cand, True
    return best


def plan_route(stops: list[Stop], matrix: list[list[float]], late_weight: float = 2.0,
               pinned: list[int] | None = None) -> Plan:
    """matrix: (n+1)×(n+1) Fahrminuten, Index 0 = Start, Index k = stops[k-1].
    pinned: Stop-IDs, die der Monteur selbst in dieser Reihenfolge vorne haben will."""
    by_id = {s.id: s for s in stops}
    idx = {s.id: k + 1 for k, s in enumerate(stops)}
    fixed = [sid for sid in (pinned or []) if sid in by_id]
    free = [s.id for s in stops if s.id not in fixed]
    if not free:
        return evaluate(fixed, by_id, idx, matrix, late_weight)
    if len(free) <= BRUTE_FORCE_MAX:
        best = None
        for perm in permutations(free):
            p = evaluate(fixed + list(perm), by_id, idx, matrix, late_weight)
            if best is None or p.cost < best.cost - 1e-9:
                best = p
        return best
    # viele Aufträge: dringendste zuerst als Startlösung, dann verbessern
    start = sorted(free, key=lambda sid: by_id[sid].deadline_min)
    return _improve_2opt(start, fixed, by_id, idx, matrix, late_weight)


def best_insert(current: list[int], new_id: int, stops: list[Stop],
                matrix: list[list[float]], late_weight: float = 2.0, min_pos: int = 1) -> list[int]:
    """Neuen Auftrag an die beste Stelle einer festen Reihenfolge einfügen.
    min_pos=1: der Auftrag, den der Monteur als nächstes gewählt hat, bleibt vorne."""
    by_id = {s.id: s for s in stops}
    idx = {s.id: k + 1 for k, s in enumerate(stops)}
    best_seq, best_cost = None, None
    for pos in range(min(min_pos, len(current)), len(current) + 1):
        seq = current[:pos] + [new_id] + current[pos:]
        c = evaluate(seq, by_id, idx, matrix, late_weight).cost
        if best_cost is None or c < best_cost - 1e-9:
            best_seq, best_cost = seq, c
    return best_seq
