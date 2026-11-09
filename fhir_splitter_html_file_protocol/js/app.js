
let pyodide = null;

async function ensurePyodide() {
  if (pyodide) return pyodide;
  pyodide = await loadPyodide({ indexURL: "https://cdn.jsdelivr.net/pyodide/v0.26.1/full/" });
  const code = document.getElementById('py-splitter').textContent;
  pyodide.FS.writeFile('splitter.py', code, { encoding: 'utf8' });
  await pyodide.runPythonAsync('import importlib, sys; sys.path.append("."); splitter = importlib.import_module("splitter")');
  document.getElementById('status').textContent = 'Ready';
  return pyodide;
}

async function analyzeFile(file) {
  await ensurePyodide();
  const text = await file.text();
  const py = `
import json
from splitter import collect_resources_except_bundle, group_by_resource_type, ui_mapping, build_zip_bytes
data = json.loads(js_text)
resources = collect_resources_except_bundle(data, [])
grouped = group_by_resource_type(resources)
_mapping = ui_mapping(grouped)
_zip = build_zip_bytes(grouped)
`;
  pyodide.globals.set("js_text", text);
  await pyodide.runPythonAsync(py);
  const mapping = pyodide.globals.get("_mapping").toJs({ dict_converter: Object.fromEntries });
  const zipBytes = pyodide.globals.get("_zip").toJs();
  pyodide.globals.delete("js_text"); pyodide.globals.delete("_mapping"); pyodide.globals.delete("_zip");
  return { mapping, zipBytes };
}

function renderSummary(map) {
  const results = document.getElementById('results');
  results.innerHTML = '';
  const types = Object.keys(map || {});
  if (!types.length) {
    results.innerHTML = '<div class="muted">No non‑Bundle resources found.</div>';
    return;
  }
  types.forEach(rt => {
    const block = document.createElement('div'); block.className = 'rt-block';
    const header = document.createElement('div'); header.className = 'rt-header';
    header.innerHTML = `<div class="rt-title">${rt}</div>`; block.appendChild(header);

    const files = document.createElement('div'); files.className = 'files mono';
    const parentRow = document.createElement('div'); parentRow.textContent = `Parent file: Output/${rt}/parent.json`; files.appendChild(parentRow);
    const cHeader = document.createElement('div'); cHeader.textContent = 'Children file:'; files.appendChild(cHeader);
    (map[rt].children_files || []).forEach(p => { const row = document.createElement('div'); row.textContent = ` - ${p}`; files.appendChild(row); });
    const namesRow = document.createElement('div'); namesRow.textContent = `Child tables (${map[rt].child_tables.length}): ${map[rt].child_tables.join(', ')}`; files.appendChild(namesRow);
    block.appendChild(files); results.appendChild(block);
  });
}

let lastZipBytes = null;

window.addEventListener('DOMContentLoaded', () => {
  const fileEl = document.getElementById('file');
  const runBtn = document.getElementById('run');
  const dlBtn = document.getElementById('download-all');
  const statusEl = document.getElementById('status');
  const errEl = document.getElementById('err');

  runBtn.addEventListener('click', async () => {
    const f = fileEl.files[0];
    if (!f) { errEl.textContent = 'Choose a JSON file first.'; return; }
    errEl.textContent = ''; statusEl.textContent = 'Running…';
    try {
      const { mapping, zipBytes } = await analyzeFile(f);
      renderSummary(mapping);
      lastZipBytes = zipBytes; dlBtn.disabled = false; statusEl.textContent = 'Ready';
    } catch (e) {
      console.error(e); errEl.textContent = 'Failed to analyze. See console for details.'; statusEl.textContent = 'Error';
    }
  });

  dlBtn.addEventListener('click', () => {
    if (!lastZipBytes) return;
    const blob = new Blob([new Uint8Array(lastZipBytes)], { type: 'application/zip' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a'); a.href = url; a.download = 'Output.zip'; a.click();
    URL.revokeObjectURL(url);
  });

  ensurePyodide().catch(err => { console.error(err); statusEl.textContent = 'Pyodide failed to load'; });
});
