"""Startet die App mit einem Befehl: python start.py  (unter Windows auch: Doppelklick auf start.bat)

- Prüft, ob die Kartendatenbank da ist (sonst wird sie zuerst geladen, das dauert einmalig länger).
- Gleicht die Preise ab, wenn der letzte Abgleich älter als 20 Stunden ist.
- Startet die Weboberfläche und öffnet sie im Browser.
"""
import asyncio
import os
import threading
import webbrowser

import uvicorn

from pokeauktion import config, sync
from pokeauktion.db import verbinde

PORT = int(os.getenv("PORT", "8000"))


def main() -> None:
    ein = config.lade()
    con = verbinde(ein.db_pfad)
    leer = con.execute("SELECT COUNT(*) FROM karten").fetchone()[0] == 0
    con.close()
    if leer:
        print("Die Kartendatenbank ist noch leer. Sie wird jetzt einmalig geladen (15 bis 40 Minuten) ...")
        asyncio.run(sync.main_async([]))
    else:
        print("Prüfe, ob die Preise aktuell sind ...")
        asyncio.run(sync.main_async(["--wenn-veraltet"]))
    url = f"http://127.0.0.1:{PORT}"
    print(f"\nDie App läuft: {url}  (Beenden mit Strg + C)\n")
    threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    uvicorn.run("pokeauktion.web:app", host="127.0.0.1", port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
