"""Einmaliger Test: echte Cardmarket-Seiten speichern, damit der Preis-Leser nicht geraten werden muss.

Öffnet ein sichtbares Browserfenster (wie ein normaler Besuch), ruft wenige Seiten mit Pausen auf und
speichert jeweils HTML und Screenshot in data/cardmarket_probe/. Erscheint eine Prüfung
("Ich bin kein Roboter") oder ein Cookie-Hinweis, bitte von Hand bestätigen.
Nicht bei Cardmarket anmelden.

  pip install playwright
  python -m playwright install chromium
  python werkzeuge/cardmarket_probe.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ORDNER = Path(__file__).resolve().parent.parent / "data" / "cardmarket_probe"
PROFIL = Path(__file__).resolve().parent.parent / "data" / "browserprofil"

# Mega-Glurak X ex (Fatale Flammen #013), Cardmarket-Produkt 857588 laut TCGdex
VERSUCHE = [
    ("1_idProduct", "https://www.cardmarket.com/de/Pokemon/Products?idProduct=857588"),
    ("2_suche", "https://www.cardmarket.com/de/Pokemon/Products/Search?searchString=Mega+Charizard+X+ex"),
]
FILTER = "sellerCountry=7&minCondition=4"  # 7 = Deutschland, 4 = Good oder besser (laut Cardmarket-Filter)


def warte_auf_seite(seite, sekunden: int = 60) -> None:
    """Wartet, bis keine Prüfseite mehr angezeigt wird (die bestätigst du von Hand)."""
    ende = time.time() + sekunden
    while time.time() < ende:
        titel = seite.title().lower()
        if not any(w in titel for w in ("just a moment", "einen moment", "attention required")):
            return
        print("  Prüfseite erkannt - bitte im Browserfenster bestätigen ...")
        seite.wait_for_timeout(3000)


def speichere(seite, name: str) -> None:
    warte_auf_seite(seite)
    seite.wait_for_timeout(3000)
    (ORDNER / f"{name}.html").write_text(seite.content(), encoding="utf-8")
    seite.screenshot(path=str(ORDNER / f"{name}.png"), full_page=True)
    print(f"  gespeichert: {name}  |  Titel: {seite.title()!r}  |  Adresse: {seite.url}")


def main() -> None:
    ORDNER.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch_persistent_context(str(PROFIL), headless=False, locale="de-DE")
        seite = browser.pages[0] if browser.pages else browser.new_page()
        produktseite = None
        for name, url in VERSUCHE:
            print(f"Öffne {url}")
            antwort = seite.goto(url, wait_until="domcontentloaded")
            print(f"  HTTP {antwort.status if antwort else '?'}")
            speichere(seite, name)
            if "/Products/Singles/" in seite.url and not produktseite:
                produktseite = seite.url.split("?")[0]
            seite.wait_for_timeout(4000)  # Pause zwischen den Aufrufen
        if not produktseite:
            print("\nKeine Kartenseite gefunden. Bitte im Browserfenster die Karte 'Mega-Glurak X ex' aus "
                  "'Fatale Flammen' anklicken und danach hier Enter drücken.")
            input()
            produktseite = seite.url.split("?")[0]
        url = f"{produktseite}?{FILTER}"
        print(f"Öffne {url}")
        seite.goto(url, wait_until="domcontentloaded")
        speichere(seite, "3_gefiltert")
        browser.close()
    print(f"\nFertig. Die Dateien liegen in: {ORDNER}")
    print("Bitte den Ordner 'cardmarket_probe' auf GitHub in den Ordner 'proben' hochladen.")


if __name__ == "__main__":
    sys.exit(main())
