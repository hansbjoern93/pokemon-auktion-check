"""Weg 2: Karten von Claude lesen lassen, über das lokal installierte Claude Code (läuft über dein Abo).

Es wird kein API-Schlüssel verwendet. Aufruf (siehe `claude --help`):
  claude -p <Aufgabe> --output-format json --json-schema <Schema> --tools Read --allowedTools Read
         --model <Modell> --no-session-persistence
Claude liest dabei nur das eine Kartenbild in einem temporären Ordner.
"""
from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import cv2
import numpy as np

from .db import normalisiere
from .erkennung import Merkmale

log = logging.getLogger(__name__)

SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "description": "Kartenname genau wie aufgedruckt, mit ex/V/VMAX/GX usw."},
        "sprache": {"type": "string", "description": "Sprachcode der Karte: de, en, fr, it, es, pt, ja, ko, zh-cn, zh-tw"},
        "kp": {"type": ["integer", "null"]},
        "attacken": {"type": "array", "items": {"type": "object", "properties": {
            "name": {"type": "string"}, "schaden": {"type": ["string", "null"]}}, "required": ["name"]}},
        "besonderheit": {"type": "string", "description":
                         "normal, holo, reverse holo, full art, illustration rare, shiny, gold oder unbekannt"},
        "lesbar": {"type": "boolean"},
    },
    "required": ["name", "sprache", "lesbar"],
}

AUFGABE = (
    "Im aktuellen Ordner liegt das Bild karte.png: ein Ausschnitt mit genau einer Pokémon-Sammelkarte "
    "aus einem Auktionsfoto. Lies die Karte mit dem Read-Werkzeug und gib nur die Felder des Schemas zurück. "
    "Schreibe Name und Attacken genau so, wie sie aufgedruckt sind (nicht übersetzen). Bestimme die Sprache "
    "aus dem Kartentext. Achte bei 'besonderheit' auf Shiny-Farben (andere Farbe des Pokémon als üblich, "
    "oft Sternenhintergrund), Full Art (Bild über die ganze Karte) und Gold. Wenn du etwas nicht sicher lesen "
    "kannst, lass es weg statt zu raten. Setze lesbar=false, wenn keine Karte erkennbar ist."
)


def verfuegbar(befehl: str = "claude") -> bool:
    return shutil.which(befehl) is not None


def lese_karte(bild: np.ndarray, modell: str = "sonnet", befehl: str = "claude", zeitlimit: int = 180) -> dict | None:
    with tempfile.TemporaryDirectory(prefix="pokeauktion_") as ordner:
        cv2.imwrite(str(Path(ordner) / "karte.png"), bild)
        aufruf = [befehl, "-p", AUFGABE, "--output-format", "json", "--json-schema", json.dumps(SCHEMA),
                  "--tools", "Read", "--allowedTools", "Read", "--model", modell, "--no-session-persistence"]
        try:
            lauf = subprocess.run(aufruf, cwd=ordner, capture_output=True, text=True, timeout=zeitlimit)
        except (OSError, subprocess.TimeoutExpired) as fehler:
            log.warning("Claude Code nicht nutzbar: %s", fehler)
            return None
    if lauf.returncode != 0:
        log.warning("Claude Code meldet Fehler: %s", (lauf.stderr or lauf.stdout)[-500:])
        return None
    return auswerten(lauf.stdout)


def auswerten(ausgabe: str) -> dict | None:
    """Antwort von `claude -p --output-format json` lesen (strukturiert oder als Text mit JSON)."""
    try:
        huelle = json.loads(ausgabe)
    except json.JSONDecodeError:
        huelle = {"result": ausgabe}
    if isinstance(huelle, dict) and huelle.get("is_error"):
        log.warning("Claude Code: %s", huelle.get("result"))
        return None
    for kandidat in (huelle.get("structured_output"), huelle.get("result")):
        if isinstance(kandidat, dict):
            return kandidat
        if isinstance(kandidat, str) and (treffer := re.search(r"\{.*\}", kandidat, re.S)):
            try:
                return json.loads(treffer.group(0))
            except json.JSONDecodeError:
                continue
    return None


def als_merkmale(antwort: dict) -> Merkmale | None:
    if not antwort or not antwort.get("lesbar", True) or not antwort.get("name"):
        return None
    attacken = [normalisiere(a.get("name")).replace(" ", "") for a in antwort.get("attacken") or [] if a.get("name")]
    zahlen = set()
    for a in antwort.get("attacken") or []:
        if a.get("schaden") and (z := re.sub(r"\D", "", str(a["schaden"]))):
            zahlen.add(int(z))
    kp = {int(antwort["kp"])} if antwort.get("kp") else set()
    return Merkmale([], [normalisiere(antwort["name"])], kp, attacken, zahlen)


# Hinweise von Claude auf die Druckversion -> Teilstrings der TCGdex-Seltenheit (englisch)
BESONDERHEIT_SELTENHEIT = {
    "shiny": ("shiny",),
    "gold": ("hyper", "gold", "secret"),
    "illustration rare": ("illustration",),
    "full art": ("ultra", "full", "secret", "illustration", "hyper"),
}
