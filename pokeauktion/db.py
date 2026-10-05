"""SQLite-Datenbank: Schema, Verbindung und Hilfsfunktionen."""
from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from pathlib import Path

SCHEMA = """
PRAGMA journal_mode = WAL;

-- Ein Set (Erweiterung). IDs sind sprachübergreifend gleich für internationale Sets;
-- asiatische Sets haben eigene IDs.
CREATE TABLE IF NOT EXISTS sets (
    id              TEXT PRIMARY KEY,
    serie_id        TEXT,
    serie_name      TEXT,
    release_date    TEXT,
    karten_offiziell INTEGER,
    karten_gesamt   INTEGER,
    kuerzel         TEXT,
    cardmarket_id   INTEGER
);

CREATE TABLE IF NOT EXISTS set_namen (
    set_id  TEXT NOT NULL,
    sprache TEXT NOT NULL,
    name    TEXT NOT NULL,
    PRIMARY KEY (set_id, sprache)
);

-- Sprachunabhängige Kartendaten (aus der ersten verfügbaren Sprache, bevorzugt Englisch).
CREATE TABLE IF NOT EXISTS karten (
    id              TEXT PRIMARY KEY,      -- TCGdex-ID, z. B. "swsh3-136"
    set_id          TEXT NOT NULL,
    nummer          TEXT NOT NULL,         -- localId, z. B. "136"
    kategorie       TEXT,                  -- Pokemon / Trainer / Energy
    kp              INTEGER,
    typen           TEXT,                  -- JSON-Liste
    stufe           TEXT,                  -- Basic, Stage1, VMAX, ...
    suffix          TEXT,                  -- EX, GX, V, ...
    seltenheit      TEXT,
    illustrator     TEXT,
    dex_ids         TEXT,                  -- JSON-Liste
    regulation_mark TEXT,
    trainer_typ     TEXT,
    energie_typ     TEXT,
    rueckzug        INTEGER,
    varianten       TEXT,                  -- JSON: {"normal":..,"reverse":..,"holo":..,"firstEdition":..}
    varianten_detail TEXT,                 -- JSON-Liste aus variants_detailed
    cardmarket_id   INTEGER,               -- thirdParty.cardmarket (Cardmarket idProduct)
    detail_geladen  INTEGER NOT NULL DEFAULT 0,  -- 1 = REST-Detail (Varianten/Preis) geladen
    aktualisiert    TEXT
);
CREATE INDEX IF NOT EXISTS idx_karten_set ON karten(set_id, nummer);
CREATE INDEX IF NOT EXISTS idx_karten_kp ON karten(kp);
CREATE INDEX IF NOT EXISTS idx_karten_dex ON karten(dex_ids);

-- Sprachabhängige Texte: Name, Attacken, Fähigkeiten, Referenzbild.
CREATE TABLE IF NOT EXISTS karten_texte (
    karte_id     TEXT NOT NULL,
    sprache      TEXT NOT NULL,
    name         TEXT NOT NULL,
    name_norm    TEXT NOT NULL,
    bild_basis   TEXT,                     -- + "/high.png" bzw. "/low.webp"
    attacken     TEXT,                     -- JSON-Liste {name, cost, damage, effect}
    faehigkeiten TEXT,                     -- JSON-Liste {name, type, effect}
    effekt       TEXT,
    PRIMARY KEY (karte_id, sprache)
);
CREATE INDEX IF NOT EXISTS idx_texte_name ON karten_texte(name_norm);

-- Attacken einzeln, damit man über Attackenname + Schaden suchen kann.
CREATE TABLE IF NOT EXISTS attacken (
    karte_id  TEXT NOT NULL,
    sprache   TEXT NOT NULL,
    pos       INTEGER NOT NULL,
    name      TEXT NOT NULL,
    name_norm TEXT NOT NULL,
    schaden   TEXT,
    PRIMARY KEY (karte_id, sprache, pos)
);
CREATE INDEX IF NOT EXISTS idx_attacken_name ON attacken(name_norm);

-- Cardmarket-Preise laut TCGdex (EUR). variante = '' für die Karte selbst,
-- sonst die variantId aus variants_detailed (z. B. eigene Produkte für 1. Edition).
-- Cardmarket-Logik: die Felder ohne Zusatz gelten für die Standardversion des Produkts
-- (bei Holo-Rares ist das die Holo-Karte), die *_holo-Felder für die Foil-Version
-- (meist Reverse Holo).
CREATE TABLE IF NOT EXISTS preise (
    karte_id    TEXT NOT NULL,
    variante    TEXT NOT NULL DEFAULT '',
    id_produkt  INTEGER,
    avg REAL, low REAL, trend REAL, avg1 REAL, avg7 REAL, avg30 REAL,
    avg_holo REAL, low_holo REAL, trend_holo REAL, avg1_holo REAL, avg7_holo REAL, avg30_holo REAL,
    stand       TEXT,                      -- pricing.cardmarket.updated
    abgerufen   TEXT NOT NULL,
    PRIMARY KEY (karte_id, variante)
);

-- Täglicher Verlauf des Trendpreises.
CREATE TABLE IF NOT EXISTS preis_verlauf (
    karte_id   TEXT NOT NULL,
    variante   TEXT NOT NULL DEFAULT '',
    datum      TEXT NOT NULL,
    trend      REAL,
    trend_holo REAL,
    PRIMARY KEY (karte_id, variante, datum)
);

CREATE TABLE IF NOT EXISTS meta (
    schluessel TEXT PRIMARY KEY,
    wert       TEXT
);
"""

# Felder in pricing.cardmarket (Quelle: tcgdex/cards-database, server/src/libs/providers/cardmarket.ts)
PREISFELDER = {
    "avg": "avg", "low": "low", "trend": "trend", "avg1": "avg1", "avg7": "avg7", "avg30": "avg30",
    "avg-holo": "avg_holo", "low-holo": "low_holo", "trend-holo": "trend_holo",
    "avg1-holo": "avg1_holo", "avg7-holo": "avg7_holo", "avg30-holo": "avg30_holo",
}


def verbinde(pfad: Path | str) -> sqlite3.Connection:
    pfad = Path(pfad)
    pfad.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(pfad)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


def normalisiere(text: str | None) -> str:
    """Kleinbuchstaben, ohne Akzente und Satzzeichen: "Pokémon-ex" -> "pokemon ex".

    Nicht-lateinische Schriften (Japanisch, Koreanisch, Chinesisch, Thai) bleiben erhalten.
    """
    if not text:
        return ""
    # Akzente nur bei lateinischen Buchstaben entfernen (é -> e), nicht z. B. das
    # japanische Dakuten (ド bleibt ド).
    zerlegt = unicodedata.normalize("NFKD", text)
    behalten: list[str] = []
    for z in zerlegt:
        if unicodedata.combining(z) and behalten and behalten[-1] < "\u0250":
            continue
        behalten.append(z)
    text = unicodedata.normalize("NFKC", "".join(behalten))
    text = text.casefold().replace("♀", " w ").replace("♂", " m ")
    text = re.sub(r"[^\w]+", " ", text)
    return " ".join(text.split())


def als_json(wert) -> str | None:
    if wert is None:
        return None
    return json.dumps(wert, ensure_ascii=False)


def meta_lesen(con: sqlite3.Connection, schluessel: str) -> str | None:
    zeile = con.execute("SELECT wert FROM meta WHERE schluessel = ?", (schluessel,)).fetchone()
    return zeile["wert"] if zeile else None


def meta_schreiben(con: sqlite3.Connection, schluessel: str, wert: str) -> None:
    con.execute(
        "INSERT INTO meta (schluessel, wert) VALUES (?, ?) "
        "ON CONFLICT(schluessel) DO UPDATE SET wert = excluded.wert",
        (schluessel, wert),
    )
