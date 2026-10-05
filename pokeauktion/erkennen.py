"""Foto rein, Kartenliste mit Preisen raus.

  python -m pokeauktion.erkennen testfotos/foto1.webp
  python -m pokeauktion.erkennen foto.jpg --hinweis "Pokemon Sammlung deutsch Holo" --claude unsicher
  python -m pokeauktion.erkennen foto.jpg --rahmen ausgabe/   # Foto mit nummerierten Rahmen speichern
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2
import numpy as np

from . import config
from .analyse import Einstellungen, FotoErgebnis, analysiere_foto
from .db import verbinde
from .erkennung import Indizes
from .katalog import Karte, cardmarket_link, englischer_name
from .zuschnitt import zeichne

SPRACHNAMEN = {"de": "Deutsch", "en": "Englisch", "fr": "Französisch", "it": "Italienisch", "es": "Spanisch",
               "pt": "Portugiesisch", "ja": "Japanisch", "ko": "Koreanisch", "zh-cn": "Chinesisch (vereinfacht)",
               "zh-tw": "Chinesisch (traditionell)", "id": "Indonesisch", "th": "Thailändisch"}


def euro(wert: float | None) -> str:
    if wert is None:
        return "–"
    return f"{wert:,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")


def spanne(lo: float | None, hi: float | None) -> str:
    if lo is None and hi is None:
        return "kein Preis"
    if lo == hi or hi is None:
        return euro(lo)
    return f"{euro(lo)} – {euro(hi)}"


def _version_text(karten: list[Karte]) -> str:
    if len(karten) == 1:
        k = karten[0]
        versionen = " / ".join(v.bezeichnung for v in k.versionen) or (k.seltenheit or "?")
        return f"{k.set_name or k.set_id} #{k.nummer} · {versionen}"
    teile = [f"{k.set_id} #{k.nummer} ({k.seltenheit or '?'})" for k in karten[:6]]
    rest = f" + {len(karten) - 6} weitere" if len(karten) > 6 else ""
    return f"{len(karten)} mögliche Drucke: " + ", ".join(teile) + rest


def ausgabe(con, name: str, foto: FotoErgebnis) -> tuple[float, float, int, int]:
    print(f"\n=== {name}: {len(foto.karten)} Karten gefunden ===")
    zeilen = sorted(foto.karten, key=lambda k: -(k.ergebnis.preis_max or 0))
    summe_min = summe_max = 0.0
    ohne = ohne_preis = 0
    for k in zeilen:
        e, a = k.ergebnis, k.ausschnitt
        pos = f"{a.nr:>2}" + (f" (R{a.reihe}/S{a.spalte})" if a.reihe else "")
        if not e.karten:
            ohne += 1
            print(f"{pos}  NICHT ERKANNT – {e.begruendung}")
            continue
        haupt = e.karten[0]
        ohne_preis += e.preis_max is None
        summe_min += e.preis_min or 0
        summe_max += e.preis_max or 0
        print(f"{pos}  {haupt.name} [{SPRACHNAMEN.get(e.sprache, e.sprache)}]  Sicherheit: {e.sicherheit}"
              f"{'  (Claude)' if e.quelle == 'claude' else ''}")
        print(f"      {_version_text(e.karten)}")
        print(f"      Preis: {spanne(e.preis_min, e.preis_max)}"
              f"   Cardmarket: {cardmarket_link(englischer_name(con, haupt.id) or haupt.name)}")
        alt = e.guenstigste_alternative
        if alt and e.preis_max and alt.preis_min is not None and e.preis_max >= 5:
            print(f"      Zum Vergleich, günstigster Druck mit gleichem Text: {alt.set_id} #{alt.nummer}"
                  f" ({alt.seltenheit}) {euro(alt.preis_min)}")
        print(f"      Grund: {e.begruendung}")
    return summe_min, summe_max, ohne, ohne_preis


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description="Pokémon-Karten auf Fotos erkennen und bewerten")
    p.add_argument("bilder", nargs="+", type=Path)
    p.add_argument("--hinweis", help="Auktionstitel/-beschreibung als Zusatzhinweis (z. B. Sprache)")
    p.add_argument("--claude", choices=["aus", "unsicher", "immer"], help="Claude Code (Abo) für unsichere Karten")
    p.add_argument("--rahmen", type=Path, help="Ordner für Fotos mit nummerierten Rahmen")
    args = p.parse_args(argv)

    ein = config.lade()
    con = verbinde(ein.db_pfad)
    if con.execute("SELECT COUNT(*) FROM karten").fetchone()[0] == 0:
        sys.exit("Die Kartendatenbank ist leer. Bitte zuerst: python -m pokeauktion.sync")
    indizes = Indizes.laden(con)
    einstellungen = Einstellungen(
        claude_modus=args.claude or ein.erkennung_claude, claude_modell=ein.claude_modell,
        claude_befehl=ein.claude_befehl, bilder_ordner=ein.db_pfad.parent / "bilder")

    gesamt_min = gesamt_max = 0.0
    gesamt_karten = gesamt_ohne = gesamt_ohne_preis = 0
    for pfad in args.bilder:
        bild = cv2.imdecode(np.fromfile(pfad, dtype=np.uint8), cv2.IMREAD_COLOR)
        if bild is None:
            print(f"{pfad}: kein lesbares Bild")
            continue
        foto = analysiere_foto(con, indizes, bild, args.hinweis, einstellungen)
        lo, hi, ohne, ohne_preis = ausgabe(con, pfad.name, foto)
        gesamt_ohne_preis += ohne_preis
        gesamt_min += lo
        gesamt_max += hi
        gesamt_ohne += ohne
        gesamt_karten += len(foto.karten)
        if args.rahmen:
            args.rahmen.mkdir(parents=True, exist_ok=True)
            cv2.imwrite(str(args.rahmen / f"{pfad.stem}_rahmen.jpg"), zeichne(bild, [k.ausschnitt for k in foto.karten]))

    erkannt = gesamt_karten - gesamt_ohne
    mit_preis = erkannt - gesamt_ohne_preis
    summe = spanne(gesamt_min, gesamt_max) if mit_preis else "keine Preise vorhanden"
    print(f"\nSumme: {summe} für {mit_preis} Karten mit Preis"
          f" ({gesamt_ohne_preis} erkannte Karten ohne Preis); {gesamt_ohne} von {gesamt_karten} nicht erkannt.")
    print("Hinweis: Cardmarket-Trendpreise für gut erhaltene Karten. Der Zustand beeinflusst den Preis stark.")


if __name__ == "__main__":
    main()
