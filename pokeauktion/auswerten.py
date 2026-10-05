"""Trefferquote der Erkennung an den Testfotos messen.

  python -m pokeauktion.auswerten                 # testfotos/ mit testfotos/erwartet.txt
  python -m pokeauktion.auswerten --claude unsicher

erwartet.txt: eine Zeile pro Karte "datei; reihe-spalte; name; sprache; hinweis".
"""
from __future__ import annotations

import argparse
import re
from collections import Counter, defaultdict
from pathlib import Path

import cv2
import numpy as np
from rapidfuzz import fuzz

from . import config
from .analyse import Einstellungen, analysiere_foto
from .db import normalisiere, verbinde
from .erkennung import Indizes


def lese_erwartet(datei: Path) -> dict[str, dict[str, dict]]:
    erwartet: dict[str, dict[str, dict]] = defaultdict(dict)
    for zeile in datei.read_text(encoding="utf-8").splitlines():
        zeile = zeile.strip()
        if not zeile or zeile.startswith("#"):
            continue
        teile = [t.strip() for t in zeile.split(";")] + [""] * 5
        erwartet[teile[0]][teile[1]] = {"name": teile[2], "sprache": teile[3].lower(), "hinweis": teile[4]}
    return erwartet


def _namensvarianten(name: str) -> list[str]:
    """'月亮伊布 (Umbreon)' -> ['月亮伊布', 'umbreon']"""
    teile = [name] + re.findall(r"\(([^)]*)\)", name) + [re.sub(r"\([^)]*\)", "", name)]
    return [n for t in teile if (n := normalisiere(t).replace(" ", ""))]


def name_passt(con, karten_ids: list[str], erwarteter_name: str) -> bool:
    gesucht = _namensvarianten(erwarteter_name)
    for karte_id in karten_ids:
        for z in con.execute("SELECT name_norm FROM karten_texte WHERE karte_id = ?", (karte_id,)):
            n = z["name_norm"].replace(" ", "")
            if any(fuzz.ratio(n, g) >= 88 for g in gesucht):
                return True
    return False


def sprache_passt(erkannt: str | None, erwartet: str) -> bool:
    if not erkannt or not erwartet:
        return False
    if erkannt.startswith("zh") and erwartet.startswith("zh"):
        return True  # vereinfacht/traditionell ist auf dem Foto oft nicht zu unterscheiden
    return erkannt == erwartet


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description="Trefferquote an Testfotos messen")
    p.add_argument("ordner", nargs="?", type=Path, default=config.PROJEKT_DIR / "testfotos")
    p.add_argument("--claude", choices=["aus", "unsicher", "immer"], default="aus")
    args = p.parse_args(argv)

    ein = config.lade()
    con = verbinde(ein.db_pfad)
    indizes = Indizes.laden(con)
    erwartet = lese_erwartet(args.ordner / "erwartet.txt")
    einstellungen = Einstellungen(claude_modus=args.claude, claude_modell=ein.claude_modell,
                                  claude_befehl=ein.claude_befehl, bilder_ordner=ein.db_pfad.parent / "bilder")

    zaehler = Counter()
    sicherheit_richtig: Counter = Counter()
    sicherheit_gesamt: Counter = Counter()
    fehler: list[str] = []
    for datei, karten in sorted(erwartet.items()):
        bild = cv2.imdecode(np.fromfile(args.ordner / datei, dtype=np.uint8), cv2.IMREAD_COLOR)
        foto = analysiere_foto(con, indizes, bild, None, einstellungen)
        gefunden = {f"{k.ausschnitt.reihe}-{k.ausschnitt.spalte}": k for k in foto.karten}
        zaehler["zusaetzlich"] += len(set(gefunden) - set(karten))
        for pos, soll in karten.items():
            zaehler["erwartet"] += 1
            k = gefunden.get(pos)
            if not k:
                fehler.append(f"{datei} {pos}: {soll['name']} – nicht ausgeschnitten")
                continue
            zaehler["ausgeschnitten"] += 1
            e = k.ergebnis
            ids = [c.id for c in e.karten]
            ok_name = bool(ids) and name_passt(con, ids[:1], soll["name"])
            ok_sprache = sprache_passt(e.sprache, soll["sprache"])
            zaehler["erkannt"] += bool(ids)
            zaehler["name"] += ok_name
            zaehler["sprache"] += ok_name and ok_sprache
            sicherheit_gesamt[e.sicherheit] += 1
            sicherheit_richtig[e.sicherheit] += ok_name and ok_sprache
            if ok_name and len(ids) > 1:
                zaehler["mehrere_drucke"] += 1
            if not (ok_name and ok_sprache):
                was = f"{e.karten[0].name} [{e.sprache}]" if e.karten else "nicht erkannt"
                fehler.append(f"{datei} {pos}: erwartet {soll['name']} [{soll['sprache']}], erkannt {was}"
                              f" (Sicherheit {e.sicherheit}{', Claude' if e.quelle == 'claude' else ''})")

    n = zaehler["erwartet"] or 1
    print(f"\nTestfotos: {len(erwartet)}, erwartete Karten: {zaehler['erwartet']}")
    print(f"Karten ausgeschnitten:          {zaehler['ausgeschnitten']}/{n} ({100 * zaehler['ausgeschnitten'] / n:.0f} %)"
          f"  (+{zaehler['zusaetzlich']} zusätzliche Ausschnitte)")
    print(f"Einer Karte zugeordnet:         {zaehler['erkannt']}/{n} ({100 * zaehler['erkannt'] / n:.0f} %)")
    print(f"Richtige Karte (Name):          {zaehler['name']}/{n} ({100 * zaehler['name'] / n:.0f} %)")
    print(f"Richtige Karte und Sprache:     {zaehler['sprache']}/{n} ({100 * zaehler['sprache'] / n:.0f} %)")
    print(f"  davon mehrere mögliche Drucke (Preisspanne): {zaehler['mehrere_drucke']}")
    print("Sicherheit (richtig/gesamt):    " + ", ".join(
        f"{s} {sicherheit_richtig[s]}/{sicherheit_gesamt[s]}"
        for s in ("hoch", "mittel", "niedrig", "nicht erkannt") if sicherheit_gesamt[s]))
    if fehler:
        print("\nAbweichungen:")
        for f in fehler:
            print("  " + f)


if __name__ == "__main__":
    main()
