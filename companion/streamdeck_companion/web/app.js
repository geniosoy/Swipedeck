// Deck editor UI. Pure logic lives in model.js; this file builds the DOM (never from HTML strings: all text goes
// through textContent) and talks to the companion's small JSON API.
import * as M from './model.js';

const S = 2; // preview scale: the board screen is 320 x 240, shown at 640 x 480
const TOKEN = document.querySelector('meta[name="sd-token"]').content;
const $ = (sel) => document.querySelector(sel);
const SWATCHES = ['#2A3A4D', '#B3261E', '#C77700', '#2E7D32', '#1F6F5C', '#00838F', '#2F6FDB', '#5B3A8C', '#8E24AA', '#455A64'];
const KIND_ICON = { keys: 'keyboard', nowplaying: 'audio', stats: 'charge' };
const KIND_LABEL = { keys: 'Keys', nowplaying: 'Now playing', stats: 'PC stats' };
const ATTR_ONLY = new Set(['list', 'form']);

// ---- tiny DOM helper ---------------------------------------------------------------------------------

function h(tag, props, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k === 'class') el.className = v;
    else if (k === 'style' && typeof v === 'object') Object.assign(el.style, v);
    else if (k === 'dataset') Object.assign(el.dataset, v);
    else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2), v);
    else if (k in el && !ATTR_ONLY.has(k)) el[k] = v;
    else el.setAttribute(k, v === true ? '' : v);
  }
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c.nodeType ? c : document.createTextNode(String(c)));
  }
  return el;
}

// ---- state -------------------------------------------------------------------------------------------

const state = {
  deck: null, savedText: '', sel: { d: 0, p: 0, k: null }, icons: {}, presets: { builtin: [], user: [] },
  cat: 'all', query: '', status: null, clip: null, saving: false, capturing: false, sizeNote: '',
  templates: [], addMenu: false,
  sources: null, sourcesBusy: false, sourcesError: '', // media players found by "Detect" (Now playing filter)
};
const history = new M.History();
let lastCoalesce = { key: null, t: 0 };
let toastTimer = null;
let nextEditorId = 1;

const deckText = () => JSON.stringify(state.deck);
const isDirty = () => state.deck && deckText() !== state.savedText;
const desk = () => state.deck.desktops[state.sel.d];
const isKeys = () => M.desktopKind(desk()) === 'keys';
const page = () => (isKeys() ? desk().pages[state.sel.p] : null);
const keyObj = () => (page() && state.sel.k !== null ? page().keys[state.sel.k] : null);

function glyph(name) {
  const code = state.icons[name] || state.icons.warning || 'f071';
  return String.fromCodePoint(parseInt(code, 16));
}

function normalize(deck) {
  deck.desktops = deck.desktops || [];
  for (const d of deck.desktops) {
    if (M.desktopKind(d) === 'keys') {
      d.pages = d.pages && d.pages.length ? d.pages : [{ id: 'root', title: d.name, cols: 4, rows: 3, keys: [] }];
      for (const p of d.pages) { p.cols = p.cols || 4; p.rows = p.rows || 3; M.padKeys(p); }
    }
  }
  return deck;
}

function trimmedDeck() {
  const out = M.clone(state.deck);
  for (const d of out.desktops) {
    for (const p of d.pages || []) while (p.keys.length && M.isEmptyKey(p.keys[p.keys.length - 1])) p.keys.pop();
  }
  if (out.settings && !Object.keys(out.settings).length) delete out.settings;
  return out;
}

function clampSel() {
  const n = state.deck.desktops.length;
  state.sel.d = Math.min(Math.max(state.sel.d, 0), n - 1);
  const d = desk();
  if (M.desktopKind(d) === 'keys') {
    state.sel.p = Math.min(Math.max(state.sel.p, 0), d.pages.length - 1);
    const pg = page();
    M.padKeys(pg);
    if (state.sel.k !== null && state.sel.k >= pg.keys.length) state.sel.k = null;
  } else {
    state.sel.p = 0;
    state.sel.k = null;
  }
}

// Applies a change to the deck. History is pushed first, except that rapid edits of the same field
// ("coalesce") share one undo step. `rerender: 'live'` keeps the inspector (and so the focused input) untouched.
function commit(fn, { coalesce = null, rerender = 'all' } = {}) {
  const now = Date.now();
  if (!(coalesce && lastCoalesce.key === coalesce && now - lastCoalesce.t < 1500)) history.push(deckText());
  lastCoalesce = { key: coalesce, t: now };
  fn(state.deck);
  clampSel();
  if (rerender === 'all') renderAll();
  else renderLive();
}

function restore(text) {
  state.deck = JSON.parse(text);
  clampSel();
  renderAll();
}
const undo = () => { const t = history.undo(deckText()); if (t) restore(t); };
const redo = () => { const t = history.redo(deckText()); if (t) restore(t); };

// ---- api ---------------------------------------------------------------------------------------------

async function api(method, path, body) {
  const r = await fetch(path, {
    method,
    headers: { 'Content-Type': 'application/json', 'X-Streamdeck-Token': TOKEN },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || r.statusText);
  return data;
}

function toast(text) {
  const el = $('#toast');
  el.textContent = text;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, 3500);
}

function banner(text, kind = 'error') {
  const el = $('#banner');
  if (!text) { el.hidden = true; return; }
  el.className = kind === 'warn' ? 'warn' : '';
  el.replaceChildren(h('span', {}, text), h('button', { class: 'small', onclick: () => { el.hidden = true; } }, 'Close'));
  el.hidden = false;
}

async function save() {
  if (state.saving || !isDirty()) return;
  state.saving = true;
  renderTopbar();
  try {
    const res = await api('PUT', '/api/deck', trimmedDeck());
    state.savedText = deckText();
    state.sizeNote = `${(res.layoutBytes / 1024).toFixed(1)} KB`;
    if (res.warnings && res.warnings.length) banner('Saved, with warnings:\n' + res.warnings.join('\n'), 'warn');
    else banner(null);
    toast('Saved. The board updates in a moment.');
  } catch (e) {
    banner('Not saved: ' + e.message);
  } finally {
    state.saving = false;
    renderTopbar();
  }
}

async function revert() {
  if (isDirty() && !confirm('Discard all unsaved changes?')) return;
  await loadDeck();
  renderAll();
}

async function loadDeck() {
  state.deck = normalize(await api('GET', '/api/deck'));
  state.savedText = deckText();
  history.past = [];
  history.future = [];
  clampSel();
}

async function refreshStatus() {
  try {
    state.status = await api('GET', '/api/status');
  } catch (e) {
    state.status = null;
  }
  renderTopbar();
}

// ---- top bar -----------------------------------------------------------------------------------------

function renderTopbar() {
  const st = state.status;
  let pill = h('span', { class: 'pill off' }, 'Companion not reachable');
  if (st) {
    pill = st.connected
      ? h('span', { class: 'pill on' }, `Board connected${st.firmware ? ' \u00B7 fw ' + st.firmware : ''} \u00B7 showing desktop ${st.desktop + 1}`)
      : h('span', { class: 'pill off' }, 'Board not connected (you can still edit)');
  }
  const size = state.deck ? `${(M.layoutBytes(state.deck) / 1024).toFixed(1)} KB layout` : '';
  $('#topbar').replaceChildren(
    h('span', { class: 'brand' }, 'Deck editor'), pill, h('span', { class: 'pill' }, size), h('span', { class: 'spacer' }),
    h('button', { onclick: undo, disabled: !history.canUndo, title: 'Undo (Ctrl+Z)' }, 'Undo'),
    h('button', { onclick: redo, disabled: !history.canRedo, title: 'Redo (Ctrl+Y)' }, 'Redo'),
    h('button', { onclick: revert, disabled: !isDirty() }, 'Revert'),
    h('button', { class: 'primary', onclick: save, disabled: !isDirty() || state.saving, title: 'Save (Ctrl+S)' },
      isDirty() ? 'Save' : 'Saved'),
  );
}

// ---- tree --------------------------------------------------------------------------------------------

function addMenu() {
  const hasNp = M.hasKind(state.deck, 'nowplaying'), hasStats = M.hasKind(state.deck, 'stats');
  const item = (icon, name, desc, onclick, why = '') => h('button', { class: 'item', disabled: !!why, title: why || desc, onclick },
    h('span', { class: 'fa ic' }, glyph(icon)),
    h('span', { class: 'tx' }, h('span', { class: 'nm' }, name), h('span', { class: 'ds' }, why || desc)));
  return h('div', { class: 'addmenu' },
    h('div', { class: 'sec' }, 'Live screens'),
    item('audio', 'Now playing', 'Cover, title and player controls from the Windows media session', () => addDesktopOfKind('nowplaying'), hasNp ? 'Already added' : ''),
    item('charge', 'PC stats', 'CPU, GPU, RAM, disk and network', () => addDesktopOfKind('stats'), hasStats ? 'Already added' : ''),
    h('div', { class: 'sec' }, 'Ready-made desktops'),
    state.templates.map((t) => item(t.icon, t.name, t.description, () => addTemplate(t))),
    h('div', { class: 'sec' }, 'Blank'),
    item('keyboard', 'Keys desktop', 'An empty page of keys', () => addDesktopOfKind('keys')));
}

// Desktops can be dragged by their row to reorder; the whole card is the drop target (upper half = before it).
function dragDesktopHandlers(box, row, i) {
  const MIME = 'application/x-sd-desktop';
  const clear = (el) => el.classList.remove('drop-before', 'drop-after');
  row.draggable = true;
  row.addEventListener('dragstart', (e) => {
    e.dataTransfer.setData(MIME, String(i));
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setDragImage(box, 24, 16);
    box.classList.add('dragging');
  });
  row.addEventListener('dragend', () => { box.classList.remove('dragging'); document.querySelectorAll('.desk').forEach(clear); });
  box.addEventListener('dragover', (e) => {
    if (![...e.dataTransfer.types].includes(MIME)) return;
    e.preventDefault();
    const r = box.getBoundingClientRect();
    const after = e.clientY > r.top + r.height / 2;
    box.classList.toggle('drop-after', after);
    box.classList.toggle('drop-before', !after);
  });
  box.addEventListener('dragleave', (e) => { if (!box.contains(e.relatedTarget)) clear(box); });
  box.addEventListener('drop', (e) => {
    const from = e.dataTransfer.getData(MIME);
    if (from === '') return;
    e.preventDefault();
    const after = box.classList.contains('drop-after');
    clear(box);
    const to = M.dropTarget(+from, i, after);
    if (to !== +from) commit((d) => { M.moveInList(d.desktops, +from, to); state.sel = { d: to, p: 0, k: null }; });
  });
}

function renderTree() {
  const list = h('div');
  const full = state.deck.desktops.length >= M.LIMITS.desktops;
  list.append(h('div', { class: 'treehead' }, h('h3', {}, 'Desktops'),
    h('button', { class: 'small' + (state.addMenu ? ' primary' : ''), disabled: full && !state.addMenu, title: full ? 'The board holds at most 8 desktops' : '',
      onclick: () => { state.addMenu = !state.addMenu; renderTree(); } }, state.addMenu ? 'Close' : '+ Add desktop')));
  if (state.addMenu) list.append(addMenu());
  state.deck.desktops.forEach((d, i) => {
    const kind = M.desktopKind(d);
    const active = i === state.sel.d;
    const row = h('div', { class: 'deskrow', title: 'Drag to reorder', onclick: () => { state.sel = { d: i, p: 0, k: null }; renderAll(); } },
      h('span', { class: 'grip' }, '\u22EE\u22EE'),
      h('span', { class: 'fa' }, glyph(KIND_ICON[kind])),
      h('span', { class: 'name' }, d.name || '(unnamed)'),
      h('button', { class: 'small', title: 'Move up', disabled: i === 0, onclick: (e) => { e.stopPropagation(); moveDesktop(i, i - 1); } }, '\u2191'),
      h('button', { class: 'small', title: 'Move down', disabled: i === state.deck.desktops.length - 1, onclick: (e) => { e.stopPropagation(); moveDesktop(i, i + 1); } }, '\u2193'),
      h('button', { class: 'small danger', title: 'Delete desktop', disabled: state.deck.desktops.length <= 1, onclick: (e) => { e.stopPropagation(); deleteDesktop(i); } }, '\u2715'),
    );
    const box = h('div', { class: 'desk' + (active ? ' active' : '') }, row);
    if (kind === 'keys') {
      const pages = h('div', { class: 'pages' });
      d.pages.forEach((p, pi) => {
        pages.append(h('div', { class: 'pagerow' + (active && pi === state.sel.p ? ' active' : ''), onclick: () => { state.sel = { d: i, p: pi, k: null }; renderAll(); } },
          h('span', { class: 't' }, p.title || p.id), h('span', { class: 'id' }, p.id),
          pi > 0 ? h('button', { class: 'small danger', title: 'Delete page', onclick: (e) => { e.stopPropagation(); deletePage(i, pi); } }, '\u2715') : null));
      });
      pages.append(h('button', { class: 'small', disabled: d.pages.length >= M.LIMITS.pages, onclick: () => addPageTo(i) }, '+ Page'));
      box.append(pages);
    }
    dragDesktopHandlers(box, row, i);
    list.append(box);
  });
  $('#treeList').replaceChildren(list);
}

function renderTreeSettings() {
  const s = state.deck.settings || {};
  const setting = (name, value) => commit((d) => { d.settings = d.settings || {}; d.settings[name] = value; }, { coalesce: 'setting-' + name, rerender: 'live' });
  const vol = h('span', {}, String(s.volume ?? 70));
  const bri = h('span', {}, String(s.brightness ?? 255));
  $('#treeSettings').replaceChildren(
    h('h3', {}, 'Board settings'),
    h('div', { class: 'card' },
      h('div', { class: 'field' }, h('label', {}, 'Sound volume ', vol),
        h('input', { type: 'range', min: 0, max: 100, value: s.volume ?? 70, oninput: (e) => { vol.textContent = e.target.value; setting('volume', +e.target.value); } })),
      h('div', { class: 'field' }, h('label', {}, h('input', { type: 'checkbox', checked: s.sounds ?? true, onchange: (e) => setting('sounds', e.target.checked) }), ' Key sounds')),
      h('div', { class: 'field' }, h('label', {}, 'Brightness ', bri),
        h('input', { type: 'range', min: 5, max: 255, value: s.brightness ?? 255, oninput: (e) => { bri.textContent = e.target.value; setting('brightness', +e.target.value); } })),
      h('div', { class: 'hint' }, 'Sent to the board when you save.')));
}

function moveDesktop(from, to) {
  commit((d) => { M.moveInList(d.desktops, from, to); state.sel = { d: to, p: 0, k: null }; });
}
function deleteDesktop(i) {
  if (!confirm(`Delete the desktop "${state.deck.desktops[i].name}" and everything on it?`)) return;
  commit((d) => { M.removeDesktop(d, i); });
}
function addDesktopOfKind(kind) {
  commit((d) => { state.addMenu = false; const i = M.addDesktop(d, kind); if (i !== null) state.sel = { d: i, p: 0, k: null }; });
}
function addTemplate(t) {
  commit((d) => { state.addMenu = false; const i = M.addTemplateDesktop(d, t.desktop); if (i !== null) state.sel = { d: i, p: 0, k: null }; });
}
function addPageTo(di) {
  commit((d) => { const pi = M.addPage(d.desktops[di]); if (pi !== null) state.sel = { d: di, p: pi, k: null }; });
}
function deletePage(di, pi) {
  const d = state.deck.desktops[di];
  const refs = M.foldersTo(d, d.pages[pi].id);
  const msg = `Delete the page "${d.pages[pi].title}"?` + (refs ? ` ${refs} folder key(s) pointing to it will become empty.` : '');
  if (!confirm(msg)) return;
  commit((deck) => { M.removePage(deck.desktops[di], pi); if (state.sel.d === di) state.sel.p = 0; state.sel.k = null; });
}

// ---- the board screen preview ------------------------------------------------------------------------

function keyEl(k, i, geo) {
  const kind = M.keyKind(k);
  const { x, y } = geo.pos(i);
  const unknownIcon = k.icon && !state.icons[k.icon];
  const el = h('div', {
    class: 'key ' + kind + (state.sel.k === i ? ' sel' : ''),
    draggable: kind !== 'empty',
    dataset: { i },
    title: kind === 'empty' ? 'Empty slot' : kind === 'folder' ? `Folder \u2192 ${k.folder}` : M.describeAction(k.action),
    style: { left: x * S + 'px', top: y * S + 'px', width: geo.keyW * S + 'px', height: geo.keyH * S + 'px',
      background: kind === 'empty' ? '' : (k.color || M.DEFAULT_KEY_COLOR) },
  });
  if (kind !== 'empty') {
    if (k.icon) el.append(h('span', { class: 'ico fa', style: { fontSize: geo.iconPx * S + 'px' } }, glyph(unknownIcon ? 'warning' : k.icon)));
    if (k.label) el.append(h('span', { class: 'lbl', style: { fontSize: geo.textPx * S + 'px' } }, k.label));
    if (kind === 'folder') el.append(h('span', { class: 'arrow fa' }, glyph('right')));
  }
  el.addEventListener('click', () => { state.sel.k = i; renderScreen(); renderInspector(); });
  el.addEventListener('dragstart', (e) => { e.dataTransfer.setData('application/x-sd-key', String(i)); e.dataTransfer.effectAllowed = 'move'; });
  el.addEventListener('dragover', (e) => {
    if ([...e.dataTransfer.types].some((t) => t.startsWith('application/x-sd-'))) { e.preventDefault(); el.classList.add('dragover'); }
  });
  el.addEventListener('dragleave', () => el.classList.remove('dragover'));
  el.addEventListener('drop', (e) => {
    e.preventDefault();
    el.classList.remove('dragover');
    const fromKey = e.dataTransfer.getData('application/x-sd-key');
    const fromPreset = e.dataTransfer.getData('application/x-sd-preset');
    if (fromKey !== '' && +fromKey !== i) { commit(() => { M.swapKeys(page(), +fromKey, i); state.sel.k = i; }); }
    else if (fromPreset !== '') { const p = flatPresets()[+fromPreset]; if (p) putPreset(i, p.key); }
  });
  return el;
}

// Static previews of the two live screens. They follow the same geometry as the firmware (nowplaying.cpp, stats.cpp)
// and the options chosen below, with sample numbers instead of live data.
const px = (n) => n * S + 'px';
const abs = (style) => ({ position: 'absolute', ...style });
const fa = (name, size, style = {}) => h('span', { class: 'fa', style: { fontSize: px(size), ...style } }, glyph(name));

function mockNowPlaying(o) {
  const shift = o.album ? 0 : -18; // without the album line, the title block moves up
  const icon = (name, size, cx) => h('span', { class: 'fa', style: abs({ left: px(cx - 32), width: px(64), top: px(162), height: px(48), display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: px(size) }) }, glyph(name));
  const controls = [icon('prev', 28, 96), icon('play', 28, 160), icon('next', 28, 224)];
  if (o.controls === 'full') controls.unshift(icon('shuffle', 24, 32)), controls.push(icon('loop', 24, 288));
  return h('div', { class: 'mock', dataset: { tint: o.tint, album: o.album, controls: o.controls }, style: { background: o.tint ? '#2a2140' : '#1b2535' } },
    h('div', { style: abs({ left: px(9), top: px(9), width: px(140), height: px(140), background: o.tint ? '#3d3158' : '#2c3a52', display: 'flex', alignItems: 'center', justifyContent: 'center' }) }, fa('audio', 40, { opacity: .4 })),
    o.album ? h('div', { class: 'm-album', style: abs({ left: px(161), top: px(8), fontSize: px(14), opacity: .6 }) }, 'Album') : null,
    h('div', { style: abs({ left: px(161), top: px(28 + shift), right: px(9), fontSize: px(28), lineHeight: 1.15 }) }, 'Song title'),
    h('div', { style: abs({ left: px(161), top: px(100 + shift), fontSize: px(16), opacity: .9 }) }, 'Artist'),
    h('div', { style: abs({ left: px(161), top: px(134 + shift), fontSize: px(14), opacity: .6 }) }, '1:23 / 3:45'),
    h('div', { style: abs({ left: 0, right: 0, top: px(158), height: px(4), background: 'rgba(255,255,255,.3)' }) }, h('div', { style: { width: '38%', height: '100%', background: '#fff' } })),
    h('div', { style: abs({ left: 0, right: 0, top: px(162), height: px(48), background: o.tint ? '#0d0a14' : '#0b0f17' }) }),
    controls);
}

const SAMPLE = {
  cpu: { name: 'CPU', pct: 34, sub: 'temp n/a', subColor: '#7f8fa6' },
  gpu: { name: 'GPU', pct: 12, sub: '45\u00B0C  4W', subColor: '#1ed760' },
  ram: { name: 'RAM', pct: 58, sub: '9.1/15.6 GB', subColor: '#7f8fa6' },
  vram: { name: 'VRAM', pct: 27, sub: '1.6/6.0 GB', subColor: '#7f8fa6' },
  disk: { name: 'DISK', pct: 48, sub: '195/405 GB', subColor: '#7f8fa6' },
  net: { name: 'NET', net: true, down: 'download', downText: '1.2 MB/s', up: 'upload', upText: '0.3 MB/s' },
};
const loadColor = (pct) => (pct < 60 ? '#1ed760' : pct < 85 ? '#f5a524' : '#f04438');
const txt = (style, text, extra = {}) => h('div', { style: abs({ whiteSpace: 'nowrap', ...style }), ...extra }, text);
const netText = (it) => [fa(it.down, 14), ` ${it.downText}`];
const netUpText = (it) => [fa(it.up, 14), ` ${it.upText}`];

function barMock(x, y, w, hgt, pct) {
  return h('div', { style: abs({ left: px(x), top: px(y), width: px(w), height: px(hgt), borderRadius: px(hgt / 2), background: '#2a3a4d' }) },
    h('div', { style: { width: pct + '%', height: '100%', borderRadius: px(hgt / 2), background: loadColor(pct) } }));
}

function gaugesMock(items) {
  const pct = items.filter((i) => i !== 'net');
  const ng = Math.min(3, pct.length);
  const centers = [[160], [100, 220], [54, 160, 266]];
  const out = [];
  for (let i = 0; i < ng; i++) {
    const it = SAMPLE[pct[i]], cx = centers[ng - 1][i], sweep = it.pct * 2.7;
    out.push(h('div', { class: 'mk', dataset: { item: pct[i] } },
      h('div', { style: abs({ left: px(cx - 46), top: px(8), width: px(92), height: px(92), borderRadius: '50%', background: `conic-gradient(from 225deg, ${loadColor(it.pct)} 0deg ${sweep}deg, #2a3a4d ${sweep}deg 270deg, transparent 270deg)` }) },
        h('div', { style: abs({ inset: px(9), borderRadius: '50%', background: '#101722' }) })),
      txt({ left: px(cx - 36), width: px(72), top: px(36), textAlign: 'center', fontSize: px(24), lineHeight: 1.1 }, it.pct + '%'),
      txt({ left: px(cx - 36), width: px(72), top: px(66), textAlign: 'center', fontSize: px(14), opacity: .6 }, it.name),
      txt({ left: px(cx - 52), width: px(104), top: px(104), textAlign: 'center', fontSize: px(14), color: it.subColor }, it.sub)));
  }
  let rowY = ng > 0 ? 136 : 20;
  const rowH = ng > 0 ? 24 : 44;
  for (let i = ng; i < pct.length; i++, rowY += rowH) {
    const it = SAMPLE[pct[i]];
    out.push(h('div', { class: 'mk', dataset: { item: pct[i] } },
      txt({ left: px(10), top: px(rowY), fontSize: px(14), opacity: .6 }, it.name), barMock(58, rowY + 5, 186, 8, it.pct),
      txt({ left: px(250), width: px(62), top: px(rowY), textAlign: 'right', fontSize: px(14), opacity: .8 }, it.sub)));
  }
  if (items.includes('net')) {
    const it = SAMPLE.net;
    if (!pct.length) {
      out.push(h('div', { class: 'mk', dataset: { item: 'net' } },
        txt({ left: px(10), width: px(300), top: px(60), textAlign: 'center', fontSize: px(24) }, netText(it)),
        txt({ left: px(10), width: px(300), top: px(110), textAlign: 'center', fontSize: px(24) }, netUpText(it))));
    } else {
      out.push(h('div', { class: 'mk', dataset: { item: 'net' } },
        txt({ left: px(10), top: px(rowY), fontSize: px(14), opacity: .8 }, netText(it)),
        txt({ left: px(165), top: px(rowY), fontSize: px(14), opacity: .8 }, netUpText(it))));
    }
  }
  return out;
}

function barsMock(items) {
  const n = items.length;
  const rowH = n <= 3 ? 58 : n === 4 ? 48 : n === 5 ? 40 : 34;
  const top = (210 - rowH * n) / 2;
  const nameFont = rowH >= 48 ? 16 : 14, valueFont = rowH >= 40 ? 20 : 16;
  return items.map((id, i) => {
    const it = SAMPLE[id], cy = top + i * rowH + rowH / 2;
    const name = txt({ left: px(10), top: px(cy - nameFont * 0.6), fontSize: px(nameFont), opacity: .6 }, it.name);
    if (it.net) {
      return h('div', { class: 'mk', dataset: { item: id } }, name,
        txt({ left: px(64), top: px(cy - 9), fontSize: px(16) }, netText(it)), txt({ left: px(190), top: px(cy - 9), fontSize: px(16) }, netUpText(it)));
    }
    return h('div', { class: 'mk', dataset: { item: id } }, name, barMock(64, cy - 5, 100, 10, it.pct),
      txt({ left: px(170), top: px(cy - 8), fontSize: px(14), opacity: .8 }, it.sub),
      txt({ left: px(254), width: px(56), top: px(cy - valueFont * 0.65), textAlign: 'right', fontSize: px(valueFont) }, it.pct + '%'));
  });
}

function tilesMock(items) {
  const n = items.length, rows = Math.ceil(n / 2), pad = 6, gap = 6;
  const tileW = (320 - 2 * pad - gap) / 2 | 0, tileH = (210 - 2 * pad - (rows - 1) * gap) / rows | 0;
  const valueFont = tileH >= 80 ? 28 : 24;
  return items.map((id, i) => {
    const it = SAMPLE[id];
    const full = i === n - 1 && n % 2 === 1;
    const x = full ? pad : pad + (i % 2) * (tileW + gap), w = full ? 320 - 2 * pad : tileW, y = pad + Math.floor(i / 2) * (tileH + gap);
    const parts = [h('div', { style: abs({ left: px(x), top: px(y), width: px(w), height: px(tileH), borderRadius: px(10), background: '#17212f' }) }),
      txt({ left: px(x + 8), top: px(y + 4), fontSize: px(14), opacity: .6 }, it.name)];
    if (it.net) {
      parts.push(txt({ left: px(x + 8), top: px(y + 22), fontSize: px(16) }, netText(it)), txt({ left: px(x + 8), top: px(y + 41), fontSize: px(16) }, netUpText(it)));
    } else {
      parts.push(txt({ left: px(x + 60), width: px(w - 68), top: px(y + 4), textAlign: 'right', fontSize: px(14), color: it.subColor }, it.sub),
        txt({ left: px(x + 8), top: px(y + (tileH >= 80 ? 26 : 20)), fontSize: px(valueFont), lineHeight: 1.1 }, it.pct + '%'),
        barMock(x + 8, y + tileH - 11, w - 16, 5, it.pct));
    }
    return h('div', { class: 'mk', dataset: { item: id } }, parts);
  });
}

function mockStats(o) {
  const body = o.style === 'bars' ? barsMock(o.items) : o.style === 'tiles' ? tilesMock(o.items) : gaugesMock(o.items);
  return h('div', { class: 'mock', dataset: { style: o.style }, style: { background: '#101722' } }, body);
}

function renderScreen() {
  const d = desk();
  const kind = M.desktopKind(d);
  const n = state.deck.desktops.length;
  const pg = page();
  const title = kind === 'keys' ? (pg.title || pg.id) : d.name;
  const hdrH = M.SCREEN.header * S;
  const screen = h('div', { class: 'screen', style: { width: M.SCREEN.w * S + 'px', height: M.SCREEN.h * S + 'px' } });
  const dots = h('div', { class: 'dots' });
  if (n > 1) for (let i = 0; i < n; i++) dots.append(h('span', { class: 'dot' + (i === state.sel.d ? ' on' : '') }));
  screen.append(h('div', { class: 'hdr', style: { height: hdrH + 'px' } },
    kind === 'keys' && state.sel.p > 0 ? h('span', { class: 'back' }, h('span', { class: 'fa' }, glyph('left')), ' Back') : null,
    h('span', { class: 'title' }, title), dots));
  const content = h('div', { class: 'content', style: { top: hdrH + 'px', height: (M.SCREEN.h - M.SCREEN.header) * S + 'px' } });
  if (kind === 'keys') {
    const geo = M.geometry(pg.cols, pg.rows);
    for (let i = 0; i < pg.cols * pg.rows; i++) content.append(keyEl(pg.keys[i] || {}, i, geo));
  } else {
    content.append(kind === 'nowplaying' ? mockNowPlaying(M.liveOptions(d)) : mockStats(M.liveOptions(d)));
  }
  screen.append(content);
  const head = h('div', { class: 'stagehead' }, h('b', {}, d.name),
    kind === 'keys' ? `\u203A ${pg.title || pg.id}` : KIND_LABEL[kind],
    h('span', { class: 'hint' }, kind === 'keys' ? 'Click a key to edit it \u00B7 drag keys to swap \u00B7 drag presets onto a slot' : 'Preview only'));
  $('#screenMount').replaceChildren(head, screen);
}

function renderPageControls() {
  const box = $('#pageControls');
  if (!isKeys()) { box.replaceChildren(); return; }
  const pg = page();
  const resize = (c, r) => {
    c = Math.min(Math.max(c, 1), M.LIMITS.cols);
    r = Math.min(Math.max(r, 1), M.LIMITS.rows);
    if (c === pg.cols && r === pg.rows) return;
    const lost = M.resizeLoss(pg, c, r);
    if (lost && !confirm(`${lost} key(s) no longer fit and will be removed. Continue?`)) return;
    commit(() => { M.resizePage(page(), c, r); });
  };
  const stepper = (label, value, set) => h('span', { class: 'stepper' }, label,
    h('button', { class: 'small', onclick: () => set(value - 1) }, '\u2212'), h('b', {}, value), h('button', { class: 'small', onclick: () => set(value + 1) }, '+'));
  box.replaceChildren(
    h('span', { class: 'stepper' }, 'Page title ',
      h('input', { type: 'text', value: pg.title || '', maxLength: 24, style: { width: '170px' }, oninput: (e) => commit(() => { page().title = e.target.value; }, { coalesce: 'ptitle', rerender: 'live' }) })),
    stepper('Columns', pg.cols, (v) => resize(v, pg.rows)),
    stepper('Rows', pg.rows, (v) => resize(pg.cols, v)),
    h('span', { class: 'hint' }, `page id: ${pg.id}`));
}

// ---- inspector ---------------------------------------------------------------------------------------

function field(label, control, hint) {
  return h('div', { class: 'field' }, h('label', {}, label), control, hint ? h('div', { class: 'hint' }, hint) : null);
}

function textInput(value, onInput, attrs = {}) {
  const el = h('input', { type: 'text', value: value ?? '', ...attrs });
  el.addEventListener('input', () => onInput(el.value, el));
  return el;
}

function liveEdit(fn, id) { commit(fn, { coalesce: id, rerender: 'live' }); }

function startCapture(button, p, onChange) {
  button.textContent = 'Press the key\u2026';
  const done = () => { document.removeEventListener('keydown', handler, true); state.capturing = false; button.textContent = 'Press a key\u2026'; };
  const handler = (e) => {
    if (['Control', 'Shift', 'Alt', 'Meta'].includes(e.key)) return; // wait for the main key
    e.preventDefault();
    e.stopPropagation();
    done();
    if (e.code === 'Escape' && !e.ctrlKey && !e.shiftKey && !e.altKey) return; // Esc alone cancels
    const name = M.keyNameFromCode(e.code);
    if (!name) { toast('That key is not supported'); return; }
    p.ctrl = e.ctrlKey; p.shift = e.shiftKey; p.alt = e.altKey; p.key = name; // the Windows key cannot be captured: tick it
    onChange(M.composeHotkey(p), true);
  };
  state.capturing = true;
  document.addEventListener('keydown', handler, true);
}

function hotkeyEditor(action) {
  const id = 'hk' + nextEditorId++;
  const p = M.parseHotkey(action.keys);
  const result = h('code', {}, action.keys || '(nothing)');
  const change = (keys, structural) => {
    result.textContent = keys || '(nothing)';
    commit(() => { action.keys = keys; }, { coalesce: id, rerender: structural ? 'all' : 'live' });
  };
  const keyIn = textInput(p.key, (v, el) => { p.key = v; el.classList.toggle('bad', !!v && !M.isValidKey(v)); change(M.composeHotkey(p), false); },
    { list: 'keynames', placeholder: 'e.g. s, f5, enter, volume_up', class: p.key && !M.isValidKey(p.key) ? 'bad' : '' });
  const cap = h('button', { class: 'small' }, 'Press a key\u2026');
  cap.addEventListener('click', () => { if (!state.capturing) startCapture(cap, p, change); });
  return h('div', {},
    h('div', { class: 'row mods' }, ['ctrl', 'shift', 'alt', 'win'].map((m) =>
      h('label', {}, h('input', { type: 'checkbox', checked: p[m], onchange: (e) => { p[m] = e.target.checked; change(M.composeHotkey(p), true); } }), m[0].toUpperCase() + m.slice(1)))),
    h('div', { class: 'field sub' }, h('label', {}, 'Key'), h('div', { class: 'row tight' }, keyIn, cap)),
    h('div', { class: 'hint' }, 'Result: ', result, ' \u00B7 the Windows key cannot be captured, tick Win instead. Esc cancels capturing.'));
}

// `replace(newAction)` swaps the whole action (type change); field edits mutate `action` in place.
function actionEditor(action, replace, inMulti = false) {
  const id = 'act' + nextEditorId++;
  const types = inMulti ? ['hotkey', 'text', 'open', 'run', 'lock', 'wait'] : ['hotkey', 'text', 'open', 'run', 'lock', 'multi'];
  const names = { hotkey: 'Press keys', text: 'Type text', open: 'Open file / URL / app', run: 'Run a program', lock: 'Lock the PC', multi: 'Several steps', wait: 'Wait' };
  const cur = !action ? 'none' : action.wait !== undefined ? 'wait' : action.type;
  const select = h('select', { onchange: (e) => {
    const t = e.target.value;
    replace(t === 'none' ? null : t === 'wait' ? { wait: 0.5 } : M.defaultAction(t));
  } },
  inMulti ? null : h('option', { value: 'none', selected: cur === 'none' }, 'No action'),
  types.map((t) => h('option', { value: t, selected: cur === t }, names[t])));
  const edit = (fn) => liveEdit(fn, id);
  let body = null;
  if (cur === 'hotkey') body = hotkeyEditor(action);
  else if (cur === 'text') {
    const ta = h('textarea', { value: action.text || '', oninput: () => edit(() => { action.text = ta.value; }) });
    body = field('Text to type', ta, 'A new line presses Enter.');
  } else if (cur === 'open') {
    body = field('Target', textInput(action.target, (v) => edit(() => { action.target = v; }), { placeholder: 'https://\u2026, C:\\path\\file, spotify:' }));
  } else if (cur === 'run') {
    const shell = !!action.shell;
    const input = textInput(Array.isArray(action.command) ? M.joinCommand(action.command) : action.command || '',
      (v) => edit(() => { action.command = action.shell ? v : M.splitCommand(v); }), { placeholder: 'program and arguments' });
    body = h('div', {}, field('Command', input),
      h('label', { class: 'row tight' }, h('input', { type: 'checkbox', checked: shell, onchange: (e) => commit(() => {
        if (e.target.checked) { action.command = Array.isArray(action.command) ? M.joinCommand(action.command) : action.command; action.shell = true; }
        else { action.command = M.splitCommand(action.command || ''); delete action.shell; }
      }) }), 'Run through the shell (needed for commands like "code" or "calc")'));
  } else if (cur === 'wait') {
    body = field('Seconds', h('input', { type: 'number', min: 0, max: 30, step: 0.1, value: action.wait, oninput: (e) => edit(() => { action.wait = Math.min(30, Math.max(0, +e.target.value || 0)); }) }));
  } else if (cur === 'lock') body = h('div', { class: 'hint' }, 'Locks the Windows session, like Win+L.');
  else if (cur === 'multi') body = multiEditor(action);
  return h('div', {}, field('What it does', select), body);
}

function multiEditor(action) {
  const box = h('div');
  action.steps.forEach((step, i) => {
    box.append(h('div', { class: 'step' },
      h('div', { class: 'row tight' }, h('span', { class: 'lab' }, `Step ${i + 1}`), h('span', { class: 'spacer' }),
        h('button', { class: 'small', disabled: i === 0, onclick: () => commit(() => M.moveInList(action.steps, i, i - 1)) }, '\u2191'),
        h('button', { class: 'small', disabled: i === action.steps.length - 1, onclick: () => commit(() => M.moveInList(action.steps, i, i + 1)) }, '\u2193'),
        h('button', { class: 'small danger', disabled: action.steps.length <= 1, onclick: () => commit(() => action.steps.splice(i, 1)) }, '\u2715')),
      actionEditor(step, (na) => commit(() => { action.steps[i] = na || { type: 'lock' }; }), true)));
  });
  box.append(h('button', { class: 'small', onclick: () => commit(() => action.steps.push({ type: 'hotkey', keys: 'ctrl+c' })) }, '+ Add step'));
  return h('div', {}, h('div', { class: 'hint' }, 'Steps run one after the other, uninterrupted.'), box);
}

function folderEditor(k) {
  const d = desk();
  const others = d.pages.map((p, i) => ({ p, i })).filter(({ i }) => i !== state.sel.p);
  const select = h('select', { onchange: (e) => {
    const v = e.target.value;
    if (v === '__new__') {
      if (d.pages.length >= M.LIMITS.pages) { toast('This desktop already has the maximum number of pages'); renderInspector(); return; }
      commit(() => { const i = M.addPage(desk(), k.label || 'Folder'); k.folder = desk().pages[i].id; });
    } else commit(() => { k.folder = v; });
  } },
  others.map(({ p }) => h('option', { value: p.id, selected: p.id === k.folder }, `${p.title || p.id} (${p.id})`)),
  h('option', { value: '__new__' }, '+ New page\u2026'));
  const target = d.pages.findIndex((p) => p.id === k.folder);
  return h('div', {}, field('Opens page', select),
    target >= 0 ? h('button', { class: 'small', onclick: () => { state.sel = { d: state.sel.d, p: target, k: null }; renderAll(); } }, 'Edit that page') : h('div', { class: 'hint' }, 'The page this folder pointed to is gone.'));
}

function iconPicker(k) {
  const names = Object.keys(state.icons).sort();
  const grid = h('div', { class: 'iconGrid' }, h('button', { class: k.icon ? '' : 'on', title: 'No icon', onclick: () => commit(() => { delete page().keys[state.sel.k].icon; }) }, '\u2205'),
    names.map((n) => h('button', { class: 'fa' + (k.icon === n ? ' on' : ''), title: n, onclick: () => commit(() => { page().keys[state.sel.k].icon = n; }) }, glyph(n))));
  return field('Icon' + (k.icon ? ` \u00B7 ${k.icon}` : ''), grid);
}

function colorPicker(k) {
  const cur = (k.color || M.DEFAULT_KEY_COLOR).toUpperCase();
  const set = (c) => commit(() => { const key = page().keys[state.sel.k]; if (c === M.DEFAULT_KEY_COLOR) delete key.color; else key.color = c; });
  const custom = h('input', { type: 'color', value: cur, title: 'Custom color', style: { width: '34px', height: '28px', padding: 0, border: 0, background: 'none' },
    oninput: (e) => liveEdit(() => { page().keys[state.sel.k].color = e.target.value.toUpperCase(); }, 'color') });
  return field('Color', h('div', { class: 'swatches' }, SWATCHES.map((c) => h('button', { class: 'sw' + (cur === c ? ' on' : ''), style: { background: c }, title: c, onclick: () => set(c) })), custom));
}

function setKeyKind(kind) {
  const i = state.sel.k;
  if (kind === 'folder' && desk().pages.length < 2 && desk().pages.length >= M.LIMITS.pages) { toast('No page available for a folder'); return; }
  commit(() => {
    const key = page().keys[i] || {};
    if (kind === 'empty') { page().keys[i] = {}; return; }
    if (kind === 'action') {
      delete key.folder;
      if (!key.action) key.action = M.defaultAction('hotkey');
      if (!key.label) key.label = 'Key';
    } else {
      delete key.action;
      key.label = key.label || 'Folder';
      key.icon = key.icon || 'directory';
      key.color = key.color || '#1F6F5C';
      const other = desk().pages.find((_, pi) => pi !== state.sel.p);
      key.folder = other ? other.id : desk().pages[M.addPage(desk(), key.label)].id;
    }
    page().keys[i] = key;
  });
}

function renderInspector() {
  const box = $('#inspector');
  const d = desk();
  const kind = M.desktopKind(d);
  const cards = [];
  cards.push(h('div', { class: 'card' }, h('h4', {}, 'Desktop'),
    field('Name', textInput(d.name, (v) => liveEdit(() => { desk().name = v; }, 'dname'), { maxLength: 24 })),
    h('div', { class: 'hint' }, kind === 'keys' ? 'A desktop holds pages of keys; swipe between desktops on the board.' :
      kind === 'nowplaying' ? 'The Now playing screen shows the Windows media session: cover, title, progress bar and controls. Choose the players and the look below the preview.' :
        'The PC stats screen shows CPU, GPU, RAM, VRAM, disk and network. Choose the style, the items and their order below the preview.')));
  if (kind !== 'keys') { box.replaceChildren(...cards); return; }
  const k = keyObj();
  if (state.sel.k === null) {
    cards.push(h('div', { class: 'card' }, h('h4', {}, 'No key selected'),
      h('div', { class: 'hint' }, 'Click a key on the screen to edit it. Click an empty slot, then a preset, to place it \u2014 or drag a preset onto a slot. Delete clears the selected key, Ctrl+C / Ctrl+V copy and paste it, Ctrl+Z undoes.')));
    box.replaceChildren(...cards);
    return;
  }
  const kk = M.keyKind(k);
  const seg = h('div', { class: 'seg' }, [['action', 'Action'], ['folder', 'Folder'], ['empty', 'Empty']].map(([v, t]) =>
    h('button', { class: kk === v ? 'on' : '', onclick: () => { if (kk !== v) setKeyKind(v); } }, t)));
  const keyCard = h('div', { class: 'card' }, h('h4', {}, `Key ${state.sel.k + 1}`), seg);
  if (kk !== 'empty') {
    keyCard.append(field('Label', textInput(k.label || '', (v) => liveEdit(() => { const key = page().keys[state.sel.k]; if (v) key.label = v; else delete key.label; }, 'label'), { maxLength: 10, placeholder: 'short text' }), 'Up to about 10 characters fit.'),
      iconPicker(k), colorPicker(k));
    if (kk === 'folder') keyCard.append(folderEditor(k));
    else keyCard.append(actionEditor(k.action || null, (na) => commit(() => { const key = page().keys[state.sel.k]; if (na) key.action = na; else delete key.action; })));
  } else {
    keyCard.append(h('div', { class: 'hint' }, 'This slot is empty. Pick a type above, or click a preset below.'));
  }
  keyCard.append(h('div', { class: 'row', style: { marginTop: '10px' } },
    h('button', { class: 'small', disabled: kk === 'empty', onclick: copyKey }, 'Copy'),
    h('button', { class: 'small', disabled: !state.clip, onclick: pasteKey }, 'Paste'),
    h('button', { class: 'small danger', disabled: kk === 'empty', onclick: clearKey }, 'Clear'),
    h('button', { class: 'small', disabled: kk !== 'action', onclick: saveAsPreset, title: kk === 'folder' ? 'Folders cannot be presets' : '' }, 'Save as preset')));
  cards.push(keyCard);
  box.replaceChildren(...cards);
}

function copyKey() { const k = keyObj(); if (k && !M.isEmptyKey(k)) { state.clip = M.clone(k); toast('Key copied'); renderInspector(); } }
function pasteKey() {
  if (!state.clip || state.sel.k === null) return;
  const clip = M.clone(state.clip);
  if (clip.folder && !desk().pages.some((p) => p.id === clip.folder)) { toast('That folder points to a page on another desktop'); return; }
  commit(() => { page().keys[state.sel.k] = clip; });
}
function clearKey() { if (state.sel.k !== null) commit(() => { page().keys[state.sel.k] = {}; }); }

// ---- live screen settings ----------------------------------------------------------------------------

const checkRow = (label, checked, onchange) => h('label', { class: 'check' }, h('input', { type: 'checkbox', checked, onchange: (e) => onchange(e.target.checked) }), label);
const radioRow = (group, label, value, current, onPick, hint) => h('label', { class: 'check' },
  h('input', { type: 'radio', name: group, checked: current === value, onchange: () => onPick(value) }), h('span', {}, label, hint ? h('span', { class: 'hint' }, ` \u2014 ${hint}`) : null));

// A list row that can be dragged to reorder (same drop-position rule as the desktop list).
function sortableRow(row, index, mime, move) {
  const clear = () => row.classList.remove('drop-before', 'drop-after');
  row.draggable = true;
  row.addEventListener('dragstart', (e) => { e.dataTransfer.setData(mime, String(index)); e.dataTransfer.effectAllowed = 'move'; row.classList.add('dragging'); });
  row.addEventListener('dragend', () => { row.classList.remove('dragging'); document.querySelectorAll('.srow').forEach((r) => r.classList.remove('drop-before', 'drop-after')); });
  row.addEventListener('dragover', (e) => {
    if (![...e.dataTransfer.types].includes(mime)) return;
    e.preventDefault();
    const r = row.getBoundingClientRect();
    const after = e.clientY > r.top + r.height / 2;
    row.classList.toggle('drop-after', after);
    row.classList.toggle('drop-before', !after);
  });
  row.addEventListener('dragleave', (e) => { if (!row.contains(e.relatedTarget)) clear(); });
  row.addEventListener('drop', (e) => {
    const from = e.dataTransfer.getData(mime);
    if (from === '') return;
    e.preventDefault();
    const after = row.classList.contains('drop-after');
    clear();
    move(+from, M.dropTarget(+from, index, after));
  });
}

async function detectPlayers() {
  state.sourcesBusy = true;
  renderLiveConfig();
  try {
    state.sources = (await api('GET', '/api/media-sources')).sources;
    state.sourcesError = '';
    const list = document.getElementById('knownapps');
    if (list) list.replaceChildren(...state.sources.map((s) => h('option', { value: s.id })));
  } catch (e) {
    state.sources = null;
    state.sourcesError = e.message;
  }
  state.sourcesBusy = false;
  renderLiveConfig();
}

function nowPlayingConfig(d) {
  const o = M.liveOptions(d);
  const setFilter = (fn) => commit(() => { const f = M.liveOptions(d).filter; fn(f); M.setOptions(d, { filter: f }); });
  const setOpt = (patch) => commit(() => { M.setOptions(d, patch); });
  const addInput = h('input', { type: 'text', list: 'knownapps', placeholder: 'part of an app name, e.g. tidal', maxLength: 80, id: 'addApp' });
  const addApp = () => { if (addInput.value.trim()) setFilter((f) => { M.addFilterApps(f, [addInput.value]); }); };
  addInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); addApp(); } });

  const who = h('div', { class: 'card' }, h('h4', {}, 'Which players are shown'),
    M.FILTER_MODES.map((m) => radioRow('npmode', m.name, m.id, o.filter.mode, (v) => setFilter((f) => { f.mode = v; }))),
    h('div', { class: 'hint', id: 'filterSummary' }, M.describeFilter(o.filter)));
  if (o.filter.mode !== 'all') {
    who.append(...[ // Node.append() turns null into the text "null": keep only real children
      h('div', { class: 'appchips' }, o.filter.apps.map((a, i) => h('span', { class: 'appchip' }, a, h('button', { class: 'x', title: 'Remove', onclick: () => setFilter((f) => { f.apps.splice(i, 1); }) }, '\u2715')))),
      h('div', { class: 'row tight' }, addInput, h('button', { class: 'small', onclick: addApp }, 'Add')),
      h('div', { class: 'row tight', style: { marginTop: '8px' } }, h('span', { class: 'lab' }, 'Quick add:'),
        M.APP_GROUPS.map((g) => h('button', { class: 'small', title: g.apps.join(', '), onclick: () => setFilter((f) => { M.addFilterApps(f, g.apps); }) }, g.name))),
      h('div', { style: { marginTop: '10px' } }, h('button', { class: 'small', disabled: state.sourcesBusy, onclick: detectPlayers }, state.sourcesBusy ? 'Looking\u2026' : 'Detect running players')),
      state.sourcesError ? h('div', { class: 'hint bad' }, state.sourcesError) : null,
      state.sources ? h('div', { class: 'found' }, state.sources.length ? state.sources.map((src) => h('div', { class: 'foundrow' },
        h('span', { class: 'fid' }, src.id), h('span', { class: 'ds' }, [src.title, src.artist].filter(Boolean).join(' \u2014 ') || '(nothing loaded)'),
        src.playing ? h('span', { class: 'tag' }, 'playing') : null,
        h('button', { class: 'small', disabled: M.appMatches(src.id, o.filter.apps), onclick: () => setFilter((f) => { M.addFilterApps(f, [src.id]); }) }, M.appMatches(src.id, o.filter.apps) ? 'Listed' : 'Add')))
        : h('div', { class: 'hint' }, 'No media players are running right now.')) : null,
      h('div', { class: 'hint', style: { marginTop: '8px' } }, 'Names are matched by part, ignoring case. Some apps (Firefox) have a code as their Windows name: use "Detect" while they play.')].filter(Boolean));
  }

  const look = h('div', { class: 'card' }, h('h4', {}, 'Look'),
    checkRow('Tint the background with the cover colors', o.tint, (v) => setOpt({ tint: v })),
    checkRow('Show the album line', o.album, (v) => setOpt({ album: v })),
    h('div', { class: 'lab', style: { marginTop: '8px' } }, 'Controls'),
    radioRow('npctl', 'Full', 'full', o.controls, (v) => setOpt({ controls: v }), 'shuffle, previous, play/pause, next, repeat'),
    radioRow('npctl', 'Basic', 'basic', o.controls, (v) => setOpt({ controls: v }), 'previous, play/pause, next'));
  return [who, look];
}

function statsConfig(d) {
  const o = M.liveOptions(d);
  const setOpt = (patch) => commit(() => { M.setOptions(d, patch); });
  const { enabled, disabled } = M.statItemOrder(o.items);
  const info = (id) => M.STAT_ITEMS.find((x) => x.id === id);
  const move = (from, to) => commit(() => { const items = [...M.liveOptions(d).items]; if (M.moveInList(items, from, to)) M.setOptions(d, { items }); });
  const toggle = (id, on) => commit(() => { const items = [...M.liveOptions(d).items]; if (M.toggleStatItem(items, id, on)) M.setOptions(d, { items }); });

  const style = h('div', { class: 'card' }, h('h4', {}, 'Style'),
    M.STAT_STYLES.map((s) => h('button', { class: 'option' + (o.style === s.id ? ' on' : ''), dataset: { style: s.id }, onclick: () => setOpt({ style: s.id }) },
      h('b', {}, s.name), h('span', { class: 'ds' }, s.desc))));
  const rows = [];
  enabled.forEach((id, i) => {
    const row = h('div', { class: 'srow', dataset: { item: id } }, h('span', { class: 'grip', title: 'Drag to reorder' }, '\u22EE\u22EE'),
      h('input', { type: 'checkbox', checked: true, disabled: enabled.length <= 1, title: enabled.length <= 1 ? 'At least one item must stay' : 'Hide', onchange: (e) => toggle(id, e.target.checked) }),
      h('span', { class: 'nm' }, info(id).name), h('span', { class: 'ds' }, info(id).desc),
      h('button', { class: 'small', disabled: i === 0, title: 'Move up', onclick: () => move(i, i - 1) }, '\u2191'),
      h('button', { class: 'small', disabled: i === enabled.length - 1, title: 'Move down', onclick: () => move(i, i + 1) }, '\u2193'));
    sortableRow(row, i, 'application/x-sd-statitem', move);
    rows.push(row);
  });
  for (const id of disabled) {
    rows.push(h('div', { class: 'srow off', dataset: { item: id } }, h('span', { class: 'grip' }, ''),
      h('input', { type: 'checkbox', checked: false, title: 'Show', onchange: (e) => toggle(id, e.target.checked) }),
      h('span', { class: 'nm' }, info(id).name), h('span', { class: 'ds' }, info(id).desc)));
  }
  const items = h('div', { class: 'card' }, h('h4', {}, 'What to show'), h('div', { class: 'hint', style: { marginBottom: '6px' } }, 'Tick what you want and drag to set the order.'), rows,
    h('div', { class: 'hint' }, 'GPU and VRAM need an NVIDIA graphics card. CPU temperature is not available on Windows without extra software.'));
  return [style, items];
}

function renderLiveConfig() {
  const box = $('#liveConfig');
  if (isKeys()) { box.replaceChildren(); return; }
  const d = desk();
  box.replaceChildren(...(M.desktopKind(d) === 'nowplaying' ? nowPlayingConfig(d) : statsConfig(d)));
}

// ---- presets -----------------------------------------------------------------------------------------

function flatPresets() {
  const out = state.presets.user.map((p, idx) => ({ cat: 'user', catName: 'My presets', name: p.name, key: p.key, user: true, idx }));
  for (const c of state.presets.builtin) for (const p of c.presets) out.push({ cat: c.id, catName: c.name, name: p.name, key: p.key });
  return out;
}

function putPreset(slot, presetKey) {
  if (!isKeys()) return;
  const nk = M.clone(presetKey);
  if (nk.folder === 'new' && desk().pages.length >= M.LIMITS.pages) { toast('This desktop already has the maximum number of pages'); return; }
  commit(() => {
    if (nk.folder === 'new') nk.folder = desk().pages[M.addPage(desk(), nk.label || 'Folder')].id;
    page().keys[slot] = nk;
    state.sel.k = slot;
  });
}

function applyPreset(p) {
  if (!isKeys()) { toast('Select a keys desktop first'); return; }
  let slot = state.sel.k;
  if (slot === null) {
    slot = M.firstEmptySlot(page());
    if (slot === null) { toast('This page has no empty slot. Select a key to replace it, or enlarge the grid.'); return; }
  }
  putPreset(slot, p.key);
}

function fillWithCategory(cat) {
  if (!isKeys()) return;
  const items = flatPresets().filter((p) => p.cat === cat && !M.isEmptyKey(p.key) && p.key.folder !== 'new');
  const free = page().keys.map((k, i) => (M.isEmptyKey(k) ? i : -1)).filter((i) => i >= 0);
  const n = Math.min(items.length, free.length);
  if (!n) { toast('No empty slots on this page'); return; }
  commit(() => { for (let j = 0; j < n; j++) page().keys[free[j]] = M.clone(items[j].key); });
  toast(`Placed ${n} of ${items.length} presets` + (n < items.length ? ' (the page is full)' : ''));
}

async function saveUserPresets(list) {
  try {
    await api('PUT', '/api/presets/user', { presets: list });
    state.presets.user = list;
    renderPresets();
    return true;
  } catch (e) {
    banner('Could not save presets: ' + e.message);
    return false;
  }
}

function saveAsPreset() {
  const k = keyObj();
  if (!k || M.keyKind(k) !== 'action') return;
  const name = prompt('Name for this preset:', k.label || 'My key');
  if (!name || !name.trim()) return;
  const key = M.clone(k);
  saveUserPresets([...state.presets.user, { name: name.trim().slice(0, 40), key }]).then((ok) => ok && toast('Preset saved'));
}

function miniKey(key) {
  const empty = M.isEmptyKey(key);
  const el = h('div', { class: 'mini' + (empty ? ' empty' : ''), style: { background: empty ? '' : (key.color || M.DEFAULT_KEY_COLOR) } });
  if (empty) el.append('empty');
  else {
    if (key.icon && state.icons[key.icon]) el.append(h('span', { class: 'ico fa' }, glyph(key.icon)));
    if (key.label) el.append(h('span', { class: 'lbl' }, key.label));
  }
  return el;
}

function renderPresets() {
  if (!isKeys()) { $('#presets').replaceChildren(); return; } // live screens have settings instead (renderLiveConfig)
  const all = flatPresets();
  const cats = [{ id: 'all', name: 'All' }];
  if (state.presets.user.length) cats.push({ id: 'user', name: 'My presets' });
  for (const c of state.presets.builtin) cats.push({ id: c.id, name: c.name });
  const q = state.query.trim().toLowerCase();
  const shown = all.filter((p) => (state.cat === 'all' || p.cat === state.cat) &&
    (!q || p.name.toLowerCase().includes(q) || (p.key.label || '').toLowerCase().includes(q) || M.describeAction(p.key.action).toLowerCase().includes(q)));
  const grid = h('div', { class: 'pgrid' });
  for (const p of shown) {
    const card = h('div', { class: 'pcard', draggable: true, title: p.key.folder ? 'A folder with a new page' : M.describeAction(p.key.action), onclick: () => applyPreset(p) },
      miniKey(p.key), h('div', { class: 'nm' }, p.name),
      p.user ? h('button', { class: 'small danger del', title: 'Delete this preset', onclick: (e) => { e.stopPropagation(); saveUserPresets(state.presets.user.filter((_, i) => i !== p.idx)); } }, '\u2715') : null);
    card.addEventListener('dragstart', (e) => { e.dataTransfer.setData('application/x-sd-preset', String(all.indexOf(p))); e.dataTransfer.effectAllowed = 'copy'; });
    grid.append(card);
  }
  if (!shown.length) grid.append(h('div', { class: 'hint' }, 'No presets match.'));
  const canFill = isKeys() && state.cat !== 'all' && state.cat !== 'layout';
  $('#presets').replaceChildren(
    h('h3', {}, 'Button presets'),
    h('div', { class: 'phead' },
      h('input', { type: 'text', placeholder: 'Search presets\u2026', value: state.query, oninput: (e) => { state.query = e.target.value; renderPresetGrid(grid, e.target); } }),
      h('button', { class: 'small', disabled: !canFill, title: 'Put every preset of the selected category into the empty slots of this page', onclick: () => fillWithCategory(state.cat) }, 'Add category to page'),
      h('span', { class: 'hint' }, 'Click a preset to place it in the selected (or first empty) slot, or drag it onto a slot.')),
    h('div', { class: 'chips' }, cats.map((c) => h('button', { class: 'chip' + (state.cat === c.id ? ' on' : ''), onclick: () => { state.cat = c.id; renderPresets(); } }, c.name))),
    grid);
}

// Typing in the search box must not rebuild the box itself (it would lose focus): rebuild the grid only.
function renderPresetGrid() { renderPresets(); const input = $('#presets input'); input.focus(); input.setSelectionRange(input.value.length, input.value.length); }

// ---- rendering ---------------------------------------------------------------------------------------

function renderAll() {
  renderTopbar();
  renderTree();
  renderTreeSettings();
  renderScreen();
  renderPageControls();
  renderInspector();
  renderPresets();
  renderLiveConfig();
}

function renderLive() { // after typing: leave the inspector, the settings and the presets alone
  renderTopbar();
  renderTree();
  renderScreen();
}

// ---- keyboard ----------------------------------------------------------------------------------------

function onKeyDown(e) {
  if (state.capturing) return;
  if (e.key === 'Escape' && state.addMenu) { state.addMenu = false; renderTree(); return; }
  const tag = (e.target.tagName || '').toLowerCase();
  const typing = tag === 'input' && !['checkbox', 'range', 'color'].includes(e.target.type) || tag === 'textarea' || tag === 'select';
  const mod = e.ctrlKey || e.metaKey;
  if (mod && e.key.toLowerCase() === 's') { e.preventDefault(); save(); return; }
  if (typing) return;
  if (mod && e.key.toLowerCase() === 'z') { e.preventDefault(); e.shiftKey ? redo() : undo(); return; }
  if (mod && e.key.toLowerCase() === 'y') { e.preventDefault(); redo(); return; }
  if (!isKeys() || state.sel.k === null) return;
  if (mod && e.key.toLowerCase() === 'c') { e.preventDefault(); copyKey(); return; }
  if (mod && e.key.toLowerCase() === 'v') { e.preventDefault(); pasteKey(); return; }
  if (e.key === 'Delete' || e.key === 'Backspace') { e.preventDefault(); clearKey(); return; }
  const pg = page();
  const moves = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -pg.cols, ArrowDown: pg.cols };
  if (e.key in moves) {
    e.preventDefault();
    const col = state.sel.k % pg.cols;
    if ((e.key === 'ArrowLeft' && col === 0) || (e.key === 'ArrowRight' && col === pg.cols - 1)) return;
    const next = state.sel.k + moves[e.key];
    if (next >= 0 && next < pg.cols * pg.rows) { state.sel.k = next; renderScreen(); renderInspector(); }
  }
}

// ---- start -------------------------------------------------------------------------------------------

async function start() {
  const names = [...M.NAMED_KEYS, ...Array.from({ length: 24 }, (_, i) => `f${i + 1}`)];
  document.body.append(h('datalist', { id: 'keynames' }, names.map((n) => h('option', { value: n }))), h('datalist', { id: 'knownapps' }));
  try {
    const [icons, presets, , templates] = await Promise.all([fetch('/icons.json').then((r) => r.json()), api('GET', '/api/presets'), loadDeck(),
      fetch('/templates.json').then((r) => r.json())]);
    state.icons = icons;
    state.templates = templates.templates;
    state.presets = { builtin: presets.builtin, user: presets.user };
    renderAll();
  } catch (e) {
    banner('Could not load the deck: ' + e.message);
    return;
  }
  document.addEventListener('keydown', onKeyDown);
  window.addEventListener('beforeunload', (e) => { if (isDirty()) { e.preventDefault(); e.returnValue = ''; } });
  refreshStatus();
  setInterval(refreshStatus, 3000);
}

start();
