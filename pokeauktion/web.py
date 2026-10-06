"""Weboberfläche (FastAPI). Start: python start.py"""
from __future__ import annotations

import html
from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config, preise_de, sammlung
from .analyse import Einstellungen
from .db import verbinde
from .erkennung import Indizes
from .katalog import suche

STATIC = Path(__file__).parent / "static"
ein = config.lade()
UPLOADS = ein.db_pfad.parent / "auswertungen"
app = FastAPI(title="Pokémon-Karten-Check")
app.mount("/static", StaticFiles(directory=STATIC), name="static")


def con():
    c = verbinde(ein.db_pfad)
    sammlung.vorbereiten(c)
    return c


@lru_cache(maxsize=1)
def indizes() -> Indizes:
    return Indizes.laden(con())


def einstellungen() -> Einstellungen:
    return Einstellungen(claude_modus=ein.erkennung_claude, claude_modell=ein.claude_modell,
                         claude_befehl=ein.claude_befehl, bilder_ordner=ein.db_pfad.parent / "bilder")


@app.get("/", response_class=HTMLResponse)
def startseite():
    return (STATIC / "index.html").read_text(encoding="utf-8")


@app.get("/api/lesezeichen")
def lesezeichen(request: Request):
    return {"code": preise_de.lesezeichen(str(request.base_url).rstrip("/"))}


@app.post("/api/auswerten")
async def auswerten(fotos: list[UploadFile] = File(...)):
    dateien = [(f.filename or "foto", await f.read()) for f in fotos]
    if not any(inhalt for _, inhalt in dateien):
        raise HTTPException(400, "Keine Fotos erhalten.")
    c = con()
    if c.execute("SELECT COUNT(*) FROM karten").fetchone()[0] == 0:
        raise HTTPException(400, "Die Kartendatenbank ist leer. Bitte zuerst: python -m pokeauktion.sync")
    auswertung = sammlung.neue_auswertung(c, indizes(), dateien, UPLOADS, einstellungen())
    return sammlung.ergebnis(c, auswertung)


@app.get("/api/auswertung/{auswertung_id}")
def auswertung(auswertung_id: str):
    erg = sammlung.ergebnis(con(), auswertung_id)
    if not erg:
        raise HTTPException(404, "Auswertung nicht gefunden.")
    return erg


@app.get("/api/auswertungen")
def auswertungen():
    return [dict(z) for z in con().execute(
        "SELECT id, name, erstellt FROM auswertungen ORDER BY erstellt DESC LIMIT 20")]


@app.get("/fotos/{auswertung_id}/{datei}")
def foto(auswertung_id: str, datei: str):
    pfad = (UPLOADS / auswertung_id / datei).resolve()
    if UPLOADS.resolve() not in pfad.parents or not pfad.exists():
        raise HTTPException(404)
    return FileResponse(pfad)


class Korrektur(BaseModel):
    zeile: str
    karte_id: str | None = None
    karte_aendern: bool = False
    preis_de: float | None = None
    preis_aendern: bool = False


@app.post("/api/auswertung/{auswertung_id}/korrektur")
def korrektur(auswertung_id: str, k: Korrektur):
    c = con()
    sammlung.korrigieren(c, auswertung_id, k.zeile,
                         karte_id=k.karte_id if k.karte_aendern else ...,
                         preis_de=k.preis_de if k.preis_aendern else ...)
    return sammlung.ergebnis(c, auswertung_id)


@app.post("/api/auswertung/{auswertung_id}/oeffnen/{zeile}")
def oeffnen(auswertung_id: str, zeile: str):
    sammlung.oeffnen_merken(con(), auswertung_id, zeile)
    return {"ok": True}


@app.get("/api/suche")
def kartensuche(q: str, kp: int | None = None):
    return [{"id": k.id, "name": k.name, "sprache": k.sprache, "set": k.set_name or k.set_id,
             "nummer": k.nummer, "seltenheit": k.seltenheit, "bild": k.bild_url, "ab": k.preis_min_ab}
            for k in suche(con(), q, kp=kp, limit=25)]


@app.get("/uebernehmen", response_class=HTMLResponse)
def uebernehmen(url: str = "", text: str = ""):
    """Ziel des Lesezeichens. Antwortet mit einer kleinen Seite, die sich selbst schließt."""
    erg = sammlung.preis_uebernehmen(con(), url, text)
    if erg["ok"]:
        preis = f"{erg['preis']:.2f}".replace(".", ",")
        botschaft = f"✅ {html.escape(erg['karte'])}: <b>{preis} €</b> übernommen."
        skript = "<script>setTimeout(()=>window.close(),2500)</script>"
    else:
        botschaft = "⚠️ " + html.escape(erg["meldung"])
        skript = ""
    return f"""<!doctype html><html lang="de"><meta charset="utf-8"><title>Preis übernehmen</title>
<body style="font:18px system-ui;padding:2rem;background:#f6f7fb">{botschaft}
<p style="color:#666;font-size:14px">Dieses Fenster kannst du schließen.</p>{skript}</body></html>"""
