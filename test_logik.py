import sys, os, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from parser import parse_orders
from planner import Stop, plan_route, best_insert

TEXT = """Max, [6 Nov 2026 um 14:05]
Auftragseingang: D. 06.11.2026
Name: Herr Bisek
Straße: Pestalozzistr. 3
PLZ: 82110 Germering
Telefon: 0172 5437067
Info: Rohrreinigung, Toilette verstopft BAR/EC geklärt, ohne Preis!
Wartezeit: 2-3 Stunden
Anfahrt: 39€ Anfahrt
 Max, [6 Nov 2026 um 14:54]
Auftragseingang: 06.11.2026
Name: Frau Simon
Straße: Am Uhrackerl 3
PLZ: 85247 Schwabhausen
Telefon: 01607636121
Info: Rohrreinigung, Kellerabfluss verstopft BAR/EC geklärt, ohne Preis!
Wartezeit: 2-3 Stunden
Anfahrt: 39€ Anfahrt
 Max, [6 Nov 2026 um 16:02]
Auftragseingang: J. 06.11.2026
Name: Frau Lös
Straße: Valpichlerstr. 94
PLZ: 80686 München
Telefon: 01749901892
Info: Rohrreinigung, Küchenabfluss verstopft BAR/EC geklärt, ohne Preis!
Wartezeit: 2-3 Stunden
Sonstige Info: 30 Min. vorher anrufen
"""

# Hamburg: 0=Altona, 1=Dammtor, 2=Barmbek, 3=Wandsbek, 4=Jenfeld (Fahrminuten, grob)
HH = [
    [0, 12, 22, 28, 32],
    [12, 0, 14, 18, 24],
    [22, 14, 0, 10, 16],
    [28, 18, 10, 0, 9],
    [32, 24, 16, 9, 0],
]
NAMES = {1: "Dammtor", 2: "Barmbek", 3: "Wandsbek", 4: "Jenfeld"}

class T(unittest.TestCase):
    def test_parser(self):
        o = parse_orders(TEXT)
        self.assertEqual([x.name for x in o], ["Herr Bisek", "Frau Simon", "Frau Lös"])
        self.assertEqual(o[0].header_time.strftime("%H:%M"), "14:05")
        self.assertEqual(o[2].header_time.strftime("%H:%M"), "16:02")
        self.assertEqual((o[0].plz, o[0].city), ("82110", "Germering"))
        self.assertEqual(o[0].wait_min, 180)
        self.assertEqual(o[2].call_ahead_min, 30)
        self.assertEqual(o[0].call_ahead_min, 0)
        self.assertEqual(o[1].phone, "01607636121")

    def test_einzeln_ohne_kopf(self):
        o = parse_orders("Auftragseingang: 06.11.2026\nName: Test\nStraße: A 1\nPLZ: 20095 Hamburg\nWartezeit: 90 Min")
        self.assertEqual(len(o), 1); self.assertIsNone(o[0].header_time); self.assertEqual(o[0].wait_min, 90)

    def test_kein_auftrag(self):
        self.assertEqual(parse_orders("Hallo wie gehts"), [])

    def _stops(self, deadlines):
        return [Stop(i, deadlines[i], 60) for i in (1, 2, 3, 4)]

    def test_hamburg_route(self):
        p = plan_route(self._stops({1: 180, 2: 180, 3: 180, 4: 180}), HH)
        print("\nnormal:", [NAMES[i] for i in p.order])
        self.assertEqual(p.order, [1, 2, 3, 4])

    def test_hamburg_jenfeld_eilig(self):
        # Jenfeld wartet schon lange, nur noch 40 Min
        p = plan_route(self._stops({1: 180, 2: 180, 3: 180, 4: 40}), HH)
        print("jenfeld eilig:", [NAMES[i] for i in p.order])
        self.assertEqual(p.order[0], 4)

    def test_frueher_rein_aber_weit(self):
        # Jenfeld kam zuerst, hat aber noch genug Zeit -> trotzdem nicht zuerst
        p = plan_route(self._stops({1: 200, 2: 200, 3: 200, 4: 160}), HH)
        print("jenfeld zuerst rein:", [NAMES[i] for i in p.order])
        self.assertNotEqual(p.order[0], 4)

    def test_eigene_reihenfolge(self):
        p = plan_route(self._stops({1: 180, 2: 180, 3: 180, 4: 180}), HH, pinned=[3])
        self.assertEqual(p.order[0], 3)

    def test_einfuegen(self):
        seq = best_insert([1, 3, 4], 2, self._stops({1: 180, 2: 180, 3: 180, 4: 180}), HH)
        self.assertEqual(seq, [1, 2, 3, 4])

if __name__ == "__main__":
    unittest.main(verbosity=1)
