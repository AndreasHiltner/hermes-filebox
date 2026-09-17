# Hermes Filebox

Explorer-style file browser for **Hermes Desktop**. Browse, preview, rename,
bulk-rename, open and trash files inside a strictly whitelisted set of roots.

Filebox is **token-free in normal use**: it registers no core agent tools, no
hooks, no middleware — it is a plain dashboard plugin. No network I/O, no model
tokens, no self-update.

## Architecture

Two halves, cleanly separated:

```
┌─────────────────────────────┐      ctx.rest      ┌──────────────────────────────┐
│ desktop/plugin.js          │ ───────────────────▶ │ dashboard/plugin_api.py     │
│ dual-panel commander        │  GET/POST/DELETE     │ FastAPI router (owns ALL    │
│ presentation only           │                     │ filesystem I/O)              │
│ no fs, no network          │ ◀─────────────────── │ mounted at                    │
└─────────────────────────────┘      JSON             │ /api/plugins/filebox/        │
                                                     └──────────────────────────────┘
```

- **Backend** (`dashboard/`): the only place that touches the filesystem. A
  FastAPI router (`plugin_api.py`) mounted by Hermes Dashboard at
  `/api/plugins/filebox/`. Every handler resolves paths through the whitelist
  guard and opens **only the canonical return value**.
  - `guard.py` — `guard_path()` whitelist guard, returns canonical `realpath`.
  - `roots.py` — `RootStore` (roots / add_root / remove_root), persisted in
    `state/filebox/roots.json`.
  - `plugin_api.py` — the 13 HTTP endpoints.
- **Renderer** (`desktop/plugin.js`): dual-panel commander that calls `ctx.rest`
  and renders. No filesystem access, no network, no tokens.

## Security model

1. **Root whitelist** — every filesystem operation is validated against the
   configured roots. A path outside the whitelist is rejected with `403`.
   This includes the `target_dir` of `/copy`, `/move`, and `/symlink`, which
   is guarded against the whitelist exactly like any source path.
2. **TOCTOU-safe guard** — `guard_path()` resolves the input to a canonical
   `realpath` and returns it. Handlers must open **that return value**, never
   the raw input, closing the symlink-swap window.
3. **No network I/O** — the backend only ever touches the local filesystem and
   the `xdg-open` / `x-terminal-emulator` launchers.
4. **No model tokens** — no LLM calls anywhere; normal use never consumes tokens.
5. **No self-update** — updates happen only via `hermes plugins update` against
   a pinned SHA. The plugin never fetches or executes remote code.
6. **Mime from extension** — preview content-types come from `mimetypes`, never
   from user-supplied headers.
7. **Safe subprocess** — launchers always run with `shell=False` and an
   argument list, never a shell string.

## Install

```bash
hermes plugins install <repo-url>     # or the private-track path for your setup
```

## Endpoints

Mounted at `/api/plugins/filebox/`.

| Method | Path                | Purpose                                  |
|--------|---------------------|------------------------------------------|
| GET    | `/list`             | List directory entries (sort/order/page/limit) |
| GET    | `/read`             | Read a text file (size-limited, `full` flag) |
| GET    | `/preview`          | Inline image/audio/PDF preview (FileResponse) |
| GET    | `/roots`            | List whitelist roots                     |
| POST   | `/roots`            | Add a root (canonicalized, `/` and `~` refused) |
| POST   | `/rename`           | Rename a single entry                    |
| POST   | `/bulk-rename`      | Bulk rename (replace/prefix/suffix/seq)  |
| POST   | `/open`             | Open with `xdg-open`                     |
| POST   | `/open-terminal`    | Open terminal in a directory             |
| POST   | `/copy`             | Copy entries (ask/overwrite/skip/rename conflict handling) |
| POST   | `/move`             | Move entries (same conflict model as `/copy`) |
| POST   | `/symlink`          | Create relative/absolute symlinks        |
| DELETE | `/roots/{path}`     | Remove a root                            |
| DELETE | `/trash`            | Move entries to trash (send2trash)       |

Error contract: `403` outside whitelist, `404` not found, `400` invalid input,
`409` target exists, `413` file too large, `415` unsupported/binary,
`500` launcher failure.

## Commander mode

The renderer is a **dual-panel commander** (Total-Commander-style): two panels,
each with a path bar and entry list plus multi-select checkboxes. One panel is
active; the other is the operation target.

- `F5` copy, `F6` move, `F8` delete (trash) — with matching buttons.
- `Enter` open / navigate into a directory.
- `Ctrl+Shift+F5` create symlink (relative/absolute dialog).
- Copy/move conflicts (`phase: "ask"`) open a per-file dialog — skip / overwrite /
  rename — then re-submit with `decisions` for phase 2.

## Tests

```bash
env -u PYTHONPATH /home/andreas/.hermes/hermes-agent/venv/bin/python -m pytest tests/ -q
```

98 tests green across guard, roots, api, preview, copy_move, symlink, and security_regression.
