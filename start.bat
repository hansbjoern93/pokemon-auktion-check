@echo off
rem Doppelklick startet die App (beim ersten Mal wird alles eingerichtet).
cd /d "%~dp0"
if not exist .venv (
  echo Richte die App einmalig ein ...
  python -m venv .venv
)
call .venv\Scripts\activate
pip install -q -r requirements.txt
python start.py
pause
