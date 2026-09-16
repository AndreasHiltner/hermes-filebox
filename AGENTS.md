# AGENTS.md — Hermes Filebox

Architektur-Karte und kritische Invarianten für Agenten, die an diesem Plugin arbeiten.

## Architektur

Zwei Hälften, strikt getrennt:

- **Backend** — `dashboard/plugin_api.py` (FastAPI-Router, gemountet unter `/api/plugins/filebox/`). Besitzt **ALLE** Dateisystem-I/O. Kein Netzwerk-I/O, kein LLM/Token-Verbrauch.
- **Renderer** — `desktop/plugin.js` (thin ESM, presentation-only). Ruft das Backend via `ctx.rest` auf und rendert. Kein direkter FS-Zugriff.

Hilfsmodule im Backend:
- `dashboard/guard.py` — Whitelist-Guard (`guard_path()`), liefert kanonischen `realpath`.
- `dashboard/roots.py` — Root-Whitelist-Persistenz (`RootStore`).

## Kritische Invarianten (nicht verletzen)

1. **Jeder Handler nutzt ausschließlich den `guard_path()`-Rückgabewert** — niemals den Input-Pfad erneut öffnen/renamen/löschen (TOCTOU).
2. **Whitelist = Hard Security.** Jede Pfad-Operation läuft gegen die Root-Whitelist; außerhalb → 403.
3. **Kein Netzwerk-I/O im Backend.** Nur Dateisystem.
4. **Kein LLM/Token-Verbrauch im Normalbetrieb.**
5. **Kein Self-Update.** Update ausschließlich via `hermes plugins update` mit pinned SHA.
6. **Mime/Content-Type aus Datei-Extension**, nie aus User-Headern (`/preview`).
7. **Subprocess immer `shell=False` + arg-Liste**, nie String-Interpolation in Shell.
8. **Fehler-Vertrag:** 403 (Whitelist), 404 (nicht existent), 400 (invalid input), 409 (Kollision), 413 (zu groß), 415 (binary/non-preview), 500 (OSError).

## Zustand

- Runtime-Roots: `<HERMES_HOME>/state/filebox/roots.json` (per `POST /roots` hinzugefügt).
- Basis-Roots: `roots.yaml` (falls vorhanden, hand-editierbar) + `DEFAULT_ROOT_CANDIDATES` in `roots.py`.

## Befehle

```
# Tests
env -u PYTHONPATH /home/andreas/.hermes/hermes-agent/venv/bin/python -m pytest tests/ -q

# Validierung
hermes plugins validate .

# Renderer-Syntax
node --check desktop/plugin.js
```
