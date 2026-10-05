"""Einstellungen aus der .env-Datei (nie Zugangsdaten im Code)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

PROJEKT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(PROJEKT_DIR / ".env")

# Alle Sprachen, die TCGdex unterstützt (Quelle: tcgdex/cards-database, interfaces.d.ts).
# Einige davon haben (noch) keine Karten; das schadet nicht.
ALLE_SPRACHEN = [
    "en", "de", "fr", "es", "es-mx", "it", "pt", "pt-br", "pt-pt", "nl", "pl", "ru",
    "ja", "ko", "zh-tw", "zh-cn", "id", "th",
]


def _liste(name: str, standard: list[str]) -> list[str]:
    wert = os.getenv(name, "").strip()
    if not wert:
        return standard
    return [teil.strip() for teil in wert.split(",") if teil.strip()]


@dataclass
class Einstellungen:
    db_pfad: Path = field(default_factory=lambda: Path(os.getenv("DB_PFAD", PROJEKT_DIR / "data" / "karten.sqlite")))
    tcgdex_basis: str = os.getenv("TCGDEX_BASIS_URL", "https://api.tcgdex.net/v2")
    sprachen: list[str] = field(default_factory=lambda: _liste("TCGDEX_SPRACHEN", ALLE_SPRACHEN))
    parallele_abrufe: int = int(os.getenv("TCGDEX_PARALLEL", "8"))
    graphql_seitengroesse: int = int(os.getenv("TCGDEX_GRAPHQL_SEITE", "250"))

    # Ab Schritt 2/3 verwendet:
    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")
    claude_modell: str = os.getenv("CLAUDE_MODELL", "claude-opus-5-5")
    ebay_client_id: str = os.getenv("EBAY_CLIENT_ID", "")
    ebay_client_secret: str = os.getenv("EBAY_CLIENT_SECRET", "")


def lade() -> Einstellungen:
    return Einstellungen()
