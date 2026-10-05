"""Ein Foto komplett auswerten: Karten ausschneiden, erkennen, optional von Claude nachlesen lassen."""
from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from . import claude_code
from .bildvergleich import Referenzbilder
from .erkennung import Ergebnis, Indizes, erkenne_aus_merkmalen, lese_text, merkmale_aus, sprachen_aus_hinweis
from .zuschnitt import Ausschnitt, finde_karten

log = logging.getLogger(__name__)

RANG = {"hoch": 3, "mittel": 2, "niedrig": 1, "nicht erkannt": 0}


@dataclass
class KartenErgebnis:
    ausschnitt: Ausschnitt
    ergebnis: Ergebnis


@dataclass
class FotoErgebnis:
    karten: list[KartenErgebnis] = field(default_factory=list)


@dataclass
class Einstellungen:
    claude_modus: str = "aus"          # aus / unsicher / immer
    claude_modell: str = "sonnet"
    claude_befehl: str = "claude"
    bilder_ordner: Path | None = None  # Cache für Referenzbilder; None = kein Bildvergleich


def _claude_lohnt(e: Ergebnis, modus: str) -> bool:
    if modus == "immer":
        return True
    if modus != "unsicher":
        return False
    if e.sicherheit in ("niedrig", "nicht erkannt"):
        return True
    # Mehrere Drucke mit deutlich unterschiedlichem Preis (z. B. normal vs. Shiny): Claude soll die Version prüfen
    if len(e.karten) > 1 and e.preis_max and e.preis_min is not None:
        return e.preis_max >= 5 and e.preis_max >= 2 * max(e.preis_min, 0.5)
    return False


def analysiere_foto(
    con: sqlite3.Connection,
    indizes: Indizes,
    bild: np.ndarray,
    hinweis: str | None = None,
    einstellungen: Einstellungen | None = None,
) -> FotoErgebnis:
    ein = einstellungen or Einstellungen()
    hinweis_sprachen = sprachen_aus_hinweis(hinweis)
    referenzen = Referenzbilder(con, ein.bilder_ordner) if ein.bilder_ordner else None
    vergleich = referenzen.vergleiche if referenzen else None
    claude_an = ein.claude_modus != "aus" and claude_code.verfuegbar(ein.claude_befehl)
    if ein.claude_modus != "aus" and not claude_an:
        log.warning("Claude Code ('%s') nicht gefunden, es wird nur lokal erkannt.", ein.claude_befehl)

    ergebnis = FotoErgebnis()
    for a in finde_karten(bild):
        m = merkmale_aus(lese_text(a.bild))
        e = erkenne_aus_merkmalen(con, indizes, m, a.bild, hinweis_sprachen, vergleich)
        if claude_an and _claude_lohnt(e, ein.claude_modus):
            antwort = claude_code.lese_karte(a.bild, ein.claude_modell, ein.claude_befehl)
            cm = claude_code.als_merkmale(antwort) if antwort else None
            if cm:
                sprachen = hinweis_sprachen | ({antwort["sprache"]} if antwort.get("sprache") else set())
                e2 = erkenne_aus_merkmalen(con, indizes, cm, a.bild, sprachen, vergleich, antwort.get("besonderheit"))
                e2.quelle = "claude"
                if RANG[e2.sicherheit] > RANG[e.sicherheit] or (
                        RANG[e2.sicherheit] == RANG[e.sicherheit] and 0 < len(e2.karten) < len(e.karten)):
                    e = e2
                else:
                    e.begruendung += f"; Claude las '{antwort.get('name')}', brachte aber keine Verbesserung"
        ergebnis.karten.append(KartenErgebnis(a, e))
    return ergebnis
