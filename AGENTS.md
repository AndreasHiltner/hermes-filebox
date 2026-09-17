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

- Zwei Panels (left/right), je Pfad-Leiste (Roots-`<select>`-Dropdown) + Entry-Liste, multi-select Checkboxes. Ein "active" Panel, das andere ist operation target.
- `F5` copy, `F6` move, `F8` delete (trash), `F9` terminal im aktiven Panel, `Enter` open/navigate, `Ctrl+Shift+F5` symlink — Buttons ebenso.
- Sortierung: **Ordner zuerst, dann Dateien**, jede Gruppe alphabetisch aufsteigend (`/list` sortiert erst nach `is_dir`, dann nach dem angefragten Key).
- **Datei-Icons** (Emoji) pro Datei-Typ via Extension — `fileIcon(name, isDir)` in `plugin.js`. Ordner/PDF/Text/MD/Spreadsheet/Bild/Audio/Video/Archiv/Code/Log, unbekannt → generisches Dokument.
- Single-Click selektiert **genau einen** Entry (cleart vorherige Auswahl); öffnen/navigieren nur via Double-Click oder Ctrl/Cmd+Click. Checkboxes machen multi-select und stoppen propagation (navigieren nie).
- Copy/move Konflikte (`phase: "ask"`) öffnen einen Per-File-Dialog (skip/overwrite/rename), dann Phase-2 mit `decisions`. Unaufgelöste Konflikte blockieren "Apply" ("Resolve all conflicts first (N left)").
- Symlink: relative/absolute Dialog.

### Kontextmenü (Right-Click)

- **Nur Datei-/Ordner-Entries** öffnen das Custom-Kontextmenü (Open / Copy / Move / Delete / Symlink / Copy Path / Open Terminal here); Right-Click selektiert den Entry zusätzlich. Leere Fläche zeigt dasselbe Menü ohne Open/Symlink.
- `Copy Path` kopiert den absoluten Pfad in die Zwischenablage (`copyTextToClipboard`, navigator.clipboard + textarea/execCommand-Fallback für Webviews). `Open Terminal here` → `POST /open-terminal` (Ordner-Entry: dessen Pfad, Datei-Entry/leere Fläche: Panel-Verzeichnis).
- Pfad-Leisten-`<select>` und der `..`-Eintrag zeigen **kein** Menü. Die Pane-Root trägt `data-context-menu-skip` → unterdrückt das globale App-Menü überall, außer auf den Entry-Rows, deren eigener `onContextMenu`-Handler `preventDefault()` + `stopPropagation()` aufruft und ein eigenes Menü rendert.
- Das globale App-Menü läuft capture-phase (`app-context-menu.tsx`); ohne `stopPropagation` + `data-context-menu-skip` gewinnt immer die App.

### Delete-Bestätigung

- `F8` / Toolbar / Kontextmenü löscht **nicht** sofort. Öffnet Bestätigungs-Dialog mit Dateinamen-Liste ("Move this item to trash?" / "Move these N items to trash?"), Delete + Cancel. Löschung erst bei Confirm via `DELETE /trash`.

### Root hinzufügen

- Toolbar "＋ Add root" öffnet Dialog für absoluten Pfad → `POST /roots` (Backend kanonisiert + persistiert nach `state/filebox/roots.json`, lehnt `/`, `~` und jeden Vorfahren von `~` ab) → Roots-Liste refreshen + neuer Root ins aktive Panel laden.
- Roots erscheinen zusätzlich im Pfad-Leisten-Dropdown (Wechsel lädt das Panel).

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

## Drei Kopien (kritisch!)

Der Renderer existiert in **drei** Dateien, die nach jedem Edit synchron gehalten werden müssen:

| Pfad | Rolle |
|---|---|
| `/srv/clawd-share/Allgemein/Sentinel/Repositories/hermes-filebox/desktop/plugin.js` | Git-Repo-Master |
| `/home/andreas/.hermes/plugins/filebox/desktop/plugin.js` | Installiertes Plugin (Backend-Quelle) |
| `/home/andreas/.hermes/desktop-plugins/filebox/plugin.js` | **Materialisierte Kopie, die die App tatsächlich lädt** |

Backend (`dashboard/plugin_api.py`) zusätzlich nach `/home/andreas/.hermes/plugins/filebox/dashboard/` kopieren.

Sync-Befehl (nach jedem Frontend-Edit):

```bash
cd /srv/clawd-share/Allgemein/Sentinel/Repositories/hermes-filebox && \
node --check desktop/plugin.js && \
cp desktop/plugin.js /home/andreas/.hermes/plugins/filebox/desktop/plugin.js && \
cp desktop/plugin.js /home/andreas/.hermes/desktop-plugins/filebox/plugin.js && \
node --check /home/andreas/.hermes/plugins/filebox/desktop/plugin.js && \
node --check /home/andreas/.hermes/desktop-plugins/filebox/plugin.js
```

> `desktop-plugins/filebox/plugin.js` trägt eine `.hermes-package.json`-Markierung und wird ggf. aus `plugins/filebox/desktop/` re-materialisiert (mtime-Drift). Der identische Patch liegt in beiden — kein Drift-Risiko.

## Befehle

```
# Tests (98 grün)
env -u PYTHONPATH /home/andreas/.hermes/hermes-agent/venv/bin/python -m pytest tests/ -q

# Validierung
hermes plugins validate .

# Renderer-Syntax
node --check desktop/plugin.js
```

## Install / Security-Scan (False Positives)

`hermes plugins install <pfad|url>` blockt bei der Erstinstallation mit `Decision: BLOCKED — (community source + caution verdict, N findings)`. Alle Findings sind hier **False Positives** und mit `--force` zu überstimmen:

- **HIGH `traversal`** → eigene Regressionstests in `tests/test_security_regression.py` (prüfen, dass `guard_path()` `/etc/passwd` ablehnt). Der Guard funktioniert korrekt — der Test ist der Beweis, nicht der Angriff.
- **MEDIUM `execution`** → `subprocess.run(["xdg-open", real], check=False, ...)` in `dashboard/plugin_api.py` — `shell=False` + arg-Liste, exakt Invariante #7. Kein String-Shelling.
- **MEDIUM `obfuscation`** → Test-Fixtures mit Binär-Bytes (`write_bytes(b"\x00\x01\x02")`).
- **LOW `persistence`** → `AGENTS.md`-Match (diese Datei), inhaltsleer.

Install-Kommando (lokal, ohne Push):

```
cd ~/.hermes/hermes-agent && hermes plugins install "file:///srv/clawd-share/Allgemein/Sentinel/Repositories/hermes-filebox" --enable --force
```

Nach install/enable: `hermes gateway restart` fürs Backend-Modul. Die UI-Pane erscheint erst über Settings → Plugins Toggle (Renderer-seitig, localStorage — kein config.yaml-Eintrag).
