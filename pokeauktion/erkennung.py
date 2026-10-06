"""Kartenerkennung: Ausschnitt rein, Kandidaten mit Sicherheit raus.

Ablauf je Karte:
1. Text lesen (RapidOCR, lokal und kostenlos): Name, KP, Attacken, Schaden.
2. Kandidaten über den Namen in allen Sprachen suchen (unscharf, OCR macht Fehler).
3. Mit KP und Attacken (in der Sprache des Kandidaten) bewerten. Die Attacken entscheiden
   auch über die Sprache, wenn ein Name in mehreren Sprachen gleich ist ("Murkrow").
4. Kandidaten mit gleichem Text (Nachdrucke, Shiny, Full Art) per Bildvergleich trennen,
   aber nur, wenn der Unterschied eindeutig ist. Sonst bleiben alle als mögliche Versionen.
5. Optional: unsichere Karten zusätzlich von Claude lesen lassen (über Claude Code im Abo).
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from functools import lru_cache

import cv2
import numpy as np
from rapidfuzz import fuzz, process

from .db import normalisiere
from .katalog import Karte, lade_karte

# Wörter auf der Karte, die nicht zum Namen gehören (Stufe, KP-Kürzel in mehreren Sprachen)
FUELLWOERTER = {
    "basic", "basis", "base", "stage", "phase", "stufe", "stadio", "fase", "etapa", "level", "kp", "hp", "ps", "pv",
    "pokemon", "trainer", "item", "supporter", "unterstutzung", "artikel", "evolves", "from", "entwickelt", "sich",
    "aus",
}
# Diese Wörter gehören zwar zum Namen, werden von der OCR aber oft verschluckt; nicht entfernen:
# ex, gx, v, vmax, vstar, mega, team, rocket(s)

GUELTIGE_KP = set(range(30, 410, 10))

# Sprachausgaben mit lateinischer Schrift, die auf deutschen Auktionen selten sind und oft
# denselben Text wie eine häufige Ausgabe haben (indonesische Karten nutzen z. B. englische Namen).
SELTENE_SPRACHEN = {"id": 8, "es-mx": 8, "pt-br": 6, "pt": 2, "es": 1, "it": 1}


@dataclass
class OcrZeile:
    text: str
    sicherheit: float
    y: float  # Mitte, relativ zur Kartenhöhe (0 = oben)
    x: float
    hoehe: float


@dataclass
class Merkmale:
    zeilen: list[OcrZeile]
    namen: list[str]            # mögliche Namen aus dem Kopfbereich (normalisiert)
    kp: set[int]                # mögliche KP-Werte
    attacken: list[str]         # normalisierte Textzeilen aus dem Attackenbereich
    zahlen: set[int]            # Schadenszahlen

    @property
    def leer(self) -> bool:
        return not self.namen and not self.attacken


@dataclass
class Kandidat:
    karte_id: str
    sprache: str
    name: str
    punkte: float
    name_punkte: float
    kp_passt: bool | None
    attacken_treffer: int


@dataclass
class Ergebnis:
    sicherheit: str                     # hoch / mittel / niedrig / nicht erkannt
    karten: list[Karte] = field(default_factory=list)   # mögliche Versionen, beste zuerst
    sprache: str | None = None
    merkmale: Merkmale | None = None
    begruendung: str = ""
    quelle: str = "ocr"                 # ocr / claude
    # Drucke mit gleichem Text, die per Bildvergleich/Hinweis ausgeschlossen wurden.
    # Die günstigste davon wird bei teuren Treffern zur Kontrolle mit angezeigt.
    ausgeschlossen: list[Karte] = field(default_factory=list)

    @property
    def guenstigste_alternative(self) -> Karte | None:
        mit_preis = [k for k in self.ausgeschlossen if k.preis_min is not None]
        return min(mit_preis, key=lambda k: k.preis_min) if mit_preis else None

    @property
    def preis_min(self) -> float | None:
        werte = [k.preis_min for k in self.karten if k.preis_min is not None]
        return min(werte) if werte else None

    @property
    def preis_max(self) -> float | None:
        werte = [k.preis_max for k in self.karten if k.preis_max is not None]
        return max(werte) if werte else None


# --------------------------------------------------------------------------- OCR

@lru_cache(maxsize=1)
def _ocr():
    # rapidocr bringt die Erkennungsmodelle (PP-OCRv6) im Paket mit, es wird nichts nachgeladen.
    import logging as _logging
    from rapidocr import RapidOCR
    _logging.disable(_logging.INFO)  # RapidOCR meldet beim Start jede Modelldatei; das ist nur Rauschen
    try:
        ocr = RapidOCR()
    finally:
        _logging.disable(_logging.NOTSET)
    _logging.getLogger("RapidOCR").setLevel(_logging.WARNING)
    return ocr


def lese_text(bild: np.ndarray) -> list[OcrZeile]:
    h, w = bild.shape[:2]
    faktor = max(1.0, 900 / h)  # kleine Ausschnitte vergrößern, kleine Schrift wird sonst nicht gelesen
    if faktor > 1:
        bild = cv2.resize(bild, None, fx=faktor, fy=faktor, interpolation=cv2.INTER_CUBIC)
    ausgabe = _ocr()(bild)
    H = bild.shape[0]
    W = bild.shape[1]
    zeilen = []
    if ausgabe.boxes is None:
        return zeilen
    for box, text, sicherheit in zip(ausgabe.boxes, ausgabe.txts, ausgabe.scores):
        ys = [p[1] for p in box]
        xs = [p[0] for p in box]
        zeilen.append(OcrZeile(text, float(sicherheit), (min(ys) + max(ys)) / 2 / H,
                               (min(xs) + max(xs)) / 2 / W, (max(ys) - min(ys)) / H))
    return sorted(zeilen, key=lambda z: (z.y, z.x))


def _kp_aus(text: str) -> set[int]:
    """KP aus OCR-Text. Das Energiesymbol hinter der Zahl wird oft als 0, 6, @ oder O gelesen."""
    werte = set()
    for zahl in re.findall(r"\d{2,4}", text):
        for kandidat in {zahl, zahl[:-1], zahl[1:]}:
            if kandidat and int(kandidat) in GUELTIGE_KP:
                werte.add(int(kandidat))
    return werte


def _name_teil(text: str) -> str:
    woerter = [w for w in normalisiere(re.sub(r"\d+", " ", text)).split() if w not in FUELLWOERTER]
    return " ".join(woerter)


def merkmale_aus(zeilen: list[OcrZeile], sicherheit_min: float = 0.5) -> Merkmale:
    gut = [z for z in zeilen if z.sicherheit >= sicherheit_min]
    kopf = [z for z in gut if z.y < 0.2]
    rumpf = [z for z in gut if z.y >= 0.4]
    namen: list[str] = []
    for z in kopf:
        teil = _name_teil(z.text)
        if len(teil.replace(" ", "")) >= 3:
            namen.append(teil)
        # OCR verliest oft den Anfang ("Tan keara Wcezing"): auch die letzten Wörter allein versuchen
        woerter = teil.split()
        for n in (1, 2):
            if len(woerter) > n and len(ende := " ".join(woerter[-n:]).replace(" ", "")) >= 5:
                namen.append(" ".join(woerter[-n:]))
    # Benachbarte Kopfzeilen auf gleicher Höhe zusammensetzen ("Charizard" + "VSTAR")
    for a, b in zip(kopf, kopf[1:]):
        if abs(a.y - b.y) < 0.03:
            teil = _name_teil(a.text + " " + b.text)
            if teil and teil not in namen:
                namen.append(teil)
    kp: set[int] = set()
    for z in kopf:
        kp |= _kp_aus(z.text)
    attacken = [n for z in rumpf if len(n := _name_teil(z.text).replace(" ", "")) >= 4]
    zahlen = {int(m) for z in rumpf for m in re.findall(r"\b(\d{2,3})\b", z.text) if int(m) % 10 == 0}
    return Merkmale(zeilen, namen, kp, attacken, zahlen)


# --------------------------------------------------------------------------- Kandidaten

class Namensindex:
    """Alle Kartennamen aller Sprachen, ohne Leerzeichen, für die unscharfe Suche."""

    def __init__(self, con: sqlite3.Connection):
        self.eintraege: dict[str, list[tuple[str, str]]] = {}
        for z in con.execute("SELECT karte_id, sprache, name_norm FROM karten_texte"):
            schluessel = z["name_norm"].replace(" ", "")
            if schluessel:
                self.eintraege.setdefault(schluessel, []).append((z["karte_id"], z["sprache"]))
        self.schluessel = list(self.eintraege)

    def suche(self, name: str, grenze: int = 72, anzahl: int = 12) -> list[tuple[str, float]]:
        q = name.replace(" ", "")
        if len(q) < 3:
            return []
        treffer: dict[str, float] = {}
        for s, punkte, _ in process.extract(q, self.schluessel, scorer=fuzz.ratio, limit=anzahl, score_cutoff=grenze):
            treffer[s] = max(treffer.get(s, 0), punkte)
        # OCR liest oft Text vor/nach dem Namen mit ("TeamRocketsKramurx", "Mega...ex360"):
        # Teilübereinstimmung zulassen, wenn der Name einen großen Teil der Zeile ausmacht.
        for s, punkte, _ in process.extract(q, self.schluessel, scorer=fuzz.partial_ratio, limit=anzahl * 2,
                                            score_cutoff=max(grenze, 85)):
            if len(s) >= 5 and len(s) >= 0.45 * len(q):
                treffer[s] = max(treffer.get(s, 0), punkte * (0.8 + 0.2 * min(1, len(s) / len(q))))
        return sorted(treffer.items(), key=lambda t: -t[1])


class Attackenindex:
    """Alle Attackennamen, für Karten mit unlesbarem Namen (Spiegelung über dem Kopf)."""

    def __init__(self, con: sqlite3.Connection):
        self.eintraege: dict[str, list[tuple[str, str]]] = {}
        for z in con.execute("SELECT karte_id, sprache, name_norm FROM attacken"):
            schluessel = z["name_norm"].replace(" ", "")
            if len(schluessel) >= 8:
                self.eintraege.setdefault(schluessel, []).append((z["karte_id"], z["sprache"]))
        self.schluessel = list(self.eintraege)

    def suche(self, zeile: str, grenze: int = 88) -> list[tuple[str, float]]:
        if len(zeile) < 8:
            return []
        return [(s, p) for s, p, _ in process.extract(zeile, self.schluessel, scorer=fuzz.partial_ratio,
                                                      limit=60, score_cutoff=grenze) if len(s) >= 0.6 * len(zeile)]


def _attacken_der_karte(con, karte_id, sprache) -> list[tuple[str, str | None]]:
    return [(z["name_norm"].replace(" ", ""), z["schaden"]) for z in con.execute(
        "SELECT name_norm, schaden FROM attacken WHERE karte_id = ? AND sprache = ? ORDER BY pos", (karte_id, sprache))]


def bewerte(con: sqlite3.Connection, index: Namensindex, m: Merkmale, hinweis_sprachen: set[str],
            att_index: Attackenindex | None = None) -> list[Kandidat]:
    namens_treffer: dict[tuple[str, str], tuple[str, float]] = {}
    for name in m.namen:
        for schluessel, punkte in index.suche(name):
            for karte_id, sprache in index.eintraege[schluessel]:
                alt = namens_treffer.get((karte_id, sprache))
                if not alt or punkte > alt[1]:
                    namens_treffer[(karte_id, sprache)] = (schluessel, punkte)
    bester_name = max((p for _, p in namens_treffer.values()), default=0)
    if bester_name < 85 and att_index is not None:
        # Name unlesbar oder unsicher: Kandidaten zusätzlich über die Attacken suchen
        # Mit gelesenen KP darf die Attackensuche unschärfer sein, die KP filtern danach.
        for zeile in m.attacken:
            for schluessel, _ in att_index.suche(zeile, 75 if m.kp else 88):
                for karte_id, sprache in att_index.eintraege[schluessel][:200]:
                    if (karte_id, sprache) not in namens_treffer:
                        name = con.execute("SELECT name_norm FROM karten_texte WHERE karte_id = ? AND sprache = ?",
                                           (karte_id, sprache)).fetchone()
                        namens_treffer[(karte_id, sprache)] = ((name["name_norm"] if name else "").replace(" ", ""), 0.0)
    if not namens_treffer:
        return []
    kp_je_karte = {}
    ids = list({k for k, _ in namens_treffer})
    for i in range(0, len(ids), 500):
        teil = ids[i:i + 500]
        for z in con.execute(f"SELECT id, kp FROM karten WHERE id IN ({','.join('?' * len(teil))})", teil):
            kp_je_karte[z["id"]] = z["kp"]

    kandidaten = []
    for (karte_id, sprache), (schluessel, name_punkte) in namens_treffer.items():
        punkte = name_punkte
        kp = kp_je_karte.get(karte_id)
        kp_passt = None
        if m.kp and kp:
            kp_passt = kp in m.kp
            punkte += 25 if kp_passt else -15
        treffer = 0
        att_grenze = 75 if kp_passt else 85  # passende KP machen eine unscharf gelesene Attacke glaubwürdig
        for att_name, schaden in _attacken_der_karte(con, karte_id, sprache):
            if any(fuzz.partial_ratio(att_name, zeile) >= att_grenze for zeile in m.attacken if len(att_name) >= 4):
                treffer += 1
                punkte += 18
                if schaden and re.sub(r"\D", "", schaden) and int(re.sub(r"\D", "", schaden)) in m.zahlen:
                    punkte += 4
        if sprache in hinweis_sprachen:
            punkte += 3
        elif sprache in SELTENE_SPRACHEN:
            punkte -= SELTENE_SPRACHEN[sprache]
        kandidaten.append(Kandidat(karte_id, sprache, schluessel, punkte, name_punkte, kp_passt, treffer))
    return sorted(kandidaten, key=lambda k: -k.punkte)


# --------------------------------------------------------------------------- Ergebnis

def _gleicher_text(con, a: Kandidat, b: Kandidat) -> bool:
    """Gleicher Name, gleiche KP und gleiche Attacken = mit Text nicht unterscheidbar."""
    if a.name != b.name:
        return False
    kp = {z["id"]: z["kp"] for z in con.execute("SELECT id, kp FROM karten WHERE id IN (?, ?)", (a.karte_id, b.karte_id))}
    if kp.get(a.karte_id) != kp.get(b.karte_id):
        return False
    return [n for n, _ in _attacken_der_karte(con, a.karte_id, a.sprache)] == \
           [n for n, _ in _attacken_der_karte(con, b.karte_id, b.sprache)]


@dataclass
class Indizes:
    namen: Namensindex
    attacken: Attackenindex

    @classmethod
    def laden(cls, con: sqlite3.Connection) -> "Indizes":
        return cls(Namensindex(con), Attackenindex(con))


def erkenne_ausschnitt(
    con: sqlite3.Connection,
    indizes: Indizes,
    bild: np.ndarray,
    hinweis_sprachen: set[str] | None = None,
    bildvergleich=None,
) -> Ergebnis:
    m = merkmale_aus(lese_text(bild))
    return erkenne_aus_merkmalen(con, indizes, m, bild, hinweis_sprachen or set(), bildvergleich)


def _nach_besonderheit(con, ids: list[str], besonderheit: str | None) -> tuple[list[str], str | None]:
    """Druckversionen anhand eines Hinweises (Shiny, Full Art, ...) eingrenzen, nur wenn das eindeutig trennt."""
    from .claude_code import BESONDERHEIT_SELTENHEIT
    if not besonderheit or len(ids) < 2:
        return ids, None
    seltenheit = {z["id"]: (z["seltenheit"] or "").lower() for z in con.execute(
        f"SELECT id, seltenheit FROM karten WHERE id IN ({','.join('?' * len(ids))})", ids)}
    b = besonderheit.lower()
    alle_besonderen = {w for werte in BESONDERHEIT_SELTENHEIT.values() for w in werte}
    if b in BESONDERHEIT_SELTENHEIT:
        passend = [i for i in ids if any(w in seltenheit.get(i, "") for w in BESONDERHEIT_SELTENHEIT[b])]
    elif b in ("normal", "holo", "reverse holo"):
        passend = [i for i in ids if not any(w in seltenheit.get(i, "") for w in alle_besonderen)]
    else:
        return ids, None
    if passend and len(passend) < len(ids):
        return passend, f"laut Claude '{besonderheit}'"
    return ids, None


def erkenne_aus_merkmalen(con, indizes: Indizes, m: Merkmale, bild, hinweis_sprachen: set[str],
                          bildvergleich=None, besonderheit: str | None = None) -> Ergebnis:
    if m.leer:
        return Ergebnis("nicht erkannt", merkmale=m, begruendung="kein Text lesbar")
    kandidaten = bewerte(con, indizes.namen, m, hinweis_sprachen, indizes.attacken)
    if not kandidaten:
        return Ergebnis("nicht erkannt", merkmale=m, begruendung="kein passender Name in der Datenbank")
    nur_attacken = kandidaten[0].name_punkte < 72
    if nur_attacken and not (kandidaten[0].attacken_treffer >= 1 and kandidaten[0].kp_passt):
        return Ergebnis("nicht erkannt", merkmale=m, begruendung="kein passender Name in der Datenbank")

    bester = kandidaten[0]
    # Alle Kandidaten, die praktisch gleich gut sind und denselben Text haben, sind mögliche Versionen
    gruppe = [k for k in kandidaten if k.punkte >= bester.punkte - 6 and k.sprache == bester.sprache
              and (k.karte_id == bester.karte_id or _gleicher_text(con, k, bester))]
    # Gleich gute Kandidaten mit anderem Text (z. B. Name gleich, KP nicht gelesen) machen es unsicher
    # Eine Karte mit gelesenen, aber nicht passenden KP ist keine echte Alternative.
    konkurrenz = [k for k in kandidaten if k.punkte >= bester.punkte - 6 and k not in gruppe
                  and k.karte_id not in {g.karte_id for g in gruppe}
                  and not (bester.kp_passt and k.kp_passt is False)]

    begruendung = ["Name unlesbar, über Attacken gefunden" if nur_attacken
                   else f"Name '{bester.name}' ({bester.name_punkte:.0f}%)"]
    if bester.kp_passt:
        begruendung.append("KP passt")
    if bester.attacken_treffer:
        begruendung.append(f"{bester.attacken_treffer} Attacke(n) passen")

    ids = list(dict.fromkeys(k.karte_id for k in gruppe))
    # Gleicher Name und gleiche KP, aber (teilweise) andere Attacken, z. B. weil nur eine Attacke
    # lesbar war: auch diese Karten kommen in den Bildvergleich.
    kp_bester = con.execute("SELECT kp FROM karten WHERE id = ?", (bester.karte_id,)).fetchone()["kp"]
    for k in konkurrenz:
        if k.name == bester.name and k.sprache == bester.sprache and k.karte_id not in ids:
            if con.execute("SELECT kp FROM karten WHERE id = ?", (k.karte_id,)).fetchone()["kp"] == kp_bester:
                ids.append(k.karte_id)
    konkurrenz = [k for k in konkurrenz if k.karte_id not in ids]
    alle_ids = list(ids)
    ids, notiz = _nach_besonderheit(con, ids, besonderheit)
    if notiz:
        begruendung.append(notiz)
    if len(ids) > 1 and bildvergleich is not None:
        auswahl, notiz = bildvergleich(bild, ids, bester.sprache)
        if auswahl:
            ids = auswahl
        if notiz:
            begruendung.append(notiz)

    belege = (bester.kp_passt is True) + min(2, bester.attacken_treffer)
    name_ok = bester.name_punkte >= 85 or (bester.name_punkte >= 72 and belege >= 2)
    if name_ok and belege >= 1 and not konkurrenz and len(ids) == 1:
        sicherheit = "hoch"
    elif name_ok and (belege >= 1 or len(ids) > 1) and len(konkurrenz) <= 2:
        sicherheit = "mittel"
    elif nur_attacken and belege >= 2 and not konkurrenz:
        sicherheit = "mittel"
    else:
        sicherheit = "niedrig"
    if len(ids) > 1:
        begruendung.append(f"{len(ids)} mögliche Drucke")
    if konkurrenz:
        # Nie still auf eine Karte festlegen: ähnliche Karten immer mit anzeigen
        begruendung.append(f"{len(konkurrenz)} weitere ähnliche Karten")
        ids += [i for i in dict.fromkeys(k.karte_id for k in konkurrenz) if i not in ids][:4]

    karten = [k for i in ids if (k := lade_karte(con, i, bester.sprache))]
    ausgeschlossen = [k for i in alle_ids if i not in ids and (k := lade_karte(con, i, bester.sprache))]
    return Ergebnis(sicherheit, karten, bester.sprache, m, ", ".join(begruendung), ausgeschlossen=ausgeschlossen)


def sprachen_aus_hinweis(text: str | None) -> set[str]:
    """Sprachhinweise aus Auktionstitel/-beschreibung."""
    t = normalisiere(text)
    sprachen = set()
    for wort, sprache in (("deutsch", "de"), ("german", "de"), ("englisch", "en"), ("english", "en"),
                          ("japan", "ja"), ("japanisch", "ja"), ("franzosisch", "fr"), ("french", "fr"),
                          ("italienisch", "it"), ("spanisch", "es"), ("chinesisch", "zh-cn"), ("chinese", "zh-cn"),
                          ("koreanisch", "ko"), ("korean", "ko")):
        if wort in t.split() or any(w.startswith(wort) for w in t.split()):
            sprachen.add(sprache)
    return sprachen
