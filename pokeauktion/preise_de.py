"""Genaue Preise aus Deutschland: werden über das Lesezeichen "Preis an App" aus der Cardmarket-Seite
übernommen, die du selbst in deinem Browser geöffnet hast (kein automatischer Abruf).

Die Cardmarket-Seite wird mit den Filtern sellerCountry=7 (Deutschland) und minCondition=4
(Zustand Good oder besser) geöffnet. Die Angebote sind nach Preis aufsteigend sortiert, das erste
Angebot unter der Tabellenüberschrift ist also das billigste passende.
"""
from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, quote_plus, urlparse

FILTER = {"sellerCountry": "7", "minCondition": "4"}  # 7 = Deutschland, 4 = Good oder besser
GUELTIG_TAGE = 3

SCHEMA = """
CREATE TABLE IF NOT EXISTS de_preise (
    karte_id  TEXT PRIMARY KEY,
    preis     REAL NOT NULL,
    url       TEXT,
    zeit      TEXT NOT NULL
);
"""

# Spaltenüberschrift der Angebotstabelle (Deutsch/Englisch). Bewusst nicht "Angebot" oder "Verkäufer":
# "Angebote zeigen" und "Verkäuferstatus" stehen schon oberhalb der Tabelle, vor dem "ab"-Preis.
TABELLENKOPF = ("Produktinfo", "Product Information")
PREIS = re.compile(r"(\d{1,3}(?:[.\s]\d{3})*,\d{2}|\d+,\d{2}|\d{1,3}(?:,\d{3})*\.\d{2})\s*€")


def zahl(text: str) -> float:
    text = text.replace(" ", "")
    if "," in text and (text.rfind(",") > text.rfind(".")):
        text = text.replace(".", "").replace(",", ".")  # 1.013,10 -> 1013.10
    else:
        text = text.replace(",", "")                    # 1,013.10 -> 1013.10
    return float(text)


def erster_preis(text: str) -> float | None:
    """Erster Angebotspreis unter dem Tabellenkopf im Seitentext."""
    start = -1
    for kopf in TABELLENKOPF:
        if (i := text.find(kopf)) >= 0 and (start < 0 or i < start):
            start = i
    if start < 0:
        return None
    treffer = PREIS.search(text, start)
    return zahl(treffer.group(1)) if treffer else None


def filter_aktiv(url: str) -> bool:
    q = parse_qs(urlparse(url).query)
    return all(q.get(k, [""])[0] == v for k, v in FILTER.items())


def suchlink(name_en: str) -> str:
    filter_ = "&".join(f"{k}={v}" for k, v in FILTER.items())
    return f"https://www.cardmarket.com/de/Pokemon/Products/Search?searchString={quote_plus(name_en)}&{filter_}"


def speichern(con: sqlite3.Connection, karte_id: str, preis: float, url: str | None) -> None:
    con.executescript(SCHEMA)
    con.execute("INSERT OR REPLACE INTO de_preise (karte_id, preis, url, zeit) VALUES (?, ?, ?, ?)",
                (karte_id, preis, url, datetime.now(timezone.utc).isoformat()))


def gespeichert(con: sqlite3.Connection, karte_id: str) -> float | None:
    con.executescript(SCHEMA)
    z = con.execute("SELECT preis, zeit FROM de_preise WHERE karte_id = ?", (karte_id,)).fetchone()
    if not z:
        return None
    if datetime.fromisoformat(z["zeit"]) < datetime.now(timezone.utc) - timedelta(days=GUELTIG_TAGE):
        return None
    return z["preis"]


def lesezeichen(app_url: str) -> str:
    """JavaScript für das Lesezeichen. Läuft nur, wenn du es auf der geöffneten Cardmarket-Seite anklickst.

    1. Klick ohne Filter: setzt die Filter Deutschland / ab Good und lädt die Seite neu.
    2. Klick mit Filter: schickt Adresse und den Text ab der Angebotstabelle an die App.
    """
    filter_js = ";".join(f"u.searchParams.set('{k}','{v}')" for k, v in FILTER.items())
    pruefung = "||".join(f"u.searchParams.get('{k}')!=='{v}'" for k, v in FILTER.items())
    koepfe = ",".join(f"'{k}'" for k in TABELLENKOPF)
    js = (
        "(()=>{const u=new URL(location.href);"
        "if(!/cardmarket\\.com$/.test(u.hostname)){alert('Bitte auf einer Cardmarket-Kartenseite anklicken.');return}"
        f"if({pruefung}){{{filter_js};"
        "alert('Filter Deutschland / ab Good wird gesetzt. Nach dem Laden bitte noch einmal klicken.');"
        "location.href=u.toString();return}"
        "const t=document.body.innerText;let i=-1;"
        f"for(const k of [{koepfe}]){{const j=t.indexOf(k);if(j>=0&&(i<0||j<i))i=j}}"
        "const a=t.slice(Math.max(0,i),Math.max(0,i)+1200);"
        f"window.open('{app_url}/uebernehmen?url='+encodeURIComponent(location.href)+'&text='+encodeURIComponent(a),'_blank')"
        "})()"
    )
    return "javascript:" + js
