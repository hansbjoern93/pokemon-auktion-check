#!/bin/sh
# Startet die App (beim ersten Mal wird alles eingerichtet).
cd "$(dirname "$0")"
[ -d .venv ] || python3 -m venv .venv
. .venv/bin/activate
pip install -q -r requirements.txt
python start.py
