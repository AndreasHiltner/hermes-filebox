// Hermes Filebox — desktop renderer (thin ESM, presentation-only).
// Dual-panel commander. All filesystem I/O happens in dashboard/plugin_api.py;
// this file calls ctx.rest (a FUNCTION: ctx.rest(path, opts)) and renders.
// No network, no model tokens, no fs access. Runs uncompiled: no JSX syntax,
// UI is built with jsx()/jsxs() from react/jsx-runtime.

import { useState, useEffect, useCallback, useRef } from 'react'
import { jsx, jsxs } from 'react/jsx-runtime'

// ---------------------------------------------------------------------------
// Backend calls — every I/O operation goes through ctx.rest(path, opts?).
// GET: ctx.rest('/list?path=' + encodeURIComponent(path))
// POST/DELETE: ctx.rest('/copy', { method: 'POST', body: {...} })
// ---------------------------------------------------------------------------
function listPath(ctx, path) {
  return ctx.rest(
    '/list?path=' + encodeURIComponent(path) + '&sort=name&order=asc&page=0&limit=100'
  )
}

function listRoots(ctx) {
  return ctx.rest('/roots')
}

function post(ctx, path, body) {
  return ctx.rest(path, { method: 'POST', body })
}

function del(ctx, path, body) {
  return ctx.rest(path, { method: 'DELETE', body })
}

// ---------------------------------------------------------------------------
// Small presentational helpers (plain React elements, no JSX).
// ---------------------------------------------------------------------------
function el(tag, props, ...children) {
  if (children.length === 0) return jsx(tag, props)
  if (children.length === 1) return jsx(tag, Object.assign({}, props, { children: children[0] }))
  return jsxs(tag, Object.assign({}, props, { children }))
}

function fmtSize(size) {
  if (size === null || size === undefined) return ''
  if (size < 1024) return size + ' B'
  if (size < 1024 * 1024) return (size / 1024).toFixed(1) + ' KB'
  if (size < 1024 * 1024 * 1024) return (size / (1024 * 1024)).toFixed(1) + ' MB'
  return (size / (1024 * 1024 * 1024)).toFixed(1) + ' GB'
}

// Copy text to the system clipboard. Prefers the async Clipboard API; falls
// back to a hidden textarea + execCommand('copy') for desktop webviews where
// navigator.clipboard may be gated (non-secure context / permission denied).
function copyTextToClipboard(text) {
  if (navigator.clipboard && navigator.clipboard.writeText) {
    return navigator.clipboard.writeText(text)
  }
  return new Promise((resolve, reject) => {
    const ta = document.createElement('textarea')
    ta.value = text
    ta.style.position = 'fixed'
    ta.style.opacity = '0'
    document.body.appendChild(ta)
    ta.focus()
    ta.select()
    let ok = false
    try {
      ok = document.execCommand('copy')
    } catch (e) {
      ok = false
    }
    document.body.removeChild(ta)
    ok ? resolve() : reject(new Error('clipboard unavailable'))
  })
}

function fmtMtime(mtime) {
  if (!mtime) return ''
  return new Date(mtime * 1000).toLocaleString()
}

// Emoji icon per file type (extension-based). Directories and unknown types
// fall back to the folder / generic-document glyphs.
const EXT_ICONS = {
  // Documents
  pdf: '\u{1F4C3}', txt: '\u{1F4C4}', md: '\u{1F4DD}', doc: '\u{1F4C4}', docx: '\u{1F4C4}',
  rtf: '\u{1F4C4}', odt: '\u{1F4C4}',
  // Spreadsheets / slides
  xls: '\u{1F4CA}', xlsx: '\u{1F4CA}', csv: '\u{1F4CA}', ppt: '\u{1F4C8}', pptx: '\u{1F4C8}',
  // Images
  png: '\u{1F5BC}', jpg: '\u{1F5BC}', jpeg: '\u{1F5BC}', gif: '\u{1F5BC}', svg: '\u{1F5BC}',
  webp: '\u{1F5BC}', bmp: '\u{1F5BC}', ico: '\u{1F5BC}', tiff: '\u{1F5BC}',
  // Media
  mp3: '\u{1F3B5}', wav: '\u{1F3B5}', flac: '\u{1F3B5}', ogg: '\u{1F3B5}', m4a: '\u{1F3B5}',
  mp4: '\u{1F3AC}', mkv: '\u{1F3AC}', mov: '\u{1F3AC}', avi: '\u{1F3AC}', webm: '\u{1F3AC}',
  // Archives
  zip: '\u{1F5C3}', tar: '\u{1F5C3}', gz: '\u{1F5C3}', rar: '\u{1F5C3}', '7z': '\u{1F5C3}',
  // Code
  js: '\u{1F4BB}', ts: '\u{1F4BB}', jsx: '\u{1F4BB}', tsx: '\u{1F4BB}', py: '\u{1F4BB}',
  rb: '\u{1F4BB}', go: '\u{1F4BB}', rs: '\u{1F4BB}', java: '\u{1F4BB}', c: '\u{1F4BB}',
  cpp: '\u{1F4BB}', h: '\u{1F4BB}', hpp: '\u{1F4BB}', cs: '\u{1F4BB}', php: '\u{1F4BB}',
  sh: '\u{1F4BB}', html: '\u{1F4BB}', css: '\u{1F4BB}', json: '\u{1F4BB}', yml: '\u{1F4BB}',
  yaml: '\u{1F4BB}', toml: '\u{1F4BB}',
  // Misc
  log: '\u{1F4DC}',
}

function fileIcon(name, isDir) {
  if (isDir) return '\u{1F4C1}'
  const dot = name.lastIndexOf('.')
  const ext = dot >= 0 ? name.slice(dot + 1).toLowerCase() : ''
  return EXT_ICONS[ext] || '\u{1F4C4}'
}

// Parent directory of an absolute path; null when at the filesystem root
// (or when the path is empty/unset). Used for the `..` entry.
function parentPath(p) {
  if (!p) return null
  const trimmed = p.replace(/\/+$/, '')
  if (!trimmed || trimmed === '/') return null
  const idx = trimmed.lastIndexOf('/')
  if (idx <= 0) return '/'
  return trimmed.slice(0, idx)
}

// Shared button style — makes dialog actions read as real buttons, not text.
const btnStyle = {
  padding: '3px 10px',
  border: '1px solid var(--ui-stroke-secondary)',
  borderRadius: '4px',
  background: 'var(--ui-bg-elevated)',
  color: 'var(--ui-text-primary)',
  cursor: 'pointer',
}

// ---------------------------------------------------------------------------
// FileboxPane — React function component. Uses only useState/useEffect/useCallback
// from 'react' (allowed specifier). All state is UI-local; no fs/network/tokens.
// ---------------------------------------------------------------------------
function FileboxPane({ ctx }) {
  const [panels, setPanels] = useState({
    left: { path: null, entries: [] },
    right: { path: null, entries: [] },
  })
  const [roots, setRoots] = useState([])
  const [history, setHistory] = useState({ left: [], right: [] })
  const [active, setActive] = useState('left')
  const [selected, setSelected] = useState({ left: {}, right: {} })
  const [dialog, setDialog] = useState(null)
  const [notice, setNotice] = useState(null)
  const [menu, setMenu] = useState(null) // { x, y, side } while a context menu is open
  // Cursor index into the visible row list per panel (0 = `..` when present).
  // Drives arrow/PageUp/PageDown keyboard navigation; independent of checkboxes.
  const [cursor, setCursor] = useState({ left: 0, right: 0 })
  const listRefs = useRef({ left: null, right: null })

  const loadRoots = useCallback(async () => {
    const r = await listRoots(ctx)
    return r.roots
  }, [ctx])

  // Record a visited directory at the front of a panel's history (deduped),
  // so the dropdown doubles as a recent-locations list. Most recent first.
  const pushHistory = useCallback((side, path) => {
    if (!path) return
    setHistory((prev) => {
      const list = [path].concat((prev[side] || []).filter((x) => x !== path))
      return { ...prev, [side]: list }
    })
  }, [])

  const loadPanel = useCallback(
    async (side, path) => {
      const r = await listPath(ctx, path)
      setPanels((prev) => ({ ...prev, [side]: { path, entries: r.entries } }))
      setCursor((prev) => ({ ...prev, [side]: 0 }))
      pushHistory(side, path)
    },
    [ctx, pushHistory]
  )

  useEffect(() => {
    ;(async () => {
      try {
        const rs = await loadRoots()
        setRoots(rs)
        if (rs.length) {
          await loadPanel('left', rs[0])
          await loadPanel('right', rs[0])
        }
      } catch (e) {
        setNotice({ kind: 'error', text: 'Failed to load roots: ' + (e && e.message ? e.message : e) })
      }
    })()
  }, [loadRoots, loadPanel])

  const otherSide = (side) => (side === 'left' ? 'right' : 'left')

  // Only explicitly selected paths. No implicit fallback to the panel path:
  // copy/move/delete on "nothing selected" must error, not act on the cwd.
  const selectedPaths = (side) =>
    Object.keys(selected[side]).filter((k) => selected[side][k])

  // Flattened list of selectable paths for one panel, matching the rendered
  // order: `..` (parent) first when present, then the entries. The cursor
  // index refers to this list.
  const visiblePaths = (side) => {
    const p = panels[side]
    const parent = parentPath(p.path)
    const list = []
    if (parent) list.push(parent)
    for (const e of p.entries || []) list.push(e.path)
    return list
  }

  // Half the number of rows currently visible in a panel's list (min 1).
  // Used for PageUp/PageDown; falls back to a fixed step if the DOM isn't
  // measured yet.
  function halfPageLen(side) {
    const ul = listRefs.current[side]
    if (!ul || !ul.clientHeight) return 10
    const first = ul.firstElementChild
    const rowH = first ? first.offsetHeight : 0
    if (!rowH) return 10
    return Math.max(1, Math.floor(ul.clientHeight / rowH / 2))
  }

  function scrollCursorIntoView(side, index) {
    const ul = listRefs.current[side]
    if (!ul) return
    const child = ul.children[index]
    if (child && child.scrollIntoView) child.scrollIntoView({ block: 'nearest' })
  }

  // Jump the keyboard cursor to an absolute index, clamped to the list bounds.
  // Selects the target row and scrolls it into view (nearest = never scrolls
  // past the top/bottom when already at the edge).
  function jumpTo(side, index) {
    const paths = visiblePaths(side)
    if (!paths.length) return
    const next = Math.max(0, Math.min(index, paths.length - 1))
    setCursor((prev) => ({ ...prev, [side]: next }))
    selectOne(side, paths[next])
    scrollCursorIntoView(side, next)
  }

  // Move the keyboard cursor by `delta` rows from its current position. When
  // nothing is selected yet, the first navigation step anchors at the first
  // row (index 0), so keyboard-only navigation selects it and continues from it.
  function moveCursor(side, delta) {
    setActive(side)
    const base = selectedPaths(side).length ? cursor[side] : (delta > 0 ? -1 : 0)
    jumpTo(side, base + delta)
  }

  async function navigate(side, entry) {
    setActive(side)
    if (entry.is_dir) {
      await loadPanel(side, entry.path)
      setSelected((prev) => ({ ...prev, [side]: {} }))
    } else {
      // Open a file: try the /open backend (xdg-open) as a convenience.
      try {
        await post(ctx, '/open', { path: entry.path })
      } catch (e) {
        setNotice({ kind: 'error', text: 'Open failed: ' + (e && e.message ? e.message : e) })
      }
    }
  }

  function toggleSelect(side, path) {
    setActive(side)
    setSelected((prev) => {
      const next = { ...prev[side] }
      if (next[path]) delete next[path]
      else next[path] = true
      return { ...prev, [side]: next }
    })
  }

  // Total Commander style: a plain single click selects exactly one entry
  // (clearing any prior selection) and makes that panel active; multi-select
  // happens via the checkbox. Also syncs the keyboard cursor to the selected
  // row so arrow/page navigation continues from the mouse-selected entry.
  function selectOne(side, path) {
    setActive(side)
    setSelected((prev) => ({ ...prev, [side]: { [path]: true } }))
    const idx = visiblePaths(side).indexOf(path)
    if (idx >= 0) setCursor((prev) => ({ ...prev, [side]: idx }))
  }

  function activate(side) {
    setActive(side)
  }

  function entryFor(side, path) {
    if (!path) return null
    return (panels[side].entries || []).find((x) => x.path === path) || null
  }

  // --- copy/move: two-phase "ask" flow ------------------------------------
  async function runCopyMove(op, targetDir, decisions) {
    const src = active
    const sources = selectedPaths(src)
    if (!sources.length) {
      setNotice({ kind: 'error', text: 'Nothing selected.' })
      return
    }
    const body = { sources, target_dir: targetDir, on_conflict: 'ask' }
    if (decisions) body.decisions = decisions
    const resp = await post(ctx, op, body)
    if (resp.phase === 'ask') {
      setDialog({
        kind: 'conflict',
        op,
        targetDir,
        conflicts: resp.conflicts,
        decisions: {},
      })
    } else {
      setDialog(null)
      setNotice(summarizeResults(resp.results, resp.errors))
      await refreshAfter(sources, src)
    }
  }

  function summarizeResults(results, errors) {
    const ok = (results || []).length
    const err = (errors || []).length
    let text = ok + ' done'
    if (err) {
      const shown = (errors || []).slice(0, 5).map((e) => e.path + ' (' + e.error + ')').join(', ')
      const more = err > 5 ? ', and ' + (err - 5) + ' more' : ''
      text += ', ' + err + ' error(s): ' + shown + more
    }
    return { kind: err ? 'error' : 'ok', text }
  }

  async function refreshAfter(sources, src) {
    // reload active panel (sources may have moved) and the other panel (target)
    await loadPanel(src, panels[src].path)
    await loadPanel(otherSide(src), panels[otherSide(src)].path)
  }

  function submitConflictDecision(conflict, decision) {
    setDialog((d) => ({
      ...d,
      decisions: { ...d.decisions, [conflict.source]: decision },
    }))
  }

  async function confirmConflictDialog() {
    const d = dialog
    const unresolved = (d.conflicts || []).filter((c) => !d.decisions[c.source])
    if (unresolved.length) {
      setNotice({ kind: 'error', text: 'Resolve all conflicts first (' + unresolved.length + ' left).' })
      return
    }
    const decisions = (d.conflicts || []).map((c) => ({
      source: c.source,
      decision: d.decisions[c.source],
    }))
    setDialog(null)
    await runCopyMove(d.op, d.targetDir, decisions)
  }

  // --- symlink flow -------------------------------------------------------
  function startSymlink() {
    const sources = selectedPaths(active)
    if (!sources.length) {
      setNotice({ kind: 'error', text: 'Nothing selected.' })
      return
    }
    setDialog({ kind: 'symlink', sources, targetDir: panels[otherSide(active)].path, linkType: 'relative' })
  }

  const startSymlinkRef = useRef()
  startSymlinkRef.current = startSymlink

  // --- open terminal here (F9) ---------------------------------------------
  async function openTerminalHere(path) {
    const p = path || panels[active].path
    if (!p) return
    try {
      await post(ctx, '/open-terminal', { path: p })
    } catch (e) {
      setNotice({ kind: 'error', text: 'Open terminal failed: ' + (e && e.message ? e.message : e) })
    }
  }
  const openTerminalRef = useRef()
  openTerminalRef.current = openTerminalHere

  // --- copy path -----------------------------------------------------------
  async function copyPath(path) {
    const p = path || panels[active].path
    if (!p) return
    try {
      await copyTextToClipboard(p)
      setNotice({ kind: 'ok', text: 'Copied: ' + p })
    } catch (e) {
      setNotice({ kind: 'error', text: 'Copy failed: ' + (e && e.message ? e.message : e) })
    }
  }

  async function confirmSymlink() {
    const d = dialog
    const resp = await post(ctx, '/symlink', {
      sources: d.sources,
      target_dir: d.targetDir,
      link_type: d.linkType,
    })
    setDialog(null)
    setNotice(summarizeResults(resp.results, resp.errors))
    await refreshAfter(d.sources, active)
  }

  // --- delete (trash) -----------------------------------------------------
  async function trashSelection() {
    const sel = selectedPaths(active)
    if (!sel.length) {
      setNotice({ kind: 'error', text: 'Nothing selected.' })
      return
    }
    // Confirmation gate — deleting is irreversible (well, trash, but still).
    setDialog({ kind: 'trash', paths: sel })
  }

  async function confirmTrash() {
    const d = dialog
    setDialog(null)
    const resp = await del(ctx, '/trash', { paths: d.paths })
    setNotice(summarizeResults(resp.results, resp.errors))
    await refreshAfter(d.paths, active)
  }

  // --- add root ------------------------------------------------------------
  function startAddRoot() {
    setDialog({ kind: 'add-root', value: '' })
  }

  async function confirmAddRoot() {
    const d = dialog
    const path = (d.value || '').trim()
    if (!path) {
      setNotice({ kind: 'error', text: 'Enter a directory path.' })
      return
    }
    setDialog(null)
    try {
      const resp = await post(ctx, '/roots', { path })
      setRoots(resp.roots)
      await loadPanel(active, resp.added || path)
      setNotice({ kind: 'ok', text: 'Added root: ' + resp.added })
    } catch (e) {
      setNotice({ kind: 'error', text: 'Add root failed: ' + (e && e.message ? e.message : e) })
    }
  }

  // --- context menu -------------------------------------------------------
  function openContextMenu(e, side) {
    e.preventDefault()
    e.stopPropagation()
    setActive(side)
    setMenu({ x: e.clientX, y: e.clientY, side })
  }

  function closeContextMenu() {
    setMenu(null)
  }

  function contextAction(fn) {
    return () => {
      setMenu(null)
      fn()
    }
  }

  useEffect(() => {
    if (!menu) return
    const onKey = (e) => {
      if (e.key === 'Escape') setMenu(null)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [menu])

  // --- keyboard (Total Commander layout) ----------------------------------
  function onKeyDown(e) {
    if (e.key === 'F5') {
      e.preventDefault()
      runCopyMove('/copy', panels[otherSide(active)].path)
    } else if (e.key === 'F6') {
      e.preventDefault()
      runCopyMove('/move', panels[otherSide(active)].path)
    } else if (e.key === 'F8') {
      e.preventDefault()
      trashSelection()
    } else if (e.key === 'F9') {
      e.preventDefault()
      openTerminalRef.current()
    } else if (e.key === 'Enter') {
      e.preventDefault()
      const sel = selectedPaths(active)
      if (sel.length === 1) {
        const parent = parentPath(panels[active].path)
        if (sel[0] === parent) {
          navigate(active, { is_dir: true, path: parent, name: '..' })
        } else {
          const entry = panels[active].entries.find((x) => x.path === sel[0])
          if (entry) navigate(active, entry)
        }
      }
    } else if (e.key === 'Tab') {
      e.preventDefault()
      setActive(otherSide(active))
    } else if (e.key === 'ArrowDown') {
      e.preventDefault()
      moveCursor(active, 1)
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      moveCursor(active, -1)
    } else if (e.key === 'PageDown') {
      e.preventDefault()
      moveCursor(active, halfPageLen(active))
    } else if (e.key === 'PageUp') {
      e.preventDefault()
      moveCursor(active, -halfPageLen(active))
    } else if (e.key === 'Home') {
      e.preventDefault()
      setActive(active)
      jumpTo(active, 0)
    } else if (e.key === 'End') {
      e.preventDefault()
      setActive(active)
      jumpTo(active, visiblePaths(active).length - 1)
    }
  }

  function onGlobalKeyDown(e) {
    if (e.ctrlKey && e.shiftKey && e.key === 'F5') {
      e.preventDefault()
      startSymlinkRef.current()
    }
  }

  useEffect(() => {
    window.addEventListener('keydown', onGlobalKeyDown)
    return () => window.removeEventListener('keydown', onGlobalKeyDown)
  }, [])

  // -------------------------------------------------------------------------
  // Rendering
  // -------------------------------------------------------------------------
  function renderPathBar(side) {
    const p = panels[side]
    // Recent locations first (most recent first), then roots as a suffix.
    // Dedupe so a root already in history doesn't appear twice.
    const hist = history[side] || []
    const rootList = roots || []
    const seen = {}
    const items = []
    for (const h of hist) {
      if (!seen[h]) {
        seen[h] = true
        items.push(h)
      }
    }
    for (const r of rootList) {
      if (!seen[r]) {
        seen[r] = true
        items.push(r)
      }
    }
    const current = p.path || ''
    const options = items.map((path) => el('option', { key: path, value: path }, path))
    const select = el(
      'select',
      {
        value: current,
        style: {
          width: '100%',
          minWidth: 0,
          background: 'var(--ui-bg-elevated)',
          color: active === side ? 'var(--ui-accent)' : 'var(--ui-text-primary)',
          border: '1px solid var(--ui-stroke-secondary)',
          borderRadius: '4px',
          padding: '4px 6px',
          fontSize: '13px',
        },
        onChange: (e) => {
          const v = e.target.value
          if (v) loadPanel(side, v)
        },
        title: p.path || '(none)',
      },
      options
    )
    return el(
      'div',
      { style: { padding: '2px 1px' } },
      select
    )
  }

  function renderEntry(side, entry) {
    const isSel = !!selected[side][entry.path]
    const rowStyle = {
      display: 'flex',
      alignItems: 'center',
      gap: '6px',
      padding: '2px 4px',
      cursor: 'pointer',
      borderBottom: '1px solid var(--ui-stroke-secondary)',
      background: isSel ? '#2563eb' : 'transparent',
      color: isSel ? '#ffffff' : 'inherit',
    }
    const nameStyle = { flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }
    const metaStyle = {
      color: isSel ? '#dbeafe' : 'var(--ui-text-tertiary)',
      fontSize: '11px',
      whiteSpace: 'nowrap',
    }
    return el(
      'div',
      {
        key: entry.path,
        style: rowStyle,
        onClick: (e) => {
          if (e.ctrlKey || e.metaKey) navigate(side, entry)
          else selectOne(side, entry.path)
        },
        onDoubleClick: () => navigate(side, entry),
        onContextMenu: (e) => {
          selectOne(side, entry.path)
          openContextMenu(e, side)
        },
      },
      el('input', {
        type: 'checkbox',
        checked: isSel,
        onClick: (e) => e.stopPropagation(),
        onChange: () => toggleSelect(side, entry.path),
      }),
      el('span', null, fileIcon(entry.name, entry.is_dir)),
      el('span', { style: nameStyle }, entry.name),
      el('span', { style: metaStyle }, fmtSize(entry.size)),
      el('span', { style: metaStyle }, fmtMtime(entry.mtime))
    )
  }

  // The `..` entry at the top of each panel — navigates to the parent dir.
  function renderParentEntry(side, parent) {
    const isSel = !!selected[side][parent]
    const rowStyle = {
      display: 'flex',
      alignItems: 'center',
      gap: '6px',
      padding: '2px 4px',
      cursor: 'pointer',
      borderBottom: '1px solid var(--ui-stroke-secondary)',
      background: isSel ? '#2563eb' : 'transparent',
      color: isSel ? '#ffffff' : 'inherit',
    }
    return el(
      'div',
      {
        key: '..',
        style: rowStyle,
        onClick: () => selectOne(side, parent),
        onDoubleClick: () => navigate(side, { is_dir: true, path: parent, name: '..' }),
      },
      el('span', null, '\u{1F4C1}'),
      el('span', { style: { flex: 1 } }, '..'),
      el('span', null, ''),
      el('span', null, '')
    )
  }

  function renderPanel(side) {
    const p = panels[side]
    const parent = parentPath(p.path)
    const entries = (p.entries || []).map((e) => renderEntry(side, e))
    const items = parent ? [renderParentEntry(side, parent)].concat(entries) : entries
    const containerStyle = {
      flex: 1,
      display: 'flex',
      flexDirection: 'column',
      border: '1px solid var(--ui-stroke-secondary)',
      minWidth: 0,
    }
    const listStyle = { flex: 1, overflowY: 'auto', margin: 0, padding: 0, listStyle: 'none' }
    return el(
      'div',
      { style: containerStyle, onMouseDown: () => setActive(side) },
      renderPathBar(side),
      el('ul', {
        style: listStyle,
        onMouseDown: () => setActive(side),
        ref: (n) => (listRefs.current[side] = n),
      }, items)
    )
  }

  function renderToolbar() {
    const btn = (label, onClick) =>
      el('button', { key: label, onClick, style: { ...btnStyle, marginRight: '6px' } }, label)
    return el(
      'div',
      { style: { padding: '6px 0', display: 'flex', gap: '4px' } },
      [
        btn('F5 Copy', () => runCopyMove('/copy', panels[otherSide(active)].path)),
        btn('F6 Move', () => runCopyMove('/move', panels[otherSide(active)].path)),
        btn('F8 Delete', trashSelection),
        btn('Ctrl+Shift+F5 Symlink', startSymlink),
        btn('＋ Add root', startAddRoot),
      ]
    )
  }

  function renderNotice() {
    if (!notice) return null
    const style = {
      padding: '6px',
      color: notice.kind === 'error' ? 'var(--ui-red, #e5484d)' : 'var(--ui-text-secondary)',
    }
    return el('div', { style }, notice.text)
  }

  function renderContextMenu() {
    if (!menu) return null
    const side = menu.side
    const sel = selectedPaths(side)
    const entry = sel.length === 1 ? entryFor(side, sel[0]) : null

    const item = (label, onClick) =>
      el(
        'div',
        {
          key: label,
          onClick,
          style: {
            padding: '5px 12px',
            cursor: 'pointer',
            whiteSpace: 'nowrap',
          },
        },
        label
      )

    const items = []
    if (entry) {
      items.push(
        item('Open', () => navigate(side, entry)),
        item('Copy (F5)', contextAction(() => runCopyMove('/copy', panels[otherSide(side)].path))),
        item('Move (F6)', contextAction(() => runCopyMove('/move', panels[otherSide(side)].path))),
        item('Delete (F8)', contextAction(trashSelection)),
        item('Symlink (Ctrl+Shift+F5)', contextAction(startSymlink)),
        item('Copy Path', contextAction(() => copyPath(entry.path))),
        item('Open Terminal here (F9)', contextAction(() => openTerminalHere(entry.is_dir ? entry.path : panels[side].path)))
      )
    } else {
      items.push(item('Copy (F5)', contextAction(() => runCopyMove('/copy', panels[otherSide(side)].path))))
      items.push(item('Move (F6)', contextAction(() => runCopyMove('/move', panels[otherSide(side)].path))))
      items.push(item('Delete (F8)', contextAction(trashSelection)))
      items.push(item('Copy Path', contextAction(() => copyPath(panels[side].path))))
      items.push(item('Open Terminal here (F9)', contextAction(() => openTerminalHere(panels[side].path))))
    }

    const menuStyle = {
      position: 'fixed',
      left: menu.x,
      top: menu.y,
      zIndex: 9999,
      background: 'var(--ui-bg-elevated)',
      border: '1px solid var(--ui-stroke-secondary)',
      borderRadius: '6px',
      boxShadow: '0 4px 12px rgba(0,0,0,0.3)',
      padding: '4px 0',
      minWidth: '180px',
      color: 'var(--ui-text-primary)',
    }
    const backdropStyle = {
      position: 'fixed',
      inset: 0,
      zIndex: 9998,
    }

    return el(
      'div',
      null,
      el('div', { key: 'backdrop', style: backdropStyle, onClick: closeContextMenu }),
      el('div', { key: 'menu', style: menuStyle }, items)
    )
  }

  function renderConflictDialog() {
    if (!dialog || dialog.kind !== 'conflict') return null
    const rows = (dialog.conflicts || []).map((c) => {
      const dec = dialog.decisions[c.source] || ''
      const pick = (val) =>
        el(
          'button',
          {
            key: val,
            onClick: () => submitConflictDecision(c, val),
            style: {
              ...btnStyle,
              marginRight: '4px',
              fontWeight: dec === val ? 700 : 400,
              background: dec === val ? 'var(--ui-accent)' : 'var(--ui-bg-elevated)',
              color: dec === val ? '#ffffff' : 'var(--ui-text-primary)',
            },
          },
          val
        )
      return el(
        'div',
        { key: c.source, style: { padding: '4px 0' } },
        el('span', { style: { marginRight: '8px' } }, c.source),
        pick('skip'),
        pick('overwrite'),
        pick('rename')
      )
    })
    const overlayStyle = {
      position: 'fixed',
      inset: 0,
      background: 'rgba(0,0,0,0.4)',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
    }
    const boxStyle = {
      background: 'var(--ui-bg-elevated)',
      padding: '16px',
      border: '1px solid var(--ui-stroke-secondary)',
      maxHeight: '70vh',
      overflowY: 'auto',
    }
    return el(
      'div',
      { style: overlayStyle },
      el(
        'div',
        { style: boxStyle },
        el('h3', null, 'Resolve conflicts (' + (dialog.conflicts || []).length + ')'),
        rows,
        el(
          'div',
          { style: { marginTop: '12px' } },
          el('button', { style: btnStyle, onClick: confirmConflictDialog }, 'Apply'),
          el('button', { style: { ...btnStyle, marginLeft: '8px' }, onClick: () => setDialog(null) }, 'Cancel')
        )
      )
    )
  }

  function renderSymlinkDialog() {
    if (!dialog || dialog.kind !== 'symlink') return null
    const overlayStyle = {
      position: 'fixed',
      inset: 0,
      background: 'rgba(0,0,0,0.4)',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
    }
    const boxStyle = {
      background: 'var(--ui-bg-elevated)',
      padding: '16px',
      border: '1px solid var(--ui-stroke-secondary)',
    }
    const radio = (val) =>
      el(
        'label',
        { key: val, style: { display: 'block' } },
        el('input', {
          type: 'radio',
          name: 'link_type',
          checked: dialog.linkType === val,
          onChange: () => setDialog({ ...dialog, linkType: val }),
        }),
        val
      )
    return el(
      'div',
      { style: overlayStyle },
      el(
        'div',
        { style: boxStyle },
        el('h3', null, 'Create symlinks'),
        radio('relative'),
        radio('absolute'),
        el(
          'div',
          { style: { marginTop: '12px' } },
          el('button', { style: btnStyle, onClick: confirmSymlink }, 'Create'),
          el('button', { style: { ...btnStyle, marginLeft: '8px' }, onClick: () => setDialog(null) }, 'Cancel')
        )
      )
    )
  }

  function renderTrashDialog() {
    if (!dialog || dialog.kind !== 'trash') return null
    const overlayStyle = {
      position: 'fixed',
      inset: 0,
      background: 'rgba(0,0,0,0.4)',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
    }
    const boxStyle = {
      background: 'var(--ui-bg-elevated)',
      padding: '16px',
      border: '1px solid var(--ui-stroke-secondary)',
      maxWidth: '480px',
      maxHeight: '70vh',
      overflowY: 'auto',
    }
    const name = (p) => (p || '').split('/').filter(Boolean).pop() || p
    const rows = (dialog.paths || []).map((p) =>
      el('div', { key: p, style: { fontFamily: 'monospace', fontSize: '12px', padding: '1px 0' } }, name(p))
    )
    const count = (dialog.paths || []).length
    const label =
      count === 1 ? 'Move this item to trash?' : 'Move these ' + count + ' items to trash?'
    return el(
      'div',
      { style: overlayStyle },
      el(
        'div',
        { style: boxStyle },
        el('h3', null, 'Delete'),
        el('p', { style: { margin: '4px 0 8px' } }, label),
        rows,
        el(
          'div',
          { style: { marginTop: '12px' } },
          el('button', { style: btnStyle, onClick: confirmTrash }, 'Delete'),
          el('button', { style: { ...btnStyle, marginLeft: '8px' }, onClick: () => setDialog(null) }, 'Cancel')
        )
      )
    )
  }

  function renderAddRootDialog() {
    if (!dialog || dialog.kind !== 'add-root') return null
    const overlayStyle = {
      position: 'fixed',
      inset: 0,
      background: 'rgba(0,0,0,0.4)',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
    }
    const boxStyle = {
      background: 'var(--ui-bg-elevated)',
      padding: '16px',
      border: '1px solid var(--ui-stroke-secondary)',
      width: '420px',
      maxWidth: '90vw',
    }
    return el(
      'div',
      { style: overlayStyle },
      el(
        'div',
        { style: boxStyle },
        el('h3', null, 'Add root'),
        el('p', { style: { margin: '4px 0 8px' } }, 'Enter an absolute directory path:'),
        el('input', {
          type: 'text',
          autoFocus: true,
          value: dialog.value || '',
          placeholder: '/home/andreas/...',
          onChange: (e) => setDialog({ ...dialog, value: e.target.value }),
          onKeyDown: (e) => {
            if (e.key === 'Enter') confirmAddRoot()
            if (e.key === 'Escape') setDialog(null)
          },
          style: {
            width: '100%',
            boxSizing: 'border-box',
            background: 'var(--ui-bg-elevated)',
            color: 'var(--ui-text-primary)',
            border: '1px solid var(--ui-stroke-secondary)',
            borderRadius: '4px',
            padding: '6px 8px',
            fontSize: '13px',
          },
        }),
        el(
          'div',
          { style: { marginTop: '12px' } },
          el('button', { style: btnStyle, onClick: confirmAddRoot }, 'Add'),
          el('button', { style: { ...btnStyle, marginLeft: '8px' }, onClick: () => setDialog(null) }, 'Cancel')
        )
      )
    )
  }

  const root = {
    display: 'flex',
    flexDirection: 'column',
    height: '100%',
  }
  const panelsRow = { display: 'flex', gap: '8px', flex: 1, minHeight: 0 }

  return el(
    'div',
    { style: root, tabIndex: 0, onKeyDown, 'data-context-menu-skip': '' },
    renderToolbar(),
    el('div', { style: panelsRow }, renderPanel('left'), renderPanel('right')),
    renderNotice(),
    renderConflictDialog(),
    renderSymlinkDialog(),
    renderTrashDialog(),
    renderAddRootDialog(),
    renderContextMenu()
  )
}

// ---------------------------------------------------------------------------
// Plugin export — the correct desktop SDK contract: a default-exported object
// with id/name/register(ctx). Contributions are wired via ctx.register(...).
// ---------------------------------------------------------------------------
export default {
  id: 'filebox',
  name: 'Filebox',
  defaultEnabled: false,
  register(ctx) {
    ctx.register({
      id: 'pane',
      area: 'panes',
      title: 'Filebox',
      data: { placement: 'main' },
      render: () => jsx(FileboxPane, { ctx }),
    })
  },
}
