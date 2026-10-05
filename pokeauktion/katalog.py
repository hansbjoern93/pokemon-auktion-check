"""Abfragen auf die lokale Kartendatenbank (Grundlage für die Erkennung in Schritt 2)."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from urllib.parse import quote_plus

from .db import normalisiere

BILD_QUALITAET = "high"  # TCGdex: "high" oder "low"


@dataclass
class Version:
    bezeichnung: str          # z. B. "Standard", "Reverse Holo / Foil", "1. Edition"
    trend: float | None
    avg30: float | None
    low: float | None


@dataclass
class Karte:
    id: str
    set_id: str
    nummer: str
    name: str
    sprache: str
    set_name: str | None
    kp: int | None
    seltenheit: str | None
    suffix: str | None
    stufe: str | None
    attacken: list[dict] = field(default_factory=list)
    bild_url: str | None = None
    versionen: list[Version] = field(default_factory=list)

    @property
    def preis_min(self) -> float | None:
        werte = [v.trend for v in self.versionen if v.trend is not None]
        return min(werte) if werte else None

    @property
    def preis_max(self) -> float | None:
        werte = [v.trend for v in self.versionen if v.trend is not None]
        return max(werte) if werte else None


def bild_url(bild_basis: str | None, format_: str = "png") -> str | None:
    """Bild-URL nach TCGdex-SDK: {image}/{quality}.{extension}."""
    return f"{bild_basis}/{BILD_QUALITAET}.{format_}" if bild_basis else None


def cardmarket_link(name_en: str, sprache: str = "de") -> str:
    """Link zur Cardmarket-Suche (kein Abruf, nur ein Link)."""
    return f"https://www.cardmarket.com/{sprache}/Pokemon/Products/Search?searchString={quote_plus(name_en)}"


def versionen(con: sqlite3.Connection, karte_id: str) -> list[Version]:
    """Alle Druckversionen mit Cardmarket-Trendpreis.

    Zuordnung der Cardmarket-Felder: ohne Zusatz = Standardversion des Produkts
    (bei Holo-Rares die Holo-Karte), *_holo = Foil-Version (meist Reverse Holo).
    """
    k = con.execute("SELECT varianten, varianten_detail FROM karten WHERE id = ?", (karte_id,)).fetchone()
    varianten = json.loads(k["varianten"]) if k and k["varianten"] else {}
    detail = {d.get("variantId"): d for d in (json.loads(k["varianten_detail"]) if k and k["varianten_detail"] else [])}
    ergebnis: list[Version] = []
    for p in con.execute("SELECT * FROM preise WHERE karte_id = ? ORDER BY variante", (karte_id,)):
        if p["variante"] == "":
            standard = "Holo" if varianten.get("holo") and not varianten.get("normal") else "Standard"
            ergebnis.append(Version(standard, p["trend"], p["avg30"], p["low"]))
            if p["trend_holo"] is not None or varianten.get("reverse"):
                ergebnis.append(Version("Reverse Holo / Foil", p["trend_holo"], p["avg30_holo"], p["low_holo"]))
        else:
            d = detail.get(p["variante"], {})
            teile = [d.get("type"), d.get("subtype"), ", ".join(d.get("stamp") or []) or None]
            name = " ".join(t for t in teile if t) or p["variante"]
            ergebnis.append(Version(name, p["trend"], p["avg30"], p["low"]))
    return ergebnis


def lade_karte(con: sqlite3.Connection, karte_id: str, sprache: str = "de") -> Karte | None:
    t = con.execute(
        """SELECT * FROM karten_texte WHERE karte_id = ?
           ORDER BY sprache != ?, sprache != 'en', sprache LIMIT 1""",
        (karte_id, sprache),
    ).fetchone()
    k = con.execute("SELECT * FROM karten WHERE id = ?", (karte_id,)).fetchone()
    if not t or not k:
        return None
    set_name = con.execute(
        "SELECT name FROM set_namen WHERE set_id = ? ORDER BY sprache != ?, sprache != 'en' LIMIT 1",
        (k["set_id"], t["sprache"]),
    ).fetchone()
    return Karte(
        id=k["id"], set_id=k["set_id"], nummer=k["nummer"], name=t["name"], sprache=t["sprache"],
        set_name=set_name["name"] if set_name else None, kp=k["kp"], seltenheit=k["seltenheit"],
        suffix=k["suffix"], stufe=k["stufe"], attacken=json.loads(t["attacken"] or "[]"),
        bild_url=bild_url(t["bild_basis"]), versionen=versionen(con, k["id"]),
    )


def suche(
    con: sqlite3.Connection,
    name: str | None = None,
    kp: int | None = None,
    attacke: str | None = None,
    sprache: str | None = None,
    limit: int = 50,
) -> list[Karte]:
    """Kandidatensuche über Name (alle Sprachen), KP und Attackenname."""
    bedingungen, werte = [], []
    if name:
        bedingungen.append("t.name_norm LIKE ?")
        werte.append(f"%{normalisiere(name)}%")
    if sprache:
        bedingungen.append("t.sprache = ?")
        werte.append(sprache)
    if kp is not None:
        bedingungen.append("k.kp = ?")
        werte.append(kp)
    if attacke:
        bedingungen.append("EXISTS (SELECT 1 FROM attacken a WHERE a.karte_id = k.id AND a.name_norm LIKE ?)")
        werte.append(f"%{normalisiere(attacke)}%")
    wo = " AND ".join(bedingungen) or "1"
    zeilen = con.execute(
        f"""SELECT k.id, t.sprache FROM karten k JOIN karten_texte t ON t.karte_id = k.id
            WHERE {wo} ORDER BY t.sprache != 'de', t.sprache != 'en', t.sprache""",
        werte,
    ).fetchall()
    # Pro Karte ein Treffer, in der Sprache, in der sie gefunden wurde (bevorzugt Deutsch, dann Englisch).
    sprache_je_karte: dict[str, str] = {}
    for z in zeilen:
        sprache_je_karte.setdefault(z["id"], z["sprache"])
        if len(sprache_je_karte) >= limit:
            break
    return [karte for i, s in sprache_je_karte.items() if (karte := lade_karte(con, i, s))]


def englischer_name(con: sqlite3.Connection, karte_id: str) -> str | None:
    z = con.execute("SELECT name FROM karten_texte WHERE karte_id = ? AND sprache = 'en'", (karte_id,)).fetchone()
    return z["name"] if z else None
