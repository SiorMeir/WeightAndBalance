import {fields, titles, parseRequest, signature, isStale, resultHeading, centrograms} from './model.mjs';
import {CentrogramChart, escapeHTML as esc} from './chart.mjs';

const $ = id => document.getElementById(id);
const number = v => v == null ? '—' : v.toLocaleString(undefined, {maximumFractionDigits: 2});
let request, examples = [], units = null, label = '', cleanSignature = '', submitted = null, result = null;
let resultUnits = null, curves = [], selected = null, pinned = false, busy = false, section, draft, errors = [], activeExampleId = '';
const chart = new CentrogramChart($('chart'), i => selectLoading(i, true), i => {
  if (!pinned && i !== null) renderDetails(i);
  else if (i === null) renderDetails(selected);
}, error => showMessage(`Chart could not be drawn: ${error.message}`));

function showMessage(message, details = [], requestId = '') {
  const target = $('message');
  target.hidden = !message;
  target.replaceChildren();
  if (!message) return;
  const text = document.createElement('div'); text.textContent = message; target.append(text);
  if (details.length) {
    const list = document.createElement('ul');
    details.forEach(detail => {
      const li = document.createElement('li');
      const key = detail.field.split('.')[0];
      if (titles[key]) {
        const link = document.createElement('a'); link.href = '#'; link.textContent = `${detail.field}: ${detail.problem}`;
        link.onclick = event => { event.preventDefault(); openEditor(key); }; li.append(link);
      } else li.textContent = `${detail.field}: ${detail.problem}`;
      list.append(li);
    });
    target.append(list);
  }
  if (requestId) { const p = document.createElement('p'); p.textContent = `Request ID: ${requestId}`; target.append(p); }
}

function dirty() { return request && signature(request) !== cleanSignature; }
function allowReplace() {
  if (!dirty()) return Promise.resolve(true);
  return new Promise(resolve => {
    const dialog = $('replace-dialog');
    dialog.returnValue = 'cancel';
    dialog.addEventListener('close', () => resolve(dialog.returnValue === 'replace'), {once: true});
    dialog.showModal();
  });
}
function loadScenario(data, name, displayUnits, exampleId = '') {
  request = parseRequest(data); label = name; units = displayUnits;
  activeExampleId = exampleId; $('example').value = exampleId;
  cleanSignature = signature(request); submitted = null; result = null; resultUnits = null; curves = []; selected = null; pinned = false; errors = [];
  $('all').checked = request.options.all_centrograms;
  $('loading').replaceChildren(new Option('Run a simulation first', '')); $('loading').disabled = true;
  $('empty-chart').hidden = false;
  $('metrics').innerHTML = '<div><span>Robust fuel cap</span><strong>—</strong><small>Run a simulation to calculate</small></div><div><span>Full fuel</span><strong id="preview-full">—</strong><small>From the input trajectory</small></div><div><span>Loadings evaluated</span><strong>—</strong><small>Exact evaluations / total</small></div>';
  renderTimings(); $('request-id').textContent = ''; $('stats').textContent = 'Results are calculated by the REST API.';
  $('curve-count').textContent = 'Envelope preview · no simulation run';
  showMessage(''); renderInputs(); renderDetails(null); updateStatus();
  chart.setData(request, [], null, units, true);
}

function renderInputs() {
  $('scenario-note').textContent = `${label}. Edit the inputs, then run a simulation.`;
  $('edited').textContent = dirty() ? 'Edited' : 'Loaded';
  $('units').textContent = units ? `Weight: ${units.weight} · CG: ${units.cg} · Moment: ${units.weight}·${units.cg}. Keep one consistent datum.` : 'Units are unspecified. Use consistent weight, CG, and true weight × arm moment units.';
  const descriptions = {
    envelope: `${request.envelope.length} vertices · convex polygon`,
    tails: `${request.tails.length} aircraft ${request.tails.length === 1 ? 'case' : 'cases'}`,
    items: `${request.items.length} items · ${request.items.filter(i => i.optional).length} optional`,
    fuel_flow: `${request.fuel_flow.length} waypoints · ${number(request.fuel_flow.at(-1).weight)} full fuel`,
    options: `${number(request.options.max_steps)} steps · ${request.options.all_centrograms ? 'all' : 'critical'} centrograms`,
  };
  $('sections').innerHTML = Object.keys(titles).map((key, i) => {
    const rows = key === 'tails' ? request.tails.map(r => [r.id, `${number(r.weight)} @ ${number(r.cg)}`]) :
      key === 'items' ? request.items.map(r => [r.name, `${number(r.weight)}${r.weight_delta ? ` ± ${number(r.weight_delta)}` : ''}`]) : [];
    const list = rows.slice(0, 5).map(([name, value]) => `<li data-source="${esc(key)}" data-name="${esc(name)}"><span>${esc(name)}</span><span>${esc(value)}</span></li>`).join('');
    return `<section class="input-section"><button class="summary-button" data-edit="${key}" aria-haspopup="dialog"><span class="section-index">${String(i + 1).padStart(2, '0')}</span><span class="section-copy"><strong>${titles[key]}</strong><small>${esc(descriptions[key])}</small></span><span class="expand" aria-hidden="true">+</span></button>${list ? `<ul class="summary-list">${list}${rows.length > 5 ? `<li>+ ${rows.length - 5} more in editor</li>` : ''}</ul>` : ''}</section>`;
  }).join('');
  document.querySelectorAll('[data-edit]').forEach(button => button.onclick = () => openEditor(button.dataset.edit));
  if ($('preview-full')) $('preview-full').textContent = number(request.fuel_flow.at(-1).weight);
  highlightSources(selected);
}

function updateStatus() {
  const stale = submitted && isStale(request, submitted);
  $('status').textContent = busy ? 'Running…' : stale ? 'Inputs changed · rerun needed' : result?.status === 'incomplete' ? 'Incomplete · unproven' : result?.status === 'ok' ? 'Complete' : result?.status === 'error' ? 'Run failed' : 'Ready to run';
  $('status').classList.toggle('active', Boolean(result) || busy);
  $('all').disabled = !result || result.status === 'error' || !result.all_centrograms;
  $('all').parentElement.title = $('all').disabled ? 'Request all centrograms in Simulation options, then run.' : 'Switch between all and critical centrograms from this result.';
  $('all').checked = Boolean(result?.all_centrograms) && $('all').checked;
  $('run').disabled = busy || !request;
  $('run').textContent = busy ? 'Running…' : 'Run simulation';
  $('example').disabled = busy; $('import').disabled = busy; $('export').disabled = !request;
}

const help = {
  envelope: 'Ordered (CG, weight) vertices around a convex polygon. Either orientation is accepted.',
  tails: 'One basic-aircraft state per unique tail number. The result is a single cap for every tail.',
  items: 'Weight ± and CG ± are independent endpoint choices. Optional items also permit absence.',
  fuel_flow: 'Cumulative fuel weight and true moment, in fill order. Start at (0, 0); weights must strictly increase.',
  options: 'All centrograms enumerates every loading (up to 10,000). Max steps bounds the branch-and-bound search.',
};
function openEditor(key) {
  section = key; draft = structuredClone(request[key]);
  $('editor-title').textContent = titles[key]; $('editor-help').textContent = help[key];
  $('editor-error').hidden = true; $('add-row').hidden = key === 'options';
  renderEditor(); $('editor').showModal();
}

function renderEditor() {
  if (section === 'options') {
    $('editor-content').innerHTML = `<div class="options-editor"><label><input type="checkbox" data-option="all_centrograms" ${draft.all_centrograms ? 'checked' : ''}> Request all centrograms (maximum 10,000 loadings)</label><label>Max steps <input type="number" min="1" max="100000" step="1" data-option="max_steps" value="${draft.max_steps}" required></label></div>`;
    document.querySelectorAll('[data-option]').forEach(input => input.oninput = () => { draft[input.dataset.option] = input.type === 'checkbox' ? input.checked : input.valueAsNumber; });
  } else {
    const columns = fields[section];
    $('editor-content').innerHTML = `<table class="edit-table"><thead><tr><th scope="col">#</th>${columns.map(([, label]) => `<th scope="col">${label}</th>`).join('')}<th scope="col">Row actions</th></tr></thead><tbody>${draft.map((row, i) => `<tr data-editor-row="${i}"><td>${i + 1}</td>${columns.map(([key, label, type]) => `<td><input type="${type}" ${type === 'number' ? 'step="any"' : ''} data-row="${i}" data-key="${key}" aria-label="Row ${i + 1}: ${label}" ${type === 'checkbox' ? (row[key] ? 'checked' : '') : `value="${esc(row[key] ?? '')}" required`}></td>`).join('')}<td class="row-actions"><button type="button" data-move="-1" data-row="${i}" aria-label="Move row ${i + 1} up" ${i === 0 ? 'disabled' : ''}>↑</button><button type="button" data-move="1" data-row="${i}" aria-label="Move row ${i + 1} down" ${i === draft.length - 1 ? 'disabled' : ''}>↓</button><button type="button" data-delete="${i}" aria-label="Delete row ${i + 1}">Delete</button></td></tr>`).join('')}</tbody></table>`;
    $('editor-content').querySelectorAll('input').forEach(input => input.oninput = () => {
      draft[Number(input.dataset.row)][input.dataset.key] = input.type === 'checkbox' ? input.checked : input.type === 'number' ? input.valueAsNumber : input.value;
      input.removeAttribute('aria-invalid');
    });
    $('editor-content').querySelectorAll('[data-move]').forEach(button => button.onclick = () => {
      const from = Number(button.dataset.row), to = from + Number(button.dataset.move);
      [draft[from], draft[to]] = [draft[to], draft[from]]; renderEditor();
    });
    $('editor-content').querySelectorAll('[data-delete]').forEach(button => button.onclick = () => { draft.splice(Number(button.dataset.delete), 1); renderEditor(); });
  }
  const relevant = errors.filter(e => e.field.split('.')[0] === section);
  relevant.forEach(error => {
    const [, row, key] = error.field.split('.');
    const input = section === 'options' ? $('editor-content').querySelector(`[data-option="${key ?? row}"]`) :
      [...$('editor-content').querySelectorAll('input')].find(el => el.dataset.row === row && el.dataset.key === key);
    if (input) {
      input.setAttribute('aria-invalid', 'true');
      const note = document.createElement('small'); note.className = 'field-error'; note.textContent = error.problem;
      input.parentElement.append(note);
    }
  });
  if (relevant.length) { $('editor-error').hidden = false; $('editor-error').textContent = relevant.map(e => `${e.field}: ${e.problem}`).join(' '); }
  highlightSources(selected);
}

$('add-row').onclick = () => {
  draft.push(Object.fromEntries(fields[section].map(([key, , type]) => [key, type === 'checkbox' ? false : type === 'number' ? 0 : ''])));
  errors = []; renderEditor(); $('editor-content').querySelector('tbody tr:last-child input')?.focus();
};
for (const id of ['close-editor', 'cancel-editor']) $(id).onclick = () => $('editor').close();
$('edit-form').onsubmit = event => {
  event.preventDefault();
  try {
    request = parseRequest({...request, [section]: draft}); errors = [];
    $('editor').close(); showMessage(''); renderInputs(); updateStatus();
    if (!result) chart.setData(request, [], null, units, true);
  } catch (error) { errors = error.details ?? []; renderEditor(); }
};

function highlightSources(index) {
  const curve = curves[index];
  document.querySelectorAll('[data-source]').forEach(el => {
    const matches = curve && (el.dataset.source === 'tails' ? el.dataset.name === curve.tail : curve.items.some(i => i.name === el.dataset.name));
    el.classList.toggle('source-match', Boolean(matches));
  });
  if ($('editor').open && ['tails', 'items'].includes(section)) {
    document.querySelectorAll('[data-editor-row]').forEach(el => {
      const row = draft[Number(el.dataset.editorRow)];
      el.classList.toggle('source-match', Boolean(curve && (section === 'tails' ? row.id === curve.tail : curve.items.some(i => i.name === row.name))));
    });
  }
}

function renderDetails(index) {
  const curve = curves[index];
  if (!curve) { $('details').className = 'details-empty'; $('details').textContent = 'Hover to inspect a curve. Click to pin its loading, or choose it from the list.'; return; }
  $('details').className = '';
  const tail = submitted.tails.find(t => t.id === curve.tail);
  const row = (name, weight, cg, status, absent = false) => `<tr${absent ? ' class="absent"' : ''}><td>${esc(name)}</td><td>${esc(status)}</td><td>${number(weight)}</td><td>${number(cg)}</td></tr>`;
  $('details').innerHTML = `<p class="loading-summary"><strong>${esc(curve.tail)}</strong><span>${curve.binding ? 'Limiting loading' : 'Evaluated loading'}</span><span>Individual cap ${number(curve.cap)}</span><span>${pinned ? 'Pinned' : 'Click a curve to pin'}</span></p><div class="table-scroll" style="max-height:250px;margin:0"><table class="detail-table"><thead><tr><th scope="col">Component</th><th scope="col">Presence</th><th scope="col">Weight</th><th scope="col">CG</th></tr></thead><tbody>${tail ? row('Basic aircraft', tail.weight, tail.cg, 'Present') : ''}${curve.items.map(i => row(i.name, i.weight, i.cg, i.present ? 'Present' : 'Absent', !i.present)).join('')}${row('Zero fuel total', curve.zero_fuel.weight, curve.zero_fuel.cg, 'Total')}</tbody></table></div>`;
  highlightSources(index);
}

function selectLoading(index, pin = false) {
  selected = index; pinned = pin; $('loading').value = String(index); renderDetails(index);
  chart.setData(submitted, curves, selected, resultUnits);
}

function renderTimings(browserMS = null) {
  const timing = result?.timing;
  const values = [timing?.solve_ms, timing?.centrograms_ms, timing?.total_ms, browserMS];
  $('timings').innerHTML = ['Solver', 'Centrograms', 'API total', 'Browser request'].map((title, i) => `<div><span>${title}</span><strong>${values[i] == null ? '—' : `${number(values[i])} ms`}</strong></div>`).join('');
}

function showCurves(reset = false) {
  curves = centrograms(result, $('all').checked);
  $('loading').replaceChildren(...curves.map((c, i) => new Option(`${String(i + 1).padStart(2, '0')} · ${c.tail} · cap ${number(c.cap)}${c.binding ? ' · limiting' : ''}`, String(i))));
  $('loading').disabled = !curves.length;
  selected = curves.length ? 0 : null; pinned = false; renderDetails(selected);
  $('curve-count').textContent = `${number(curves.length)} ${$('all').checked ? 'of ' + number(result.stats.loadings) : 'critical'} centrograms · ${chart.layers.curves ? 'displayed' : 'hidden'}`;
  chart.setData(submitted, curves, selected, resultUnits, reset);
}

$('run').onclick = async () => {
  if (busy) return;
  submitted = structuredClone(request); resultUnits = structuredClone(units); busy = true; result = null; curves = []; selected = null; errors = [];
  showMessage(''); updateStatus(); $('empty-chart').hidden = true;
  $('loading').replaceChildren(new Option('Running simulation…', '')); $('loading').disabled = true;
  renderDetails(null); renderTimings(); $('request-id').textContent = ''; $('stats').textContent = 'Waiting for the REST API…';
  $('metrics').innerHTML = '<div><span>Robust fuel cap</span><strong>—</strong><small>Calculating</small></div><div><span>Full fuel</span><strong>—</strong></div><div><span>Loadings evaluated</span><strong>—</strong></div>';
  chart.setData(submitted, [], null, resultUnits, true);
  const started = performance.now();
  try {
    const response = await fetch('../standard-calc', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(submitted)});
    const data = await response.json();
    const duration = performance.now() - started;
    result = data;
    if (!response.ok || data.status === 'error') {
      errors = data.error?.details ?? [];
      showMessage(data.error?.message ?? `Server returned HTTP ${response.status}.`, errors, data.request_id);
      $('loading').replaceChildren(new Option('No result for this run', ''));
      $('metrics').innerHTML = '<div><span>Run failed</span><strong>—</strong><small>Check the error above</small></div><div><span>Full fuel</span><strong>—</strong></div><div><span>Loadings evaluated</span><strong>—</strong></div>';
      $('curve-count').textContent = 'Envelope preview · calculation failed';
      $('stats').textContent = 'Calculation failed. Correct the inputs and run again.';
    } else {
      const incomplete = data.status === 'incomplete';
      const cap = incomplete ? data.fuel_cap_upper_bound : data.fuel_cap;
      $('metrics').innerHTML = `<div><span>${resultHeading(data)}</span><strong>${number(cap)}</strong><small>${incomplete ? 'Step budget exhausted · not certified' : 'Safe fuel prefix for all modelled loadings'}</small></div><div><span>Full fuel</span><strong>${number(data.full_fuel)}</strong><small>${resultUnits?.weight ? esc(resultUnits.weight) : 'Consistent weight units'}</small></div><div><span>Loadings evaluated</span><strong>${number(data.stats.exact_evaluations)} <span style="display:inline;font-size:17px">/ ${number(data.stats.loadings)}</span></strong><small>Exact evaluations / total loadings</small></div>`;
      if (incomplete) showMessage('Step budget exhausted. This is an unproven upper bound, not a certified robust fuel cap. Increase max steps and rerun.');
      $('all').checked = submitted.options.all_centrograms; showCurves(true);
      $('stats').textContent = `${number(data.stats.certificates)} certificates · ${number(data.stats.pruned)} pruned · ${number(data.stats.steps)} steps`;
    }
    renderTimings(duration); $('request-id').textContent = `Request ${data.request_id ?? 'unknown'}`;
  } catch (error) {
    result = {status: 'error'};
    showMessage(`Simulation request failed: ${error.message}. Your inputs are preserved.`);
    $('metrics').innerHTML = '<div><span>Run failed</span><strong>—</strong><small>Run again to retry</small></div><div><span>Full fuel</span><strong>—</strong></div><div><span>Loadings evaluated</span><strong>—</strong></div>';
    $('loading').replaceChildren(new Option('No result for this run', ''));
    $('stats').textContent = 'Request failed. Run again to retry.'; $('curve-count').textContent = 'Envelope preview · request failed';
  } finally { busy = false; updateStatus(); }
};

$('example').onchange = async () => {
  if (!await allowReplace()) { $('example').value = activeExampleId; return; }
  const example = examples.find(e => e.id === $('example').value);
  if (example) loadScenario(example.request, example.label, example.units, example.id);
  else $('example').value = activeExampleId;
};
$('import').onclick = () => $('file').click();
$('file').onchange = async () => {
  const file = $('file').files[0]; if (!file) return;
  try {
    const parsed = parseRequest(await file.text());
    if (await allowReplace()) { loadScenario(parsed, file.name, null); $('example').value = ''; }
  } catch (error) { showMessage(`Import failed: ${error.message}`, error.details); }
  finally { $('file').value = ''; }
};
$('export').onclick = () => {
  const url = URL.createObjectURL(new Blob([JSON.stringify(request, null, 2) + '\n'], {type: 'application/json'}));
  const link = document.createElement('a'); link.href = url; link.download = 'weight-and-balance.json'; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000); cleanSignature = signature(request); renderInputs();
};
$('loading').onchange = () => selectLoading(Number($('loading').value), true);
$('all').onchange = () => showCurves(true);
for (const layer of ['envelope', 'curves']) $(`layer-${layer}`).onchange = () => {
  chart.layers[layer] = $(`layer-${layer}`).checked; chart.draw();
  if (result?.stats) $('curve-count').textContent = `${number(curves.length)} ${$('all').checked ? 'of ' + number(result.stats.loadings) : 'critical'} centrograms · ${chart.layers.curves ? 'displayed' : 'hidden'}`;
};
$('reset').onclick = () => { chart.ranges = null; chart.draw(); };
window.addEventListener('beforeunload', event => { if (dirty()) { event.preventDefault(); event.returnValue = ''; } });

try {
  const response = await fetch('examples');
  if (!response.ok) throw new Error(`Examples returned HTTP ${response.status}`);
  examples = await response.json();
  $('example').replaceChildren(new Option('Imported scenario', ''), ...examples.map(e => new Option(e.label, e.id)));
  $('example').value = 'light-twin';
  if (!window.Plotly && document.readyState !== 'complete') await new Promise(resolve => window.addEventListener('load', resolve, {once: true}));
  if (!window.Plotly) throw new Error('Plotly asset did not load. Reload the page to retry.');
  const example = examples.find(e => e.id === 'light-twin'); loadScenario(example.request, example.label, example.units, example.id);
} catch (error) { showMessage(`Could not start the explorer: ${error.message}`); }
