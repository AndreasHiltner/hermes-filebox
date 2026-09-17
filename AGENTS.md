# AGENTS.md — Hermes Filebox

Architektur-Karte und kritische Invarianten für Agenten, die an diesem Plugin arbeiten.

## Architektur

Zwei Hälften, strikt getrennt:

- **Backend** — `dashboard/plugin_api.py` (FastAPI-Router, gemountet unter `/api/plugins/filebox/`). Besitzt **ALLE** Dateisystem-I/O. Kein Netzwerk-I/O, kein LLM/Token-Verbrauch. 13 HTTP-Endpoints.
- **Renderer** — `desktop/plugin.js` (Dual-Panel-Commander, presentation-only). Ruft das Backend via `ctx.rest` auf und rendert. Kein direkter FS-Zugriff.

Hilfsmodule im Backend:
- `dashboard/guard.py` — Whitelist-Guard (`guard_path()`), liefert kanonischen `realpath`.
- `dashboard/roots.py` — Root-Whitelist-Persistenz (`RootStore`).

## SDK-Contract (Renderer)

- `ctx.rest` ist eine **Funktion** `rest(path, opts?)`, KEIN `.get`/`.post`-Objekt. GET-Parameter via Query-String im URL, POST via `{ method: 'POST', body }`, DELETE via `{ method: 'DELETE', body }`.
- Plugin-Export: `{ id, name, defaultEnabled, register(ctx) { ctx.register({ id: 'pane', area: 'panes', ..., render: () => jsx(FileboxPane, { ctx }) }) } }` — KEIN `export default function plugin(ctx)` mit `{ onMount, render }`.
- Nur `react` + `react/jsx-runtime` importieren. "plain ESM, no React" meint "kein Build-Step", nicht "kein React".
- Renderer bleibt fs-free, network-free, token-free — sämtliche I/O über `ctx.rest`.

## Commander-Modus (Renderer)

- Zwei Panels (left/right), je Pfad-Leiste + Entry-Liste, multi-select Checkboxes. Ein "active" Panel, das andere ist operation target.
- `F5` copy, `F6` move, `F8` delete (trash), `Enter` open/navigate, `Ctrl+Shift+F5` symlink — Buttons ebenso.
- Copy/move Konflikte (`phase: "ask"`) öffnen einen Per-File-Dialog (skip/overwrite/rename), dann Phase-2 mit `decisions`.
- Symlink: relative/absolute Dialog.

## Kritische Invarianten (nicht verletzen)

1. **Jeder Handler nutzt ausschließlich den `guard_path()`-Rückgabewert** — niemals den Input-Pfad erneut öffnen/renamen/löschen (TOCTOU).
2. **Whitelist = Hard Security.** Jede Pfad-Operation läuft gegen die Root-Whitelist; außerhalb → 403. Dies gilt auch für `target_dir` von `/copy`, `/move`, `/symlink` — es wird exakt wie ein Source-Pfad geprüft.
3. **Kein Netzwerk-I/O im Backend.** Nur Dateisystem.
4. **Kein LLM/Token-Verbrauch im Normalbetrieb.**
5. **Kein Self-Update.** Update ausschließlich via `hermes plugins update` mit pinned SHA.
6. **Mime/Content-Type aus Datei-Extension**, nie aus User-Headern (`/preview`).
7. **Subprocess immer `shell=False` + arg-Liste**, nie String-Interpolation in Shell.
8. **Fehler-Vertrag:** 403 (Whitelist), 404 (nicht existent), 400 (invalid input), 409 (Kollision), 413 (zu groß), 415 (binary/non-preview), 500 (OSError).

## Endpoints (13)

GET `/list` `/read` `/preview` `/roots` · POST `/roots` `/rename` `/bulk-rename` `/open` `/open-terminal` `/copy` `/move` `/symlink` · DELETE `/roots/{path}` `/trash`

- `/copy` + `/move`: `{sources, target_dir, on_conflict: "ask"|"overwrite"|"skip"|"rename", decisions?: [{source, decision}]}`. Zwei-Phasen-Protokoll bei `"ask"`: Phase-1 ohne `decisions` → `{phase:"ask", conflicts, non_conflicts}`; Phase-2 mit `decisions` → `{phase:"execute", results:[{from,to,status}], errors:[{path,error}]}`. Directory-overwrite = merge (`dirs_exist_ok`). rename iteriert `name (N).ext` (cap 1000).
- `/symlink`: `{sources, target_dir, link_type: "relative"|"absolute"}`. `link_type` invalid → 400; `target_dir` muss existieren → sonst 404. relative = `os.path.relpath`, absolute = kanonischer Source.

## Zustand

- Runtime-Roots: `<HERMES_HOME>/state/filebox/roots.json` (per `POST /roots` hinzugefügt).
- Basis-Roots: `DEFAULT_ROOT_CANDIDATES` in `roots.py` (greifen nur wenn der Ordner existiert) + Runtime-Roots in `roots.json`.

## Befehle

```
# Tests (98 grün)
env -u PYTHONPATH /home/andreas/.hermes/hermes-agent/venv/bin/python -m pytest tests/ -q

# Validierung
hermes plugins validate .

# Renderer-Syntax
node --check desktop/plugin.js
```
