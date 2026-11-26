// Deck editor UI. Pure logic lives in model.js; this file builds the DOM (never from HTML strings: all text goes
// through textContent) and talks to the companion's small JSON API.
import * as M from './model.js';

const S = 2; // preview scale: the board screen is 320 x 240, shown at 640 x 480
const TOKEN = document.querySelector('meta[name="sd-token"]').content;
const $ = (sel) => document.querySelector(sel);
const SWATCHES = ['#2A3A4D', '#B3261E', '#C77700', '#2E7D32', '#1F6F5C', '#00838F', '#2F6FDB', '#5B3A8C', '#8E24AA', '#455A64'];
const KIND_ICON = { keys: 'keyboard', nowplaying: 'audio', stats: 'charge', mixer: 'volume_up', clock: 'bell' };
const KIND_LABEL = { keys: 'Keys', nowplaying: 'Now playing', stats: 'PC stats', mixer: 'Volume mixer', clock: 'Clock' };
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
  cat: 'all', query: '', status: null, clip: null, saving: false, capturing: false,
  templates: [], addMenu: false, view: 'deck', // 'deck' = the desktop editor, 'settings' = general settings
  runningApps: null, previewOn: false,                    // programs with a window (datalist of the "programs" fields); preview a key's "on" look
  sources: null, sourcesBusy: false, sourcesError: '', sourcesTried: false, // media players running now (Now playing filter)
  catalog: { categories: [], apps: [] }, appSearch: '',   // known apps with icons (apps.json) and the library search box
};
const history = new M.History();
let lastCoalesce = { key: null, t: 0 };
const TOAST_MS = 5000, UNDO_TOAST_MS = 10000;
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
  dropDeckUndo();
  if (!(coalesce && lastCoalesce.key === coalesce && now - lastCoalesce.t < 1500)) history.push(deckText());
  lastCoalesce = { key: coalesce, t: now };
  fn(state.deck);
  clampSel();
  if (rerender === 'all') renderAll();
  else if (rerender === 'sample') { renderTopbar(); if (state.drawSample) state.drawSample(); }
  else renderLive();
}

// After an undo or a redo, the next edit is a step of its own again (even in the same field, right away).
function newUndoStep() {
  lastCoalesce = { key: null, t: 0 };
  dropDeckUndo();
}

function restore(text) {
  newUndoStep();
  state.deck = JSON.parse(text);
  clampSel();
  renderAll();
  previewSettings(); // undo / redo may have changed a setting
}
const undo = () => { const t = history.undo(deckText()); if (t) restore(t); };
const redo = () => { const t = history.redo(deckText()); if (t) restore(t); };

// ---- live preview of the general settings on the board -----------------------------------------------------

let previewTimer = null;
let lastPreviewed = '';

async function previewSettings(now = false) {
  clearTimeout(previewTimer);
  const send = async () => {
    const settings = M.resolveSettings(state.deck);
    const text = JSON.stringify(settings);
    if (text === lastPreviewed) return;
    lastPreviewed = text;
    try {
      const res = await api('POST', '/api/board/settings', { settings });
      state.previewSent = res.sent;
    } catch (e) {
      state.previewSent = false;
    }
    const note = document.querySelector('#previewNote');
    if (note) note.textContent = previewNoteText();
  };
  if (now) await send();
  else previewTimer = setTimeout(send, 150);
}

const previewNoteText = () => (state.previewSent === false || !(state.status && state.status.connected)
  ? 'Board disconnected. Saved changes apply when it reconnects.'
  : 'Live on the board. Save to keep.');

async function testSound(name) {
  await previewSettings(true); // the board should play the theme that is chosen here, not the one it had
  try {
    const res = await api('POST', '/api/board/sound', { name });
    if (!res.sent) toast('Board disconnected.');
  } catch (e) {
    toast('Couldn\u2019t play the sound: ' + e.message);
  }
}

// ---- api ---------------------------------------------------------------------------------------------

async function api(method, path, body) {
  const r = await fetch(path, {
    method,
    headers: { 'Content-Type': 'application/json', 'X-Streamdeck-Token': TOKEN },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  let data = {};
  try { data = await r.json(); } catch (e) { if (r.ok) throw new Error(`not valid JSON (${e.message})`); }
  if (!r.ok) throw new Error(data.error || r.statusText);
  return data;
}

// A short message at the bottom. With `undo` it has an Undo button and stays longer. `deck: true` means that button undoes
// the last change to the deck: it goes away when the deck changes again, so it can never undo something else.
function toast(text, { undo: back = null, deck = false } = {}) {
  const box = $('#toast');
  const item = h('div', { class: 'toastItem ' + (back ? 'withUndo' : 'plain') + (deck ? ' deckUndo' : '') }, h('span', {}, text));
  let timer = null;
  const close = () => { clearTimeout(timer); item.remove(); box.hidden = !box.children.length; };
  if (back) item.append(h('button', { class: 'small', onclick: () => { close(); back(); } }, 'Undo'));
  else box.querySelectorAll('.toastItem.plain').forEach((old) => old.remove());
  box.append(item);
  while (box.children.length > 4) box.firstChild.remove();
  box.hidden = false;
  timer = setTimeout(close, back ? UNDO_TOAST_MS : TOAST_MS);
}

function dropDeckUndo() {
  const box = $('#toast');
  box.querySelectorAll('.toastItem.deckUndo').forEach((item) => item.remove());
  box.hidden = !box.children.length;
}

function banner(text, kind = 'error') {
  const el = $('#banner');
  if (!text) { el.hidden = true; return; }
  el.className = kind === 'warn' ? 'warn' : '';
  el.replaceChildren(h('span', {}, text), h('button', { class: 'small', onclick: () => { el.hidden = true; } }, 'Dismiss'));
  el.hidden = false;
}

async function save() {
  if (state.saving || !isDirty()) return;
  state.saving = true;
  renderTopbar();
  try {
    const res = await api('PUT', '/api/deck', trimmedDeck());
    state.savedText = deckText();
    if (res.warnings && res.warnings.length) banner('Saved, with warnings:\n' + res.warnings.map((w) => M.describeProblem(w, state.deck)).join('\n'), 'warn');
    else banner(null);
    toast(res.board ? 'Saved' : 'Saved. The board updates when it reconnects.');
  } catch (e) {
    banner('Not saved. ' + M.describeProblem(e.message, state.deck));
  } finally {
    state.saving = false;
    renderTopbar();
  }
}

async function revert() {
  if (!isDirty()) return;
  const discarded = deckText();
  const shown = lastPreviewed;
  try { await loadDeck(); } catch (e) { banner(loadProblem(e)); return; }
  history.push(discarded); // Undo brings the changes back
  newUndoStep();
  if (shown !== lastPreviewed) { // the board was showing previewed settings: put the saved ones back
    const saved = lastPreviewed;
    lastPreviewed = shown;
    await previewSettings(true);
    lastPreviewed = saved;
  }
  renderAll();
  toast('Changes discarded', { undo, deck: true });
}

const loadProblem = (e) => `Couldn\u2019t load the deck: ${e.message}. Reload to try again; the previous save is in deck.json.bak.`;

async function loadDeck() {
  let deck;
  try { deck = await api('GET', '/api/deck'); } catch (e) { e.deck = true; throw e; }
  state.deck = normalize(deck);
  state.savedText = deckText();
  clampSel();
  lastPreviewed = JSON.stringify(M.resolveSettings(state.deck)); // what the board holds after loading (or discarding changes)
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
  let pill = h('span', { class: 'pill off' }, 'Can\u2019t reach Swipedeck. Check it\u2019s running, then reload.');
  if (st) pill = st.connected ? h('span', { class: 'pill on' }, 'Board connected') : h('span', { class: 'pill off' }, 'Board disconnected');
  $('#topbar').replaceChildren(
    h('span', { class: 'brand' }, 'Swipedeck'),
    h('nav', { class: 'tabs' },
      h('button', { class: 'tab' + (state.view === 'deck' ? ' on' : ''), onclick: () => setView('deck') }, 'Desktops'),
      h('button', { class: 'tab' + (state.view === 'settings' ? ' on' : ''), onclick: () => setView('settings') }, 'Settings')),
    pill, h('span', { class: 'spacer' }),
    h('button', { onclick: undo, disabled: !history.canUndo, title: 'Undo (Ctrl+Z)' }, 'Undo'),
    h('button', { onclick: redo, disabled: !history.canRedo, title: 'Redo (Ctrl+Y)' }, 'Redo'),
    h('button', { onclick: revert, disabled: !isDirty() }, 'Discard changes'),
    h('button', { class: 'primary', onclick: save, disabled: !isDirty() || state.saving, title: 'Save (Ctrl+S)' },
      isDirty() ? 'Save' : 'Saved'),
  );
}

// ---- tree --------------------------------------------------------------------------------------------

function addMenu() {
  const hasNp = M.hasKind(state.deck, 'nowplaying'), hasStats = M.hasKind(state.deck, 'stats');
  const hasMixer = M.hasKind(state.deck, 'mixer'), hasClock = M.hasKind(state.deck, 'clock');
  const item = (icon, name, desc, onclick, why = '') => h('button', { class: 'item', disabled: !!why, onclick },
    h('span', { class: 'fa ic' }, glyph(icon)),
    h('span', { class: 'tx' }, h('span', { class: 'nm' }, name), why || desc ? h('span', { class: 'ds' }, why || desc) : null));
  return h('div', { class: 'addmenu' },
    item('keyboard', 'Blank', '', () => addDesktopOfKind('keys')),
    h('div', { class: 'sec' }, 'Live'),
    item('audio', 'Now playing', 'Song, cover and controls', () => addDesktopOfKind('nowplaying'), hasNp ? 'Already added' : ''),
    item('charge', 'PC stats', 'CPU, GPU, RAM, disk and network', () => addDesktopOfKind('stats'), hasStats ? 'Already added' : ''),
    item('volume_up', 'Volume mixer', 'Volume per app', () => addDesktopOfKind('mixer'), hasMixer ? 'Already added' : ''),
    item('bell', 'Clock & timer', 'Time, weather and a countdown timer', () => addDesktopOfKind('clock'), hasClock ? 'Already added' : ''),
    h('div', { class: 'sec' }, 'Templates'),
    state.templates.map((t) => item(t.icon, t.name, t.description, () => addTemplate(t))));
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
      h('span', { class: 'name' }, d.name || 'Untitled'),
      h('button', { class: 'small', title: 'Move up', disabled: i === 0, onclick: (e) => { e.stopPropagation(); moveDesktop(i, i - 1); } }, '\u2191'),
      h('button', { class: 'small', title: 'Move down', disabled: i === state.deck.desktops.length - 1, onclick: (e) => { e.stopPropagation(); moveDesktop(i, i + 1); } }, '\u2193'),
      h('button', { class: 'small danger', title: 'Delete desktop', disabled: state.deck.desktops.length <= 1, onclick: (e) => { e.stopPropagation(); deleteDesktop(i); } }, '\u2715'),
    );
    const box = h('div', { class: 'desk' + (active ? ' active' : '') }, row);
    if (kind === 'keys') {
      const pages = h('div', { class: 'pages' });
      d.pages.forEach((p, pi) => {
        pages.append(h('div', { class: 'pagerow' + (active && pi === state.sel.p ? ' active' : ''), dataset: { page: p.id }, onclick: () => { state.sel = { d: i, p: pi, k: null }; renderAll(); } },
          h('span', { class: 't' }, M.pageName(d, p)),
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

function moveDesktop(from, to) {
  commit((d) => { M.moveInList(d.desktops, from, to); state.sel = { d: to, p: 0, k: null }; });
}
function deleteDesktop(i) {
  const name = state.deck.desktops[i].name || 'Untitled';
  commit((d) => { M.removeDesktop(d, i); });
  toast(`Deleted "${name}"`, { undo, deck: true });
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
  const title = d.pages[pi].title || d.pages[pi].id;
  commit((deck) => { M.removePage(deck.desktops[di], pi); if (state.sel.d === di) state.sel.p = 0; state.sel.k = null; });
  toast(`Deleted page "${title}"` + (refs ? `, emptied ${refs} folder key${refs > 1 ? 's' : ''}` : ''), { undo, deck: true });
}

// ---- the board screen preview ------------------------------------------------------------------------

function keyEl(k, i, geo) {
  const kind = M.keyKind(k);
  const { x, y } = geo.pos(i);
  const lit = state.previewOn && state.sel.k === i && M.stateKind(k) !== 'none';  // the editor previews the "on" look of the selected key
  if (lit) k = { ...k, ...M.stateLook(k) };
  const unknownIcon = k.icon && !state.icons[k.icon];
  const el = h('div', {
    class: 'key ' + kind + (state.sel.k === i ? ' sel' : ''),
    draggable: kind !== 'empty',
    dataset: { i },
    title: kind === 'empty' ? 'Empty key' : kind === 'folder' ? `Folder \u2192 ${(desk().pages.find((p) => p.id === k.folder) || {}).title || k.folder}` : M.describeAction(k.action)
      + (k.repeat ? ' \u00B7 repeats while held' : k.hold ? ` \u00B7 held: ${M.describeAction(k.hold)}` : '')
      + (M.stateKind(k) !== 'none' ? ' \u00B7 changes look' : ''),
    style: { left: x * S + 'px', top: y * S + 'px', width: geo.keyW * S + 'px', height: geo.keyH * S + 'px',
      borderRadius: (geo.radius ?? M.SETTING_DEFAULTS.keyRadius) * S + 'px',
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
  return h('div', { class: 'mock', dataset: { tint: o.tint, album: o.album, controls: o.controls, appicon: o.appIcon }, style: { background: o.tint ? '#2a2140' : '#1b2535' } },
    h('div', { style: abs({ left: px(9), top: px(9), width: px(140), height: px(140), background: o.tint ? '#3d3158' : '#2c3a52', display: 'flex', alignItems: 'center', justifyContent: 'center' }) }, fa('audio', 40, { opacity: .4 })),
    o.album ? h('div', { class: 'm-album', style: abs({ left: px(161), top: px(8), fontSize: px(14), opacity: .6 }) }, 'Album') : null,
    h('div', { style: abs({ left: px(161), top: px(28 + shift), right: px(9), fontSize: px(28), lineHeight: 1.15 }) }, 'Song title'),
    h('div', { style: abs({ left: px(161), top: px(100 + shift), fontSize: px(16), opacity: .9 }) }, 'Artist'),
    h('div', { style: abs({ left: px(161), top: px(134 + shift), fontSize: px(14), opacity: .6 }) }, '1:23 / 3:45'),
    o.appIcon ? h('span', { class: 'm-appicon fa', style: abs({ left: px(291), top: px(132 + shift), fontSize: px(18), color: '#1DB954' }) }, String.fromCodePoint(0xf1bc)) : null,
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

// A box placed at x, y with a size, in board pixels (the preview is drawn at S times that).
const boxAt = (x, y, w, ht, style = {}, ...kids) => h('div', { style: { position: 'absolute', left: x * S + 'px', top: y * S + 'px',
  width: w * S + 'px', height: ht * S + 'px', ...style } }, ...kids);
const labelAt = (x, y, w, size, color, text, extra = {}) => boxAt(x, y, w, size * 1.3, { fontSize: size * S + 'px', lineHeight: size * 1.3 * S + 'px', color, whiteSpace: 'nowrap', overflow: 'hidden', ...extra }, text);

// The volume mixer: a master fader and up to four programs (sample values: the real ones come from the PC).
function mockMixer(o) {
  const names = [...o.apps, 'spotify', 'chrome', 'discord', 'steam'].filter((n, i, a) => a.indexOf(n) === i).slice(0, 4);
  const cols = [['Master', 60, false, '#3B82F6'], ...names.map((n, i) => [n.charAt(0).toUpperCase() + n.slice(1), [80, 45, 30, 90][i], i === 1, '#1ED760'])];
  const x0 = (320 - (cols.length * 56 + (cols.length - 1) * 8)) / 2;
  return h('div', { class: 'mock', dataset: { kind: 'mixer' }, style: { background: '#101722' } }, cols.map(([name, vol, muted, color], i) => {
    const accent = muted ? '#55657A' : color;
    return boxAt(x0 + i * 64, 0, 56, 210, {},
      labelAt(0, 4, 56, 20, muted ? '#7F8FA6' : '#fff', String(vol), { textAlign: 'center' }),
      boxAt(13, 38, 30, 104, { background: '#2A3A4D', borderRadius: 4 * S + 'px' },
        boxAt(0, 104 * (1 - vol / 100), 30, 104 * vol / 100, { background: accent, borderRadius: 4 * S + 'px' }),
        boxAt(0, 104 * (1 - vol / 100) - 6, 30, 12, { background: muted ? '#7F8FA6' : '#E6EDF7', borderRadius: 4 * S + 'px' })),  // the cap: as wide as the fader, same corners
      boxAt(6, 156, 44, 30, { background: '#2A3A4D', borderRadius: 8 * S + 'px', display: 'flex', alignItems: 'center', justifyContent: 'center', color: muted ? '#F04438' : '#fff', fontSize: 16 * S + 'px' },
        h('span', { class: 'fa' }, glyph(muted ? 'mute' : 'volume_up'))),
      labelAt(0, 188, 56, 14, '#C5D1E3', name, { textAlign: 'center', textOverflow: 'ellipsis' }));
  }));
}

// The clock screen: time, weather and timer, as laid out in clockscreen.cpp (sample numbers).
function mockClock(o) {
  const yOff = o.timer ? 0 : 36;
  const weather = o.weather && o.place;
  const x0 = weather ? 16 : 62;
  const kids = [
    // HH:MM, or HH:MM:SS in a slightly smaller font; AM / PM sits on the date's row
    labelAt(x0, (o.seconds ? 10 : 4) + yOff, 190, o.seconds ? 40 : 48, '#fff', (o.hour12 ? '2:05' : '14:05') + (o.seconds ? ':30' : ''), { fontWeight: 600 }),
    labelAt(x0, 62 + yOff, 126, 18, '#C5D1E3', 'Thu 19 Nov'),
    o.hour12 ? labelAt(x0 + 136, 62 + yOff, 50, 18, '#7F8FA6', 'PM', { textAlign: 'right' }) : null,
  ];
  if (weather) {
    const unit = o.units === 'f' ? 'F' : 'C';
    kids.push(labelAt(206, 2 + yOff, 108, 32, '#fff', o.units === 'f' ? '48\u00B0F' : '9\u00B0C'), labelAt(206, 40 + yOff, 108, 16, '#C5D1E3', 'Overcast'),
      labelAt(206, 61 + yOff, 108, 14, '#7F8FA6', o.units === 'f' ? 'H 50\u00B0  L 43\u00B0' : 'H 10\u00B0  L 6\u00B0'), labelAt(206, 79 + yOff, 108, 14, '#7F8FA6', o.place.name));
    void unit;
  }
  if (o.timer) {
    kids.push(boxAt(16, 102, 288, 2, { background: '#2A3A4D' }), labelAt(16, 120, 140, 40, '#fff', '05:00'));
    ['1m', '5m', '10m', '25m'].forEach((t, i) => kids.push(boxAt(158 + i * 40, 112, 34, 32, { background: i === 1 ? '#3B82F6' : '#2A3A4D', borderRadius: 10 * S + 'px', color: '#fff',
      fontSize: 14 * S + 'px', display: 'flex', alignItems: 'center', justifyContent: 'center' }, t)));
    kids.push(boxAt(158, 152, 92, 46, { background: '#2E7D32', borderRadius: 10 * S + 'px', color: '#fff', fontSize: 18 * S + 'px', display: 'flex', alignItems: 'center', justifyContent: 'center' }, 'Start'),
      boxAt(256, 152, 56, 46, { background: '#2A3A4D', borderRadius: 10 * S + 'px', color: '#fff', fontSize: 20 * S + 'px', display: 'flex', alignItems: 'center', justifyContent: 'center' }, h('span', { class: 'fa' }, glyph('refresh'))));
  }
  return h('div', { class: 'mock', dataset: { kind: 'clock' }, style: { background: '#101722' } }, kids);
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
    const look = M.resolveSettings(state.deck);
    const geo = M.geometry(pg.cols, pg.rows, undefined, undefined, look.keyGap);
    geo.radius = look.keyRadius;
    for (let i = 0; i < pg.cols * pg.rows; i++) content.append(keyEl(pg.keys[i] || {}, i, geo));
  } else {
    const mocks = { nowplaying: mockNowPlaying, stats: mockStats, mixer: mockMixer, clock: mockClock };
    content.append(mocks[kind](M.liveOptions(d)));
  }
  screen.append(content);
  const head = h('div', { class: 'stagehead' }, h('b', {}, d.name),
    kind === 'keys' ? `\u203A ${pg.title || pg.id}` : KIND_LABEL[kind],
    h('span', { class: 'hint' }, kind === 'keys' ? 'Drag keys to swap' : 'Sample data'));
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
    commit(() => { M.resizePage(page(), c, r); });
    if (lost) toast(`${lost} key${lost > 1 ? 's' : ''} removed`, { undo, deck: true });
  };
  const stepper = (label, value, set) => h('span', { class: 'stepper' }, label,
    h('button', { class: 'small', onclick: () => set(value - 1) }, '\u2212'), h('b', {}, value), h('button', { class: 'small', onclick: () => set(value + 1) }, '+'));
  box.replaceChildren(
    h('span', { class: 'stepper' }, 'Page name ',
      boardText(pg.title || '', (v) => commit(() => { page().title = v; }, { coalesce: 'ptitle', rerender: 'live' }), { maxLength: 24, style: { width: '170px' } })),
    stepper('Columns', pg.cols, (v) => resize(v, pg.rows)),
    stepper('Rows', pg.rows, (v) => resize(pg.cols, v)));
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

// A name the board will show: it says so when the text has characters the board's fonts lack.
function boardText(value, onInput, attrs) {
  const warning = h('div', { class: 'hint bad accents' }, 'The board can\u2019t show accents or emoji.');
  warning.hidden = !M.boardCannotShow(value);
  const input = textInput(value, (v) => { warning.hidden = !M.boardCannotShow(v); onInput(v); }, attrs);
  return h('div', { class: 'boardtext' }, input, warning);
}

// App names, picked one at a time: each becomes a chip and the box suggests apps for the next one. (A browser matches a
// <datalist> against the box's whole value, so one comma-separated box stops suggesting after the first name.)
function appsField(id, apps, max, placeholder, onChange) {
  const input = apps.length < max ? h('input', { type: 'text', id, class: 'appadd', list: 'runningApps',
    placeholder: apps.length ? 'Add another' : placeholder, onfocus: loadRunningApps }) : null;
  const apply = (next, refocus) => {
    onChange(next);
    const again = refocus && document.getElementById(id);
    if (again) again.focus();
  };
  const add = () => {
    const typed = M.parseApps(input.value);
    const refocus = document.activeElement === input; // not when the change came from leaving the box
    input.value = '';
    const next = [...new Set([...apps, ...typed])].slice(0, max);
    if (next.length !== apps.length) apply(next, refocus);
  };
  if (input) {
    input.addEventListener('change', add);
    input.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ',') { e.preventDefault(); add(); } });
  }
  return h('div', { class: 'appsfield', dataset: { apps: id } },
    apps.length ? h('div', { class: 'appchips' }, apps.map((a) => h('span', { class: 'appchip' }, a,
      h('button', { class: 'x', title: `Remove ${a}`, onclick: () => apply(apps.filter((x) => x !== a), false) }, '\u2715')))) : null,
    input);
}

function startCapture(button, p, onChange) {
  button.textContent = 'Press keys\u2026 (Esc cancels)';
  const done = () => { document.removeEventListener('keydown', handler, true); state.capturing = false; button.textContent = 'Record'; };
  const handler = (e) => {
    if (['Control', 'Shift', 'Alt', 'Meta'].includes(e.key)) return; // wait for the main key
    e.preventDefault();
    e.stopPropagation();
    done();
    if (e.code === 'Escape' && !e.ctrlKey && !e.shiftKey && !e.altKey) return; // Esc alone cancels
    const name = M.keyNameFromCode(e.code);
    if (!name) { toast('Can\u2019t use that key. Try another.'); return; }
    p.ctrl = e.ctrlKey; p.shift = e.shiftKey; p.alt = e.altKey; p.key = name; // the Windows key cannot be captured: tick it
    onChange(M.composeHotkey(p), true);
  };
  state.capturing = true;
  document.addEventListener('keydown', handler, true);
}

function hotkeyEditor(action) {
  const id = 'hk' + nextEditorId++;
  const p = M.parseHotkey(action.keys);
  const change = (keys, structural) => {
    commit(() => { action.keys = keys; }, { coalesce: id, rerender: structural ? 'all' : 'live' });
  };
  const keyIn = textInput(p.key, (v, el) => { p.key = v; el.classList.toggle('bad', !!v && !M.isValidKey(v)); change(M.composeHotkey(p), false); },
    { list: 'keynames', placeholder: 'e.g. s, f5, enter, volume_up', class: p.key && !M.isValidKey(p.key) ? 'bad' : '' });
  const cap = h('button', { class: 'small' }, 'Record');
  cap.addEventListener('click', () => { if (!state.capturing) startCapture(cap, p, change); });
  return h('div', {},
    h('div', { class: 'row mods' }, ['ctrl', 'shift', 'alt', 'win'].map((m) =>
      h('label', {}, h('input', { type: 'checkbox', checked: p[m], onchange: (e) => { p[m] = e.target.checked; change(M.composeHotkey(p), true); } }), m[0].toUpperCase() + m.slice(1)))),
    h('div', { class: 'field sub' }, h('label', {}, 'Key'), h('div', { class: 'row tight' }, keyIn, cap)),
    h('div', { class: 'hint' }, 'Can\u2019t record Win; tick it.'));
}

// `replace(newAction)` swaps the whole action (type change); field edits mutate `action` in place.
function actionEditor(action, replace, inMulti = false) {
  const id = 'act' + nextEditorId++;
  const types = inMulti ? ['hotkey', 'text', 'open', 'run', 'lock', 'http', 'obs', 'wait'] : ['hotkey', 'text', 'open', 'run', 'lock', 'http', 'obs', 'multi'];
  const names = { hotkey: 'Press keys', text: 'Type text', open: 'Open', run: 'Run command', lock: 'Lock the PC',
    http: 'Send web request', obs: 'Control OBS', multi: 'Several steps', wait: 'Wait' };
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
      (v) => edit(() => { action.command = action.shell ? v : M.splitCommand(v); }), { placeholder: 'app and arguments' });
    body = h('div', {}, field('Command', input),
      h('label', { class: 'row tight' }, h('input', { type: 'checkbox', checked: shell, onchange: (e) => commit(() => {
        if (e.target.checked) { action.command = Array.isArray(action.command) ? M.joinCommand(action.command) : action.command; action.shell = true; }
        else { action.command = M.splitCommand(action.command || ''); delete action.shell; }
      }) }), 'Use command prompt (for commands like "code")'));
  } else if (cur === 'wait') {
    body = field('Seconds', h('input', { type: 'number', min: 0, max: 30, step: 0.1, value: action.wait, oninput: (e) => edit(() => { action.wait = Math.min(30, Math.max(0, +e.target.value || 0)); }) }));
  } else if (cur === 'http') body = httpEditor(action, edit);
  else if (cur === 'obs') body = obsEditor(action, edit);
  else if (cur === 'multi') body = multiEditor(action);
  return h('div', {}, field('Action', select), body);
}

// A box for JSON typed by hand: it saves only what parses, and says so when it does not.
function jsonBox(value, onValid, rows = 4) {
  const status = h('div', { class: 'hint' });
  const area = h('textarea', { rows, spellcheck: false, value: value === undefined ? '' : JSON.stringify(value, null, 2) });
  area.addEventListener('input', () => {
    const r = M.parseJsonBody(area.value);
    status.className = 'hint' + (r.ok ? '' : ' bad');
    status.textContent = r.ok ? '' : r.error;
    if (r.ok) onValid(r.value);
  });
  return h('div', {}, area, status);
}

function httpEditor(action, edit) {
  const method = (action.method || 'GET').toUpperCase();
  const mode = action.json !== undefined ? 'json' : action.body !== undefined ? 'text' : 'none';
  const headers = h('textarea', { rows: 2, spellcheck: false, placeholder: 'Authorization: Bearer ${HA_TOKEN}', value: M.joinHeaders(action.headers) });
  headers.addEventListener('input', () => edit(() => {
    const parsed = M.parseHeaders(headers.value);
    if (Object.keys(parsed).length) action.headers = parsed; else delete action.headers;
  }));
  const bodyMode = h('select', { onchange: (e) => commit(() => {
    delete action.json;
    delete action.body;
    if (e.target.value === 'json') action.json = {};
    if (e.target.value === 'text') action.body = '';
  }) }, [['none', 'No body'], ['json', 'JSON'], ['text', 'Text']].map(([v, t]) => h('option', { value: v, selected: mode === v }, t)));
  const box = h('div', { id: 'httpEditor' },
    h('div', { class: 'row' },
      h('select', { id: 'httpMethod', onchange: (e) => edit(() => { action.method = e.target.value; }) },
        M.HTTP_METHODS.map((m) => h('option', { value: m, selected: method === m }, m))),
      textInput(action.url || '', (v) => edit(() => { action.url = v; }), { placeholder: 'http://homeassistant.local:8123/api/services/light/toggle', id: 'httpUrl', style: { flex: '1', minWidth: '180px' } })),
    field('Headers (one per line)', headers),
    field('Body', bodyMode));
  if (mode === 'json') box.append(jsonBox(action.json, (v) => edit(() => { action.json = v; })));
  if (mode === 'text') {
    const area = h('textarea', { rows: 3, value: action.body || '' });
    area.addEventListener('input', () => edit(() => { action.body = area.value; }));
    box.append(area);
  }
  box.append(field('Timeout (seconds)', h('input', { type: 'number', min: 1, max: 30, step: 1, value: action.timeout ?? 5, style: { width: '90px' },
    oninput: (e) => edit(() => { const t = Math.min(30, Math.max(1, +e.target.value || 5)); if (t === 5) delete action.timeout; else action.timeout = t; }) })),
  h('div', { class: 'hint' }, 'Keep tokens out of the deck file: write ${NAME} to use an environment variable.'));
  return box;
}

function obsEditor(action, edit) {
  const info = M.obsRequestInfo(action.request);
  const choose = h('select', { id: 'obsRequest', onchange: (e) => commit(() => {
    delete action.data;
    action.request = e.target.value === '__other' ? 'GetVersion' : e.target.value;
  }) }, M.OBS_REQUESTS.map((r) => h('option', { value: r.id, selected: action.request === r.id }, r.label)),
  h('option', { value: '__other', selected: !info }, 'Another request\u2026'));
  const box = h('div', { id: 'obsEditor' }, field('Do', choose));
  if (info && info.fields) {
    for (const [key, label, example] of info.fields) {
      box.append(field(label, textInput((action.data || {})[key] || '', (v) => edit(() => {
        action.data = action.data || {};
        if (v) action.data[key] = v; else delete action.data[key];
        if (!Object.keys(action.data).length) delete action.data;
      }), { placeholder: example, id: 'obsField' })));
    }
  } else if (!info) {
    box.append(field('Request name', textInput(action.request || '', (v) => edit(() => { action.request = v; }), { placeholder: 'GetVersion', id: 'obsRequestName' }),
      'Any obs-websocket 5 request, written as in its documentation.'),
    field('Request data (JSON, optional)', jsonBox(action.data, (v) => edit(() => { if (Array.isArray(v) || !Object.keys(v).length) delete action.data; else action.data = v; }), 3)));
  }
  box.append(h('div', { class: 'hint' }, 'Set up in Settings > OBS.'));
  return box;
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
  return box;
}

function folderEditor(k) {
  const d = desk();
  const others = d.pages.map((p, i) => ({ p, i })).filter(({ i }) => i !== state.sel.p);
  const select = h('select', { onchange: (e) => {
    const v = e.target.value;
    if (v === '__new__') {
      if (d.pages.length >= M.LIMITS.pages) { toast('Page limit reached.'); renderInspector(); return; }
      commit(() => { const i = M.addPage(desk(), k.label || 'Folder'); k.folder = desk().pages[i].id; });
    } else commit(() => { k.folder = v; });
  } },
  others.map(({ p }) => h('option', { value: p.id, selected: p.id === k.folder }, M.pageName(d, p))),
  h('option', { value: '__new__' }, '+ New page\u2026'));
  const target = d.pages.findIndex((p) => p.id === k.folder);
  return h('div', {}, field('Page', select),
    target >= 0 ? h('button', { class: 'small', onclick: () => { state.sel = { d: state.sel.d, p: target, k: null }; renderAll(); } }, 'Open page') : h('div', { class: 'hint' }, 'Page missing. Pick another.'));
}

const iconName = (n) => (n.charAt(0).toUpperCase() + n.slice(1)).replace(/_/g, ' '); // "volume_down" -> "Volume down"

function iconPicker(k) {
  const names = Object.keys(state.icons).sort();
  const grid = h('div', { class: 'iconGrid' }, h('button', { class: k.icon ? '' : 'on', title: 'No icon', onclick: () => commit(() => { delete page().keys[state.sel.k].icon; }) }, '\u2205'),
    names.map((n) => h('button', { class: 'fa' + (k.icon === n ? ' on' : ''), title: iconName(n), dataset: { icon: n }, onclick: () => commit(() => { page().keys[state.sel.k].icon = n; }) }, glyph(n))));
  return field('Icon', grid);
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
  if (kind === 'folder' && desk().pages.length < 2 && desk().pages.length >= M.LIMITS.pages) { toast('Page limit reached.'); return; }
  commit(() => {
    const key = page().keys[i] || {};
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
    field('Name', boardText(d.name, (v) => liveEdit(() => { desk().name = v; }, 'dname'), { maxLength: 24 })),
    field('Switch here for these apps', appsField('deskApps', d.apps || [], 10, 'e.g. obs64', (apps) => commit(() => {
      if (apps.length) desk().apps = apps; else delete desk().apps;
    })),
    'Names from Task Manager > Details, without .exe.')));
  if (kind !== 'keys') { box.replaceChildren(...cards); return; }
  const k = keyObj();
  if (state.sel.k === null) {
    cards.push(h('div', { class: 'card' }, h('div', { class: 'hint' }, 'Click a key to edit it.')));
    box.replaceChildren(...cards);
    return;
  }
  const kk = M.keyKind(k);
  const seg = h('div', { class: 'seg' }, [['action', 'Action'], ['folder', 'Folder']].map(([v, t]) =>
    h('button', { class: kk === v ? 'on' : '', onclick: () => { if (kk !== v) setKeyKind(v); } }, t)));
  const keyCard = h('div', { class: 'card' }, h('h4', {}, `Key ${state.sel.k + 1}`), seg);
  if (kk !== 'empty') {
    keyCard.append(field('Label', boardText(k.label || '', (v) => liveEdit(() => { const key = page().keys[state.sel.k]; if (v) key.label = v; else delete key.label; }, 'label'), { maxLength: 10 })),
      iconPicker(k), colorPicker(k));
    if (kk === 'folder') keyCard.append(folderEditor(k));
    else {
      keyCard.append(actionEditor(k.action || null, (na) => commit(() => { const key = page().keys[state.sel.k]; if (na) key.action = na; else delete key.action; })),
        holdSection(k), stateSection(k));
    }
  } else {
    keyCard.append(h('div', { class: 'hint' }, 'Pick Action, Folder or a preset.'));
  }
  keyCard.append(h('div', { class: 'row', style: { marginTop: '10px' } },
    h('button', { class: 'small', disabled: kk === 'empty', onclick: copyKey, title: 'Copy (Ctrl+C)' }, 'Copy'),
    h('button', { class: 'small', disabled: !state.clip, onclick: pasteKey, title: 'Paste (Ctrl+V)' }, 'Paste'),
    h('button', { class: 'small danger', disabled: kk === 'empty', onclick: clearKey, title: 'Clear (Delete)' }, 'Clear'),
    kk === 'action' ? h('button', { class: 'small', onclick: saveAsPreset }, 'Save as preset') : null));
  cards.push(keyCard);
  box.replaceChildren(...cards);
}

// ---- a key held down, and a key that shows a state of the PC ------------------------------------------------

async function loadRunningApps() {
  if (state.runningApps !== null) return;
  state.runningApps = [];
  try {
    state.runningApps = (await api('GET', '/api/running-apps')).apps || [];
  } catch (e) {
    state.runningApps = [];
  }
  const list = $('#runningApps');
  if (list) list.replaceChildren(...state.runningApps.map((a) => h('option', { value: a })));
}

function holdSection(k) {
  const mode = M.holdMode(k);
  const select = h('select', { id: 'holdMode', 'aria-label': 'When the key is held', onchange: (e) => commit(() => M.setHoldMode(page().keys[state.sel.k], e.target.value)) },
    M.HOLD_MODES.map(([v, t]) => h('option', { value: v, selected: mode === v }, t)));
  const box = h('div', { class: 'subsec', id: 'holdSection' }, h('h5', {}, 'Hold (\u00BD s)'), h('div', { class: 'field' }, select));
  if (mode === 'action') {
    box.append(actionEditor(k.hold, (na) => commit(() => { const key = page().keys[state.sel.k]; if (na) key.hold = na; else delete key.hold; })));
  }
  return box;
}

function stateSection(k) {
  const kind = M.stateKind(k);
  const key = () => page().keys[state.sel.k];
  const select = h('select', { id: 'stateKind', 'aria-label': 'Change the look when', onchange: (e) => commit(() => M.setStateKind(key(), e.target.value)) },
    M.STATE_SOURCES.map(([v, t]) => h('option', { value: v, selected: kind === v }, t)));
  const box = h('div', { class: 'subsec', id: 'stateSection' }, h('h5', {}, 'Change look when'),
    h('div', { class: 'field' }, select, kind === 'none' ? h('div', { class: 'hint' }, 'For example a mute key that turns red while the sound is muted.') : null));
  if (kind === 'none') return box;
  if (kind === 'app') {
    box.append(field('App', textInput(M.stateApp(k), (v) => liveEdit(() => { key().state.source = `app:${M.normalizeApp(v) || 'obs64'}`; }, 'stateapp'),
      { placeholder: 'obs64', list: 'runningApps', id: 'stateApp', onfocus: loadRunningApps }), 'Name from Task Manager > Details, without .exe.'));
  }
  const look = M.stateLook(k);
  const icons = Object.keys(state.icons).sort();
  box.append(field('Look when on', h('div', { class: 'row tight' },
    h('input', { type: 'color', id: 'stateColor', value: look.color.toLowerCase(), title: 'Color while on',
      style: { width: '34px', height: '28px', padding: 0, border: 0, background: 'none' },
      oninput: (e) => liveEdit(() => { key().state.color = e.target.value.toUpperCase(); }, 'statecolor') }),
    boardText(k.state.label !== undefined ? k.state.label : '', (v) => liveEdit(() => { if (v) key().state.label = v; else delete key().state.label; }, 'statelabel'),
      { maxLength: 10, placeholder: 'Same label', style: { flex: '1', minWidth: '90px' } }),
    h('select', { id: 'stateIcon', onchange: (e) => commit(() => { if (e.target.value === '') delete key().state.icon; else key().state.icon = e.target.value; }) },
      h('option', { value: '', selected: k.state.icon === undefined }, 'Same icon'),
      icons.map((n) => h('option', { value: n, selected: k.state.icon === n }, iconName(n)))))));
  box.append(checkRow('Preview', !!state.previewOn, (v) => { state.previewOn = v; renderScreen(); }));
  return box;
}

function copyKey() { const k = keyObj(); if (k && !M.isEmptyKey(k)) { state.clip = M.clone(k); toast('Key copied'); renderInspector(); } }
function pasteKey() {
  if (!state.clip || state.sel.k === null) return;
  const clip = M.clone(state.clip);
  if (clip.folder && !desk().pages.some((p) => p.id === clip.folder)) { toast('Can\u2019t paste: that folder\u2019s page isn\u2019t on this desktop.'); return; }
  commit(() => { page().keys[state.sel.k] = clip; });
}
function clearKey() { if (state.sel.k !== null) commit(() => { page().keys[state.sel.k] = {}; }); }

// ---- live screen settings ----------------------------------------------------------------------------

const checkRow = (label, checked, onchange) => h('label', { class: 'check' }, h('input', { type: 'checkbox', checked, onchange: (e) => onchange(e.target.checked) }), label);
const radioRow = (group, label, value, current, onPick) => h('label', { class: 'check' },
  h('input', { type: 'radio', name: group, checked: current === value, onchange: () => onPick(value) }), h('span', {}, label));

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
  } catch (e) {
    state.sources = null;
    state.sourcesError = e.message;
  }
  state.sourcesBusy = false;
  renderLiveConfig();
}

// ---- Now playing: which players are shown ------------------------------------------------------------

// The icon of an app in this configurator: its logo (Font Awesome brand glyph), else a colored letter badge, and a grey
// placeholder for names that are not in the catalog (custom text).
function appIcon(app, size = 30) {
  const box = h('span', { class: 'aico', style: { width: size + 'px', height: size + 'px', fontSize: Math.round(size * 0.62) + 'px' } });
  if (app && app.glyph) {
    box.classList.add('brand');
    box.append(h('span', { class: 'fa', style: { color: app.color } }, String.fromCodePoint(parseInt(app.glyph, 16))));
  } else if (app && app.letter) {
    box.classList.add('badge');
    box.style.background = app.color;
    box.append(app.letter);
  } else {
    box.classList.add('placeholder');
    box.append(h('span', { class: 'fa' }, String.fromCodePoint(0xf2d0)));
  }
  return box;
}

const DRAG_APP = 'application/x-sd-app';

function appRow({ app, title, sub, tag, drag, buttons }) {
  const row = h('div', { class: 'arow' }, appIcon(app),
    h('div', { class: 'ainfo' }, h('div', { class: 'an' }, title, tag ? h('span', { class: 'tag' }, tag) : null), h('div', { class: 'as' }, sub)),
    h('div', { class: 'abtns' }, buttons));
  if (drag) {
    row.draggable = true;
    row.addEventListener('dragstart', (e) => { e.dataTransfer.setData(DRAG_APP, JSON.stringify(drag)); e.dataTransfer.effectAllowed = 'copyMove'; });
  }
  return row;
}

// What the library can offer: players running right now, then the catalog by category, and what was typed as a custom name.
function libraryItems() {
  const raw = state.appSearch.trim();
  const q = raw.toLowerCase();
  const items = [];
  const running = new Set();
  for (const src of state.sources || []) {
    const app = M.catalogApp(state.catalog, src.id);
    if (app) running.add(app.id);
    items.push({ section: 'Running now', app, name: app ? app.name : src.id, names: app ? app.patterns : [src.id], tag: src.playing ? 'playing' : null,
      sub: [src.title, src.artist].filter(Boolean).join(' \u2014 ') || (app ? src.id : 'running now') });
  }
  for (const cat of state.catalog.categories) {
    for (const app of state.catalog.apps.filter((a) => a.category === cat.id && !running.has(a.id))) {
      items.push({ section: cat.name, app, name: app.name, names: app.patterns, sub: '' });
    }
  }
  const shown = items.filter((it) => !q || it.name.toLowerCase().includes(q) || it.sub.toLowerCase().includes(q) || it.names.some((n) => n.toLowerCase().includes(q)));
  if (raw && raw.length <= 80 && !items.some((it) => it.name.toLowerCase() === q || it.names.some((n) => n.toLowerCase() === q))) {
    shown.unshift({ section: 'Custom name', app: null, name: `\u201C${raw}\u201D`, names: [raw], sub: 'Add as typed' });
  }
  return shown;
}

function fillLibrary(listEl, f, setFilter) {
  const toggle = (item, which, label) => {
    const on = M.inFilterList(f, which, item.names);
    return h('button', { class: 'small tog' + (on ? ' on' : ''), dataset: { list: which }, title: on ? 'Remove' : '',
      onclick: () => setFilter((x) => { if (on) M.removeFilterApps(x, which, item.names); else M.addFilterApps(x, item.names, which); }) }, (on ? '\u2713 ' : '') + label);
  };
  const rows = [];
  let section = null;
  for (const item of libraryItems()) {
    if (item.section !== section) { section = item.section; rows.push(h('div', { class: 'libsec' }, section)); }
    rows.push(appRow({ app: item.app, title: item.name, sub: item.sub, tag: item.tag, drag: { names: item.names }, buttons: [toggle(item, 'allow', 'Only this'), toggle(item, 'block', 'Block')] }));
  }
  if (!rows.length) rows.push(h('div', { class: 'hint' }, 'No players match.'));
  listEl.replaceChildren(...rows);
}

function listCard(which, title, empty, f, setFilter) {
  const entries = M.filterEntries(f[which], state.catalog);
  const card = h('div', { class: 'card applist', dataset: { list: which } }, h('h4', {}, title, h('span', { class: 'count' }, entries.length ? String(entries.length) : '')),
    entries.length ? entries.map((e) => appRow({ app: e.app, title: e.app ? e.app.name : e.names[0], sub: e.app ? '' : 'Custom', drag: { names: e.names },
      buttons: [h('button', { class: 'small x', title: 'Remove', onclick: () => setFilter((x) => { M.removeFilterApps(x, which, e.names); }) }, '\u2715')] }))
      : h('div', { class: 'hint emptylist' }, empty));
  card.addEventListener('dragover', (e) => { if ([...e.dataTransfer.types].includes(DRAG_APP)) { e.preventDefault(); card.classList.add('dragover'); } });
  card.addEventListener('dragleave', (e) => { if (!card.contains(e.relatedTarget)) card.classList.remove('dragover'); });
  card.addEventListener('drop', (e) => {
    card.classList.remove('dragover');
    const raw = e.dataTransfer.getData(DRAG_APP);
    if (!raw) return;
    e.preventDefault();
    const { names } = JSON.parse(raw);
    setFilter((x) => { M.addFilterApps(x, names, which); }); // adding to one list takes it off the other
  });
  return card;
}

function nowPlayingConfig(d) {
  const o = M.liveOptions(d);
  const f = o.filter;
  const setFilter = (fn) => commit(() => { const cur = M.liveOptions(d).filter; fn(cur); M.setOptions(d, { filter: cur }); });
  const setOpt = (patch) => commit(() => { M.setOptions(d, patch); });
  if (!state.sourcesTried) { state.sourcesTried = true; setTimeout(detectPlayers, 0); } // look at what is running, once

  const libList = h('div', { class: 'liblist' });
  const search = h('input', { type: 'text', id: 'appSearch', placeholder: 'Search or add a name', value: state.appSearch, maxLength: 80,
    oninput: (e) => { state.appSearch = e.target.value; fillLibrary(libList, f, setFilter); } });
  fillLibrary(libList, f, setFilter);
  const libraryCard = h('div', { class: 'card library' },
    h('div', { class: 'libhead' }, h('h4', {}, 'Players'), h('button', { class: 'small', disabled: state.sourcesBusy, onclick: detectPlayers }, state.sourcesBusy ? 'Looking\u2026' : 'Refresh')),
    h('div', { class: 'hint' }, 'A name matches any player containing it, ignoring case.'),
    search,
    state.sourcesError ? h('div', { class: 'hint bad' }, `Can\u2019t list players: ${state.sourcesError}. Try Refresh.`) : null,
    libList);

  const look = h('div', { class: 'card' }, h('h4', {}, 'Look'),
    checkRow('Background from cover colors', o.tint, (v) => setOpt({ tint: v })),
    checkRow('Album', o.album, (v) => setOpt({ album: v })),
    checkRow('App icon', o.appIcon, (v) => setOpt({ appIcon: v })),
    h('div', { class: 'lab', style: { marginTop: '8px' } }, 'Controls'),
    radioRow('npctl', 'Full', 'full', o.controls, (v) => setOpt({ controls: v })),
    radioRow('npctl', 'Basic', 'basic', o.controls, (v) => setOpt({ controls: v })));

  return [h('div', { class: 'npgrid' }, libraryCard,
    h('div', { class: 'npright' },
      listCard('allow', 'Only these', 'Any player', f, setFilter),
      listCard('block', 'Blocked', 'None', f, setFilter),
      look))];
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
      h('b', {}, s.name))));
  const rows = [];
  enabled.forEach((id, i) => {
    const row = h('div', { class: 'srow', dataset: { item: id } }, h('span', { class: 'grip', title: 'Drag to reorder' }, '\u22EE\u22EE'),
      h('input', { type: 'checkbox', checked: true, disabled: enabled.length <= 1, title: enabled.length <= 1 ? 'At least one item must stay' : 'Hide', onchange: (e) => toggle(id, e.target.checked) }),
      h('span', { class: 'nm' }, info(id).name), h('span', { class: 'spacer' }),
      h('button', { class: 'small', disabled: i === 0, title: 'Move up', onclick: () => move(i, i - 1) }, '\u2191'),
      h('button', { class: 'small', disabled: i === enabled.length - 1, title: 'Move down', onclick: () => move(i, i + 1) }, '\u2193'));
    sortableRow(row, i, 'application/x-sd-statitem', move);
    rows.push(row);
  });
  for (const id of disabled) {
    rows.push(h('div', { class: 'srow off', dataset: { item: id } }, h('span', { class: 'grip' }, ''),
      h('input', { type: 'checkbox', checked: false, title: 'Show', onchange: (e) => toggle(id, e.target.checked) }),
      h('span', { class: 'nm' }, info(id).name)));
  }
  const items = h('div', { class: 'card' }, h('h4', {}, 'What to show'), rows,
    h('div', { class: 'hint' }, 'GPU and VRAM need an NVIDIA card. CPU temperature isn\u2019t shown.'));
  return [style, items];
}

function mixerConfig(d) {
  const o = M.liveOptions(d);
  const apps = appsField('mixerApps', o.apps, 4, 'e.g. spotify', (list) => commit(() => {
    if (list.length) M.setOptions(d, { apps: list }); else if (d.options) delete d.options.apps;
  }));
  return [h('div', { class: 'card' }, h('h4', {}, 'Apps'),
    field('Show first', apps, 'Up to 4. An app appears once it has played sound.'))];
}

function clockConfig(d) {
  const o = M.liveOptions(d);
  const setOpt = (patch) => commit(() => { M.setOptions(d, patch); });
  const parts = h('div', { class: 'card' }, h('h4', {}, 'Show'),
    checkRow('12-hour', o.hour12, (v) => setOpt({ hour12: v })),
    checkRow('Seconds', o.seconds, (v) => setOpt({ seconds: v })),
    checkRow('Timer', o.timer, (v) => setOpt({ timer: v })),
    checkRow('Weather', o.weather, (v) => commit(() => { M.setOptions(d, { weather: v }); })),
    h('div', { class: 'hint' }, 'The timer keeps running on other desktops. It rings even with key sounds off.'));
  const results = h('div', { class: 'placeResults', id: 'placeResults' });
  const message = h('div', { class: 'hint', id: 'placeMessage' });
  const query = h('input', { type: 'text', id: 'placeQuery', placeholder: 'e.g. Oslo', maxLength: 80, style: { flex: '1' } });
  const search = async () => {
    message.className = 'hint';
    message.textContent = 'Searching\u2026';
    results.replaceChildren();
    try {
      const found = (await api('GET', '/api/places?q=' + encodeURIComponent(query.value.trim()))).places;
      message.textContent = found.length ? '' : 'No match. Try a nearby city.';
      results.replaceChildren(...found.map((p) => h('button', { class: 'small', dataset: { place: p.name }, onclick: () => setOpt({ place: { name: p.name, lat: p.lat, lon: p.lon } }) }, M.foundPlaceText(p))));
    } catch (e) {
      message.className = 'hint bad';
      message.textContent = e.message;
    }
  };
  query.addEventListener('keydown', (e) => { if (e.key === 'Enter') search(); });
  const weather = h('div', { class: 'card' }, h('h4', {}, 'Weather'),
    o.place ? h('div', { class: 'place', id: 'chosenPlace' }, h('span', { class: 'nm' }, M.placeText(o.place)), h('button', { class: 'small', onclick: () => commit(() => { delete d.options.place; }) }, 'Remove'))
      : h('div', { class: 'hint', id: 'chosenPlace' }, 'Pick a city to show weather.'),
    field('City', h('div', { class: 'row' }, query, h('button', { class: 'small', id: 'placeSearch', onclick: search }, 'Search'))),
    message, results,
    field('Units', h('select', { id: 'weatherUnits', onchange: (e) => setOpt({ units: e.target.value }) },
      [['c', 'Celsius (\u00B0C)'], ['f', 'Fahrenheit (\u00B0F)']].map(([v, t]) => h('option', { value: v, selected: o.units === v }, t)))),
    h('div', { class: 'hint', id: 'weatherPrivacy' }, 'Weather comes from Open-Meteo. It receives what you search for here, and the city\u2019s coordinates about every 15 minutes while the clock is on the board\u2019s screen. To turn weather off, untick Weather above.'));
  return o.weather ? [parts, weather] : [parts];
}

function renderLiveConfig() {
  const box = $('#liveConfig');
  if (isKeys()) { box.replaceChildren(); return; }
  const d = desk();
  const configs = { nowplaying: nowPlayingConfig, stats: statsConfig, mixer: mixerConfig, clock: clockConfig };
  box.replaceChildren(...configs[M.desktopKind(d)](d));
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
  if (nk.folder === 'new' && desk().pages.length >= M.LIMITS.pages) { toast('Page limit reached.'); return; }
  const old = page().keys[slot];
  commit(() => {
    if (nk.folder === 'new') nk.folder = desk().pages[M.addPage(desk(), nk.label || 'Folder')].id;
    page().keys[slot] = nk;
    state.sel.k = slot;
  });
  if (old && !M.isEmptyKey(old) && JSON.stringify(old) !== JSON.stringify(nk)) {
    toast(old.label ? `Replaced "${old.label}"` : 'Key replaced', { undo, deck: true });
  }
}

function applyPreset(p) {
  if (!isKeys()) { toast('Select a keys desktop first'); return; }
  let slot = state.sel.k;
  if (slot === null) {
    slot = M.firstEmptySlot(page());
    if (slot === null) { toast(page().rows < M.LIMITS.rows ? 'Page full. Select a key to replace, or add a row.' : 'Page full. Select a key to replace.'); return; }
  }
  putPreset(slot, p.key);
}

function fillWithCategory(cat) {
  if (!isKeys()) return;
  const items = flatPresets().filter((p) => p.cat === cat && !M.isEmptyKey(p.key) && p.key.folder !== 'new');
  const free = page().keys.map((k, i) => (M.isEmptyKey(k) ? i : -1)).filter((i) => i >= 0);
  const n = Math.min(items.length, free.length);
  if (!n) { toast('Page full.'); return; }
  commit(() => { for (let j = 0; j < n; j++) page().keys[free[j]] = M.clone(items[j].key); });
  toast(n < items.length ? `Added ${n} of ${items.length}. Page full.` : `Added ${n}.`);
}

async function saveUserPresets(list) {
  try {
    await api('PUT', '/api/presets/user', { presets: list });
    state.presets.user = list;
    renderPresets();
    return true;
  } catch (e) {
    banner('Couldn\u2019t save presets: ' + e.message);
    return false;
  }
}

// Presets are saved as soon as they change (they are not part of the deck's undo history), so deleting one has its own undo.
async function deletePreset(index) {
  const gone = state.presets.user[index];
  if (!(await saveUserPresets(state.presets.user.filter((_, i) => i !== index)))) return;
  toast(`Deleted preset "${gone.name}"`, { undo: () => {
    const list = [...state.presets.user];
    list.splice(index, 0, gone);
    saveUserPresets(list);
  } });
}

function saveAsPreset() {
  const k = keyObj();
  if (!k || M.keyKind(k) !== 'action') return;
  const name = prompt('Name for this preset:', k.label || 'My key');
  if (!name || !name.trim()) return;
  const key = M.clone(k);
  saveUserPresets([...state.presets.user, { name: name.trim().slice(0, 40), key }]).then((ok) => ok && toast('Added to My presets'));
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
  for (const c of state.presets.builtin) cats.push({ id: c.id, name: c.name, note: c.note });
  const q = state.query.trim().toLowerCase();
  const shown = all.filter((p) => (state.cat === 'all' || p.cat === state.cat) &&
    (!q || p.name.toLowerCase().includes(q) || (p.key.label || '').toLowerCase().includes(q) || M.describeAction(p.key.action).toLowerCase().includes(q)));
  const grid = h('div', { class: 'pgrid' });
  for (const p of shown) {
    const card = h('div', { class: 'pcard', draggable: true, title: p.key.folder ? 'A folder with a new page' : M.describeAction(p.key.action), onclick: () => applyPreset(p) },
      miniKey(p.key), h('div', { class: 'nm' }, p.name),
      p.user ? h('button', { class: 'small danger del', title: 'Delete this preset', onclick: (e) => { e.stopPropagation(); deletePreset(p.idx); } }, '\u2715') : null);
    card.addEventListener('dragstart', (e) => { e.dataTransfer.setData('application/x-sd-preset', String(all.indexOf(p))); e.dataTransfer.effectAllowed = 'copy'; });
    grid.append(card);
  }
  if (!shown.length) grid.append(h('div', { class: 'hint' }, 'No presets match.'));
  const canFill = isKeys() && state.cat !== 'all' && state.cat !== 'layout';
  $('#presets').replaceChildren(
    h('h3', {}, 'Presets'),
    h('div', { class: 'phead' },
      h('input', { type: 'text', placeholder: 'Search presets\u2026', value: state.query, oninput: (e) => { state.query = e.target.value; renderPresetGrid(grid, e.target); } }),
      canFill ? h('button', { class: 'small', onclick: () => fillWithCategory(state.cat) }, 'Add all') : null,
      h('span', { class: 'hint' }, 'Click or drag to add.')),
    h('div', { class: 'chips' }, cats.map((c) => h('button', { class: 'chip' + (state.cat === c.id ? ' on' : ''), title: c.note || '', onclick: () => { state.cat = c.id; renderPresets(); } }, c.name))),
    grid);
}

// Typing in the search box must not rebuild the box itself (it would lose focus): rebuild the grid only.
function renderPresetGrid() { renderPresets(); const input = $('#presets input'); input.focus(); input.setSelectionRange(input.value.length, input.value.length); }

// ---- rendering ---------------------------------------------------------------------------------------

function setView(view) {
  state.view = view;
  renderAll();
}

// ---- general settings page ---------------------------------------------------------------------------

const DIM_CHOICES = [[0, 'Never'], [15, '15 seconds'], [30, '30 seconds'], [60, '1 minute'], [120, '2 minutes'], [300, '5 minutes']];
const SLEEP_CHOICES = [[0, 'Never'], [60, '1 minute'], [120, '2 minutes'], [300, '5 minutes'], [600, '10 minutes'], [1800, '30 minutes']];

function renderSettingsPage() {
  const s = M.resolveSettings(state.deck);
  const setting = (name, value, rerender = 'live') => {
    commit((d) => { d.settings = d.settings || {}; d.settings[name] = value; }, { coalesce: 'setting-' + name, rerender });
    previewSettings();
  };
  // `show` only formats the number next to the label: the value stored is always the slider's own.
  const slider = (id, label, min, max, value, name, show = (v) => v) => {
    const out = h('b', {}, show(value));
    return h('div', { class: 'field' }, h('label', {}, label + ' ', out),
      h('input', { type: 'range', id, min, max, value, oninput: (e) => { out.textContent = show(+e.target.value); setting(name, +e.target.value, 'sample'); } }));
  };
  const choice = (id, label, options, value, name, { hint, off = () => false, pick = (v) => setting(name, v, 'all') } = {}) => h('div', { class: 'field' }, h('label', {}, label),
    h('select', { id, onchange: (e) => pick(+e.target.value) },
      options.map(([v, t]) => h('option', { value: v, selected: v === value, disabled: v !== value && off(v) }, t))),
    hint ? h('div', { class: 'hint' }, hint) : null);
  // Dimming only means something before the screen turns off: such times cannot be picked, and choosing a screen-off time
  // that comes first sets dimming to Never (what the board would do anyway).
  const dimsTooLate = (dim, sleep) => !!(dim && sleep && dim >= sleep);
  const pickSleep = (sleep) => {
    const clash = dimsTooLate(s.dimAfter, sleep);
    commit((d) => { d.settings = d.settings || {}; d.settings.sleepAfter = sleep; if (clash) d.settings.dimAfter = 0; });
    previewSettings();
    if (clash) toast('Dimming turned off: the screen turns off first', { undo, deck: true });
  };
  const st = state.status;
  const info = (label, value) => h('div', { class: 'inforow' }, h('span', { class: 'lab' }, label), h('span', {}, value));
  const desktops = state.deck.desktops.map((d, i) => [i, `${i + 1}. ${d.name || 'Untitled'}`]);

  // a small sample of the key look (same math as the board); the look sliders redraw only this
  const sample = h('div', { class: 'lookSample', id: 'lookSample', style: { width: M.SCREEN.w * 0.75 + 'px', height: (M.SCREEN.h - M.SCREEN.header) * 0.75 + 'px' } });
  const drawSample = () => {
    const look = M.resolveSettings(state.deck);
    const geo = M.geometry(4, 3, undefined, undefined, look.keyGap);
    const colors = ['#2A3A4D', '#1F6FEB', '#2EA043', '#8957E5', '#D29922', '#DA3633'];
    sample.replaceChildren(...Array.from({ length: 12 }, (_, i) => {
      const { x, y } = geo.pos(i);
      return h('div', { class: 'sampleKey', style: { left: x * 0.75 + 'px', top: y * 0.75 + 'px', width: geo.keyW * 0.75 + 'px',
        height: geo.keyH * 0.75 + 'px', borderRadius: look.keyRadius * 0.75 + 'px', background: colors[i % colors.length] } });
    }));
  };
  state.drawSample = drawSample;
  drawSample();

  $('#settingsPage').replaceChildren(h('div', { class: 'settingsWrap' },
    h('h2', {}, 'Settings'),
    h('p', { class: 'hint', id: 'previewNote' }, previewNoteText()),
    h('div', { class: 'cards' },
      h('div', { class: 'card' }, h('h4', {}, 'Sound'),
        checkRow('Key sounds', s.sounds, (v) => setting('sounds', v, 'all')),
        checkRow('Folder and Back sounds', s.navSounds, (v) => setting('navSounds', v, 'all')),
        h('div', { class: 'field' }, h('label', {}, 'Style'),
          h('div', { class: 'seg', id: 'themeSeg' }, M.SOUND_THEMES.map(([v, t]) =>
            h('button', { class: s.soundTheme === v ? 'on' : '', dataset: { theme: v }, onclick: () => setting('soundTheme', v, 'all') }, t)))),
        h('div', { class: 'field' }, h('label', {}, 'Play on board'),
          h('div', { class: 'row tight', id: 'testRow' },
            [['tap', 'Key'], ['nav', 'Folder'], ['back', 'Back'], ['error', 'Error']].map(([n, t]) =>
              h('button', { class: 'small', dataset: { sound: n }, disabled: !s.sounds, onclick: () => testSound(n) }, t)))),
        slider('setVolume', 'Volume', 0, 100, s.volume, 'volume')),
      h('div', { class: 'card' }, h('h4', {}, 'Screen'),
        slider('setBrightness', 'Brightness', 5, 255, s.brightness, 'brightness', (v) => `${Math.round(v / 2.55)}%`),
        choice('setDim', 'Dim when idle', DIM_CHOICES, s.dimAfter, 'dimAfter', { off: (v) => dimsTooLate(v, s.sleepAfter) }),
        choice('setSleep', 'Screen off when idle', SLEEP_CHOICES, s.sleepAfter, 'sleepAfter', { hint: 'First touch only wakes it.', pick: pickSleep }),
        dimsTooLate(s.dimAfter, s.sleepAfter) ? h('div', { class: 'hint bad', id: 'dimClash' }, 'Never dims: the screen turns off first.') : null, // (a hand-edited deck)
        checkRow('Rotate screen 180\u00B0', s.flip, (v) => setting('flip', v, 'all')),
        checkRow('Screen off when PC locks or sleeps', s.sleepWithPc, (v) => setting('sleepWithPc', v, 'all')),
        h('div', { class: 'hint' }, 'Touch lights it for 30 s.')),
      h('div', { class: 'card' }, h('h4', {}, 'Desktops'),
        h('div', { class: 'field' }, h('label', {}, 'Swipe distance'),
          h('select', { id: 'setSwipe', onchange: (e) => setting('swipe', e.target.value, 'all') },
            M.SWIPE_LENGTHS.map(([v, t]) => h('option', { value: v, selected: v === s.swipe }, t)))),
        checkRow('Loop desktops', s.wrap, (v) => setting('wrap', v, 'all')),
        choice('setStart', 'Start on', desktops, Math.min(s.startDesktop, desktops.length - 1), 'startDesktop',
          { hint: 'The board also returns here when a save changes what it shows.' })),
      h('div', { class: 'card' }, h('h4', {}, 'Keys'),
        sample,
        slider('setRadius', 'Corners', 0, 24, s.keyRadius, 'keyRadius', (v) => `${v} px`),
        slider('setGap', 'Spacing', 2, 12, s.keyGap, 'keyGap', (v) => `${v} px`)),
      obsCard(),
      h('div', { class: 'card' }, h('h4', {}, 'This deck'),
        info('Board', st ? (st.connected ? `connected on ${st.port || '?'}, firmware ${st.firmware || '?'}` : 'disconnected') : 'can\u2019t reach Swipedeck'),
        h('div', { class: 'hint', style: { marginTop: '8px' } }, 'Previous save: deck.json.bak'))),
  ));
}

function obsCard() {
  const obs = state.deck.obs || {};
  const set = (name, value) => commit((d) => {
    d.obs = d.obs || {};
    if (value === '') delete d.obs[name]; else d.obs[name] = value;
    if (!Object.keys(d.obs).length) delete d.obs;
  }, { coalesce: 'obs-' + name, rerender: 'live' });
  const result = h('span', { class: 'hint', id: 'obsResult' });
  const test = async () => {
    result.className = 'hint';
    result.textContent = 'Connecting\u2026';
    try {
      const r = await api('POST', '/api/obs/test', { obs: state.deck.obs || {} });
      result.className = 'hint' + (r.ok ? ' good' : ' bad');
      result.textContent = r.ok ? `Connected to OBS ${r.obs || ''}`.trim() : r.error;
    } catch (e) {
      result.className = 'hint bad';
      result.textContent = e.message;
    }
  };
  return h('div', { class: 'card', id: 'obsCard' }, h('h4', {}, 'OBS'),
    h('div', { class: 'hint' }, 'In OBS: Tools > WebSocket Server Settings > Enable WebSocket server.'),
    field('Address', textInput(obs.host || '', (v) => set('host', v.trim()), { placeholder: '127.0.0.1', id: 'obsHost' })),
    field('Port', h('input', { type: 'number', id: 'obsPort', min: 1, max: 65535, placeholder: '4455', value: obs.port ?? '', style: { width: '110px' },
      oninput: (e) => { const n = Math.round(+e.target.value); if (e.target.value === '') set('port', ''); else if (n >= 1 && n <= 65535) set('port', n); } })),
    field('Password', textInput(obs.password || '', (v) => set('password', v), { placeholder: 'none, or ${OBS_PASSWORD}', id: 'obsPassword' }),
      'Saved as plain text. To avoid that, write ${OBS_PASSWORD} and set that variable on this PC.'),
    h('div', { class: 'row' }, h('button', { class: 'small', id: 'obsTest', onclick: test }, 'Test connection'), result));
}

function renderView() {
  const settings = state.view === 'settings';
  $('#deckView').hidden = settings;
  $('#settingsPage').hidden = !settings;
  if (settings) renderSettingsPage();
}

function renderAll() {
  renderTopbar();
  renderView();
  renderTree();
  renderScreen();
  renderPageControls();
  renderInspector();
  renderPresets();
  renderLiveConfig();
}

function renderLive() { // after typing: leave the inspector, the settings and the presets alone
  renderTopbar();
  if (state.view !== 'deck') return;
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
  if (mod && e.key.toLowerCase() === 's') {
    e.preventDefault();
    if (e.target.classList && e.target.classList.contains('appadd')) e.target.blur(); // adds the name being typed
    save();
    return;
  }
  if (typing) return;
  if (state.view !== 'deck') { // the general settings page has no keys to edit, but undo / redo still apply
    if (mod && e.key.toLowerCase() === 'z') { e.preventDefault(); e.shiftKey ? redo() : undo(); }
    else if (mod && e.key.toLowerCase() === 'y') { e.preventDefault(); redo(); }
    return;
  }
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
  document.body.append(h('datalist', { id: 'keynames' }, names.map((n) => h('option', { value: n }))));
  try {
    const [icons, presets, , templates, catalog] = await Promise.all([fetch('/icons.json').then((r) => r.json()), api('GET', '/api/presets'), loadDeck(),
      fetch('/templates.json').then((r) => r.json()), fetch('/apps.json').then((r) => r.json())]);
    state.icons = icons;
    state.catalog = catalog;
    state.templates = templates.templates;
    state.presets = { builtin: presets.builtin, user: presets.user };
    renderAll();
  } catch (e) {
    banner(e.deck ? loadProblem(e) : `Couldn\u2019t open the editor: ${e.message}. Reload to try again.`);
    return;
  }
  document.addEventListener('keydown', onKeyDown);
  window.addEventListener('beforeunload', (e) => { if (isDirty()) { e.preventDefault(); e.returnValue = ''; } });
  refreshStatus();
  setInterval(refreshStatus, 3000);
}

start();
