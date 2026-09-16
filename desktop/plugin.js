// Hermes Filebox - desktop renderer (thin ESM, presentation-only).
// All filesystem I/O happens in dashboard/plugin_api.py; this file calls
// ctx.rest and renders. No network, no model tokens.

export default function plugin(ctx) {
  const state = { path: null, roots: [], entries: [] };

  async function loadRoots() {
    const r = await ctx.rest.get("/roots");
    state.roots = r.roots;
  }

  async function loadDir(path) {
    const r = await ctx.rest.get("/list", { params: { path } });
    state.path = path;
    state.entries = r.entries;
  }

  return {
    name: "filebox",
    onMount: async () => {
      await loadRoots();
      if (state.roots.length > 0) await loadDir(state.roots[0]);
    },
    render: () => {
      const rows = state.entries
        .map((e) => `<li data-path="${e.path}">${e.is_dir ? "📁" : "📄"} ${e.name}</li>`)
        .join("");
      return `<div class="filebox"><h2>Filebox</h2><ul>${rows}</ul></div>`;
    },
  };
}
