"""Auswertungen speichern und pro Karte den festen Wert berechnen.

Fester Wert je Karte (in dieser Reihenfolge):
1. Preis aus Deutschland, den du über das Lesezeichen (oder von Hand) übernommen hast.
2. Sonst vorläufig: der billigste "ab"-Preis (Cardmarket, alle Länder) aller möglichen Drucke;
   fehlt er, der billigste Trendpreis.
Bei unsicherem Druck zählt also immer der billigste mögliche Druck.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import cv2
import numpy as np

from . import config, preise_de
from .analyse import Einstellungen, analysiere_foto
from .erkennung import Indizes
from .katalog import Karte, englischer_name, lade_karte

SCHEMA = """
CREATE TABLE IF NOT EXISTS auswertungen (
    id        TEXT PRIMARY KEY,           -- Prüfsumme der Fotos: gleiche Fotos werden nicht neu ausgewertet
    name      TEXT,
    fotos     TEXT NOT NULL,              -- JSON-Liste der Dateinamen
    zeilen    TEXT NOT NULL,              -- JSON: erkannte Karten
    erstellt  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS korrekturen (
    auswertung TEXT NOT NULL,
    zeile      TEXT NOT NULL,             -- z. B. "1-4" = Foto 1, Karte 4
    karte_id   TEXT,                      -- von Hand gewählte Karte (NULL = Erkennung)
    preis_de   REAL,                      -- übernommener Preis aus Deutschland
    preis_url  TEXT,
    PRIMARY KEY (auswertung, zeile)
);
CREATE TABLE IF NOT EXISTS offen (
    id         INTEGER PRIMARY KEY CHECK (id = 1),
    auswertung TEXT, zeile TEXT, zeit TEXT
);
"""


def vorbereiten(con: sqlite3.Connection) -> None:
    con.executescript(SCHEMA + preise_de.SCHEMA)


def _billigster(karten: list[Karte]) -> tuple[float | None, str]:
    ab = [v.low for k in karten for v in k.versionen if v.low]
    if ab:
        return min(ab), "ab"
    trend = [v.trend for k in karten for v in k.versionen if v.trend]
    if trend:
        return min(trend), "trend"
    return None, "keiner"


def neue_auswertung(con, indizes: Indizes, dateien: list[tuple[str, bytes]], ordner: Path,
                    einstellungen: Einstellungen) -> str:
    vorbereiten(con)
    pruef = hashlib.sha1(b"".join(inhalt for _, inhalt in dateien)).hexdigest()[:16]
    if con.execute("SELECT 1 FROM auswertungen WHERE id = ?", (pruef,)).fetchone():
        return pruef  # schon ausgewertet: nicht noch einmal rechnen (und kein Claude-Kontingent verbrauchen)
    (ordner / pruef).mkdir(parents=True, exist_ok=True)
    fotos, zeilen = [], []
    for i, (name, inhalt) in enumerate(dateien, 1):
        bild = cv2.imdecode(np.frombuffer(inhalt, np.uint8), cv2.IMREAD_COLOR)
        if bild is None:
            continue
        datei = f"foto{i:02d}.jpg"
        cv2.imwrite(str(ordner / pruef / datei), bild)
        fotos.append({"datei": datei, "name": name, "breite": bild.shape[1], "hoehe": bild.shape[0]})
        for k in analysiere_foto(con, indizes, bild, None, einstellungen).karten:
            a, e = k.ausschnitt, k.ergebnis
            zeilen.append({
                "zeile": f"{len(fotos)}-{a.nr}", "foto": len(fotos), "nr": a.nr, "box": list(map(int, a.box)),
                "sicherheit": e.sicherheit, "sprache": e.sprache, "begruendung": e.begruendung, "quelle": e.quelle,
                "kandidaten": [c.id for c in e.karten],
                "alternativen": [c.id for c in e.alternativen],
            })
    con.execute("INSERT INTO auswertungen (id, name, fotos, zeilen, erstellt) VALUES (?, ?, ?, ?, ?)",
                (pruef, ", ".join(n for n, _ in dateien), json.dumps(fotos), json.dumps(zeilen),
                 datetime.now(timezone.utc).isoformat()))
    con.commit()
    return pruef


def _karte_json(k: Karte) -> dict:
    return {"id": k.id, "name": k.name, "set": k.set_name or k.set_id, "set_id": k.set_id, "nummer": k.nummer,
            "seltenheit": k.seltenheit, "bild": k.bild_url, "ab": k.preis_min_ab, "trend_max": k.preis_max,
            "versionen": [{"bezeichnung": v.bezeichnung, "ab": v.low, "trend": v.trend} for v in k.versionen]}


def ergebnis(con: sqlite3.Connection, auswertung_id: str) -> dict | None:
    vorbereiten(con)
    a = con.execute("SELECT * FROM auswertungen WHERE id = ?", (auswertung_id,)).fetchone()
    if not a:
        return None
    korrektur = {z["zeile"]: z for z in con.execute("SELECT * FROM korrekturen WHERE auswertung = ?", (auswertung_id,))}
    offen = con.execute("SELECT * FROM offen WHERE id = 1").fetchone()
    zeilen = []
    for z in json.loads(a["zeilen"]):
        kor = korrektur.get(z["zeile"])
        sprache = z["sprache"] or "de"
        ids = [kor["karte_id"]] if kor and kor["karte_id"] else z["kandidaten"]
        karten = [k for i in ids if (k := lade_karte(con, i, sprache))]
        wert, quelle = None, "keiner"
        if kor and kor["preis_de"] is not None:
            wert, quelle = kor["preis_de"], "de"
        elif len(karten) == 1 and (p := preise_de.gespeichert(con, karten[0].id)) is not None:
            wert, quelle = p, "de"
        if wert is None:
            wert, quelle = _billigster(karten)
        name_en = (englischer_name(con, karten[0].id) if karten else None) or (karten[0].name if karten else "")
        zeilen.append({
            **{k: z[k] for k in ("zeile", "foto", "nr", "box", "sicherheit", "begruendung", "quelle")},
            "sprache": sprache if karten else None,
            "erkannt": bool(karten),
            "von_hand": bool(kor and kor["karte_id"]),
            "karten": [_karte_json(k) for k in karten],
            "alternativen": [] if kor and kor["karte_id"] else [
                _karte_json(k) for i in z.get("alternativen", []) if (k := lade_karte(con, i, sprache))],
            "wert": wert, "wert_quelle": quelle,
            "cardmarket": preise_de.suchlink(name_en) if karten else None,
            "wartet_auf_preis": bool(offen and offen["auswertung"] == auswertung_id and offen["zeile"] == z["zeile"]),
        })
    zeilen.sort(key=lambda z: -(z["wert"] or 0))
    erkannt = [z for z in zeilen if z["erkannt"]]
    return {
        "id": a["id"], "name": a["name"], "fotos": json.loads(a["fotos"]), "zeilen": zeilen,
        "summe": round(sum(z["wert"] or 0 for z in erkannt), 2),
        "anzahl": len(zeilen), "nicht_erkannt": len(zeilen) - len(erkannt),
        "mit_de_preis": sum(z["wert_quelle"] == "de" for z in erkannt),
        "ohne_preis": sum(z["wert"] is None for z in erkannt),
    }


def korrigieren(con, auswertung_id: str, zeile: str, karte_id: str | None = ..., preis_de: float | None = ...,
                preis_url: str | None = None) -> None:
    """Karte und/oder Preis einer Zeile von Hand setzen. ... = unverändert lassen, None = zurücksetzen."""
    vorbereiten(con)
    alt = con.execute("SELECT * FROM korrekturen WHERE auswertung = ? AND zeile = ?", (auswertung_id, zeile)).fetchone()
    neu_karte = (alt["karte_id"] if alt else None) if karte_id is ... else karte_id
    neu_preis = (alt["preis_de"] if alt else None) if preis_de is ... else preis_de
    if karte_id is not ... and alt and alt["karte_id"] != karte_id and preis_de is ...:
        neu_preis = None  # andere Karte gewählt: alter Preis gilt nicht mehr
    con.execute("INSERT OR REPLACE INTO korrekturen (auswertung, zeile, karte_id, preis_de, preis_url) "
                "VALUES (?, ?, ?, ?, ?)", (auswertung_id, zeile, neu_karte, neu_preis, preis_url))
    con.commit()


def oeffnen_merken(con, auswertung_id: str, zeile: str) -> None:
    vorbereiten(con)
    con.execute("INSERT OR REPLACE INTO offen (id, auswertung, zeile, zeit) VALUES (1, ?, ?, ?)",
                (auswertung_id, zeile, datetime.now(timezone.utc).isoformat()))
    con.commit()


def preis_uebernehmen(con, url: str, text: str) -> dict:
    """Vom Lesezeichen: Preis aus dem Seitentext lesen und der zuletzt geöffneten Karte zuordnen."""
    vorbereiten(con)
    offen = con.execute("SELECT * FROM offen WHERE id = 1").fetchone()
    if not offen or datetime.fromisoformat(offen["zeit"]) < datetime.now(timezone.utc) - timedelta(hours=2):
        return {"ok": False, "meldung": "Keine Karte ausgewählt. Bitte in der App zuerst bei der Karte auf "
                                        "„Auf Cardmarket öffnen“ klicken."}
    if not preise_de.filter_aktiv(url):
        return {"ok": False, "meldung": "Die Seite ist nicht auf Deutschland / ab Good gefiltert. "
                                        "Bitte das Lesezeichen noch einmal anklicken."}
    preis = preise_de.erster_preis(text)
    if preis is None:
        (config.PROJEKT_DIR / "data" / "letzter_cardmarket_text.txt").write_text(text, encoding="utf-8")
        return {"ok": False, "meldung": "Kein Preis gefunden. Ist das eine Kartenseite mit Angeboten? "
                                        "Du kannst den Preis auch in der App von Hand eintragen."}
    korrigieren(con, offen["auswertung"], offen["zeile"], preis_de=preis, preis_url=url)
    erg = ergebnis(con, offen["auswertung"])
    zeile = next((z for z in erg["zeilen"] if z["zeile"] == offen["zeile"]), None)
    if zeile and len(zeile["karten"]) == 1:
        preise_de.speichern(con, zeile["karten"][0]["id"], preis, url)
    con.execute("DELETE FROM offen")
    con.commit()
    name = zeile["karten"][0]["name"] if zeile and zeile["karten"] else offen["zeile"]
    return {"ok": True, "preis": preis, "karte": name, "auswertung": offen["auswertung"]}
