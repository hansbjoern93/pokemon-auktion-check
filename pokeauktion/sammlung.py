"""Auswertungen speichern und pro Karte den festen Wert berechnen.

Fester Wert je Karte (in dieser Reihenfolge):
1. Von Hand eingetragener Preis.
2. Preis aus Deutschland (über die Browser-Erweiterung geholt); bei mehreren möglichen Drucken
   der billigste davon.
3. Sonst vorläufig: der billigste "ab"-Preis (Cardmarket, alle Länder) aller möglichen Drucke;
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
-- Aufträge für die Browser-Erweiterung: pro Karte und möglichem Druck eine Cardmarket-Seite
CREATE TABLE IF NOT EXISTS auftraege (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    auswertung TEXT NOT NULL,
    karte_id   TEXT NOT NULL,
    status     TEXT NOT NULL DEFAULT 'offen',   -- offen / fertig / fehler
    meldung    TEXT,
    zeit       TEXT NOT NULL
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
    zeilen = []
    for z in json.loads(a["zeilen"]):
        kor = korrektur.get(z["zeile"])
        sprache = z["sprache"] or "de"
        ids = [kor["karte_id"]] if kor and kor["karte_id"] else z["kandidaten"]
        karten = [k for i in ids if (k := lade_karte(con, i, sprache))]
        wert, quelle, de_vollstaendig = None, "keiner", True
        if kor and kor["preis_de"] is not None:
            wert, quelle = kor["preis_de"], "hand"
        elif karten:
            de = [preise_de.gespeichert(con, k.id) for k in karten]
            if any(p is not None for p in de):
                wert, quelle = min(p for p in de if p is not None), "de"
                de_vollstaendig = all(p is not None for p in de)
        if wert is None:
            wert, quelle = _billigster(karten)
        name_en = _englischer_name(con, karten[0].id) if karten else ""
        zeilen.append({
            **{k: z[k] for k in ("zeile", "foto", "nr", "box", "sicherheit", "begruendung", "quelle")},
            "sprache": sprache if karten else None,
            "erkannt": bool(karten),
            "von_hand": bool(kor and kor["karte_id"]),
            "karten": [_karte_json(k) for k in karten],
            "alternativen": [] if kor and kor["karte_id"] else [
                _karte_json(k) for i in z.get("alternativen", []) if (k := lade_karte(con, i, sprache))],
            "wert": wert, "wert_quelle": quelle,
            "de_vollstaendig": de_vollstaendig,
            "cardmarket": preise_de.suchlink(name_en) if karten else None,
        })
    zeilen.sort(key=lambda z: -(z["wert"] or 0))
    erkannt = [z for z in zeilen if z["erkannt"]]
    return {
        "id": a["id"], "name": a["name"], "fotos": json.loads(a["fotos"]), "zeilen": zeilen,
        "summe": round(sum(z["wert"] or 0 for z in erkannt), 2),
        "anzahl": len(zeilen), "nicht_erkannt": len(zeilen) - len(erkannt),
        "mit_de_preis": sum(z["wert_quelle"] in ("de", "hand") for z in erkannt),
        "auftraege": dict(con.execute("SELECT status, COUNT(*) FROM auftraege WHERE auswertung = ? GROUP BY status",
                                      (auswertung_id,)).fetchall()),
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


def _englischer_name(con, karte_id: str) -> str:
    """Name für die Cardmarket-Suche. Asiatische Karten haben keinen englischen Text: dann den englischen
    Namen einer Karte mit derselben Pokédex-Nummer und demselben Suffix nehmen (ブラッキー -> Umbreon)."""
    if name := englischer_name(con, karte_id):
        return name
    k = con.execute("SELECT dex_ids, suffix FROM karten WHERE id = ?", (karte_id,)).fetchone()
    if k and k["dex_ids"]:
        z = con.execute(
            """SELECT t.name FROM karten k JOIN karten_texte t ON t.karte_id = k.id AND t.sprache = 'en'
               WHERE k.dex_ids = ? AND COALESCE(k.suffix, '') = COALESCE(?, '')
               GROUP BY t.name ORDER BY COUNT(*) DESC LIMIT 1""", (k["dex_ids"], k["suffix"])).fetchone()
        if z:
            return z["name"]
    z = con.execute("SELECT name FROM karten_texte WHERE karte_id = ? LIMIT 1", (karte_id,)).fetchone()
    return z["name"] if z else karte_id


def _auftrag_url(con, auftrag_id: int, karte_id: str) -> str:
    return f"{preise_de.suchlink(_englischer_name(con, karte_id))}#pka={auftrag_id}"


def auftraege_anlegen(con, auswertung_id: str, app_url: str) -> dict:
    """Für alle erkannten Karten ohne aktuellen Preis aus Deutschland je einen Auftrag anlegen.
    Gibt die erste Cardmarket-Adresse zurück (die Erweiterung arbeitet sich von dort durch)."""
    vorbereiten(con)
    erg = ergebnis(con, auswertung_id)
    con.execute("DELETE FROM auftraege WHERE auswertung = ? AND status = 'offen'", (auswertung_id,))
    ids: list[str] = []
    for z in erg["zeilen"]:
        if z["wert_quelle"] == "hand":
            continue
        for k in z["karten"]:
            if k["id"] not in ids and preise_de.gespeichert(con, k["id"]) is None:
                ids.append(k["id"])
    jetzt = datetime.now(timezone.utc).isoformat()
    for karte_id in ids:
        con.execute("INSERT INTO auftraege (auswertung, karte_id, zeit) VALUES (?, ?, ?)", (auswertung_id, karte_id, jetzt))
    con.commit()
    return {"anzahl": len(ids), "erste_url": naechste_url(con, auswertung_id), "app_url": f"{app_url}/#{auswertung_id}"}


def naechste_url(con, auswertung_id: str) -> str | None:
    z = con.execute("SELECT id, karte_id FROM auftraege WHERE auswertung = ? AND status = 'offen' ORDER BY id LIMIT 1",
                    (auswertung_id,)).fetchone()
    return _auftrag_url(con, z["id"], z["karte_id"]) if z else None


def auftrag(con, auftrag_id: int) -> dict | None:
    vorbereiten(con)
    z = con.execute("SELECT * FROM auftraege WHERE id = ?", (auftrag_id,)).fetchone()
    if not z:
        return None
    k = con.execute("SELECT k.nummer, s.kuerzel FROM karten k LEFT JOIN sets s ON s.id = k.set_id WHERE k.id = ?",
                    (z["karte_id"],)).fetchone()
    offen = con.execute("SELECT COUNT(*) FROM auftraege WHERE auswertung = ? AND status = 'offen'",
                        (z["auswertung"],)).fetchone()[0]
    return {"id": z["id"], "karte_id": z["karte_id"], "status": z["status"], "offen": offen,
            "name": _englischer_name(con, z["karte_id"]),
            "kuerzel": (k["kuerzel"] if k else None) or "", "nummer": (k["nummer"] if k else "") or ""}


def auftrag_ergebnis(con, auftrag_id: int, url: str, text: str, fehler: str | None, app_url: str) -> dict:
    """Ergebnis der Erweiterung speichern und die nächste Adresse zurückgeben."""
    vorbereiten(con)
    z = con.execute("SELECT * FROM auftraege WHERE id = ?", (auftrag_id,)).fetchone()
    if not z:
        return {"fehler": "Auftrag unbekannt"}
    preis = None
    if not fehler:
        if not preise_de.filter_aktiv(url):
            fehler = "Seite nicht auf Deutschland / ab Good gefiltert"
        elif (preis := preise_de.erster_preis(text)) is None:
            fehler = "Kein Angebot aus Deutschland ab Good gefunden"
            (config.PROJEKT_DIR / "data" / "letzter_cardmarket_text.txt").write_text(text, encoding="utf-8")
    if preis is not None:
        preise_de.speichern(con, z["karte_id"], preis, url)
        con.execute("UPDATE auftraege SET status = 'fertig', meldung = ? WHERE id = ?", (f"{preis:.2f} €", auftrag_id))
    else:
        con.execute("UPDATE auftraege SET status = 'fehler', meldung = ? WHERE id = ?", (fehler, auftrag_id))
    con.commit()
    return {"preis": preis, "fehler": fehler, "naechste_url": naechste_url(con, z["auswertung"]),
            "app_url": f"{app_url}/#{z['auswertung']}"}


def auftraege_liste(con, auswertung_id: str) -> list[dict]:
    vorbereiten(con)
    return [{"karte_id": z["karte_id"], "status": z["status"], "meldung": z["meldung"]} for z in con.execute(
        "SELECT * FROM auftraege WHERE auswertung = ? ORDER BY id", (auswertung_id,))]
