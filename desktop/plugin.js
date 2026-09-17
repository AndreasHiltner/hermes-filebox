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

function fmtMtime(mtime) {
  if (!mtime) return ''
  return new Date(mtime * 1000).toLocaleString()
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
  const [active, setActive] = useState('left')
  const [selected, setSelected] = useState({ left: {}, right: {} })
  const [dialog, setDialog] = useState(null)
  const [notice, setNotice] = useState(null)

  const loadRoots = useCallback(async () => {
    const r = await listRoots(ctx)
    return r.roots
  }, [ctx])

  const loadPanel = useCallback(
    async (side, path) => {
      const r = await listPath(ctx, path)
      setPanels((prev) => ({ ...prev, [side]: { path, entries: r.entries } }))
    },
    [ctx]
  )

  useEffect(() => {
    ;(async () => {
      try {
        const rs = await loadRoots()
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

  const selectedPaths = (side) => {
    const paths = Object.keys(selected[side]).filter((k) => selected[side][k])
    return paths.length ? paths : [panels[side].path].filter(Boolean)
  }

  async function navigate(side, entry) {
    setActive(side)
    if (entry.is_dir) {
      await loadPanel(side, entry.path)
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
    setSelected((prev) => {
      const next = { ...prev[side] }
      if (next[path]) delete next[path]
      else next[path] = true
      return { ...prev, [side]: next }
    })
  }

  function activate(side) {
    setActive(side)
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
    const decisions = (d.conflicts || []).map((c) => ({
      source: c.source,
      decision: d.decisions[c.source] || 'skip',
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
    const resp = await del(ctx, '/trash', { paths: sel })
    setNotice(summarizeResults(resp.results, resp.errors))
    await refreshAfter(sel, active)
  }

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
    } else if (e.key === 'Enter') {
      e.preventDefault()
      const sel = selectedPaths(active)
      if (sel.length === 1) {
        const entry = panels[active].entries.find((x) => x.path === sel[0])
        if (entry) navigate(active, entry)
      }
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
    const style = {
      color: active === side ? 'var(--ui-accent)' : 'var(--ui-text-secondary)',
      cursor: 'pointer',
      fontWeight: active === side ? 600 : 400,
    }
    return el(
      'div',
      { style, onClick: () => activate(side), title: p.path || '(none)' },
      p.path || '(no root)'
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
    }
    const nameStyle = { flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }
    const metaStyle = { color: 'var(--ui-text-tertiary)', fontSize: '11px', whiteSpace: 'nowrap' }
    return el(
      'div',
      { key: entry.path, style: rowStyle },
      el(
        'input',
        {
          type: 'checkbox',
          checked: isSel,
          onChange: () => toggleSelect(side, entry.path),
        }
      ),
      el('span', null, entry.is_dir ? '\u{1F4C1}' : '\u{1F4C4}'),
      el(
        'span',
        { style: nameStyle, onClick: () => navigate(side, entry) },
        entry.name
      ),
      el('span', { style: metaStyle }, fmtSize(entry.size)),
      el('span', { style: metaStyle }, fmtMtime(entry.mtime))
    )
  }

  function renderPanel(side) {
    const p = panels[side]
    const entries = (p.entries || []).map((e) => renderEntry(side, e))
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
      { style: containerStyle },
      renderPathBar(side),
      el('ul', { style: listStyle }, entries)
    )
  }

  function renderToolbar() {
    const btn = (label, onClick) =>
      el(
        'button',
        { key: label, onClick, style: { marginRight: '6px' } },
        label
      )
    return el(
      'div',
      { style: { padding: '6px 0', display: 'flex', gap: '4px' } },
      [
        btn('F5 Copy', () => runCopyMove('/copy', panels[otherSide(active)].path)),
        btn('F6 Move', () => runCopyMove('/move', panels[otherSide(active)].path)),
        btn('F8 Delete', trashSelection),
        btn('Ctrl+Shift+F5 Symlink', startSymlink),
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
              marginRight: '4px',
              fontWeight: dec === val ? 700 : 400,
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
          el('button', { onClick: confirmConflictDialog }, 'Apply'),
          el('button', { onClick: () => setDialog(null), style: { marginLeft: '8px' } }, 'Cancel')
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
          el('button', { onClick: confirmSymlink }, 'Create'),
          el('button', { onClick: () => setDialog(null), style: { marginLeft: '8px' } }, 'Cancel')
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
    { style: root, tabIndex: 0, onKeyDown },
    renderToolbar(),
    el('div', { style: panelsRow }, renderPanel('left'), renderPanel('right')),
    renderNotice(),
    renderConflictDialog(),
    renderSymlinkDialog()
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
      data: { placement: 'right', width: '400px' },
      render: () => jsx(FileboxPane, { ctx }),
    })
  },
}
