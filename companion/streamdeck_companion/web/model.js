// Pure logic of the deck editor (no DOM): deck edits, grid geometry, hotkey handling, undo history.
// Used by app.js in the browser and by tests/js/model.test.mjs in Node.

export const LIMITS = { desktops: 8, pages: 16, cols: 6, rows: 5 };
export const SCREEN = { w: 320, h: 240, header: 30 };
export const DEFAULT_KEY_COLOR = '#2A3A4D';

export const clone = (v) => JSON.parse(JSON.stringify(v));

// ---- general settings -----------------------------------------------------------------------------------

// Mirrors SETTINGS_SPEC in deck.py. A setting that is missing from deck.json means its default.
export const SETTING_DEFAULTS = {
  volume: 70, sounds: true, soundTheme: 'classic', navSounds: true, brightness: 255, dimAfter: 0, sleepAfter: 0,
  flip: false, startDesktop: 0, keyRadius: 12, keyGap: 6, keyPad: 6, wrap: false, swipe: 'normal', sleepWithPc: false,
};
export const SOUND_THEMES = [['classic', 'Classic'], ['soft', 'Soft'], ['click', 'Click'], ['chirp', 'Chirp']];
export const SWIPE_LENGTHS = [['short', 'Short'], ['normal', 'Normal'], ['long', 'Long']];
export const resolveSettings = (deck) => ({ ...SETTING_DEFAULTS, ...((deck && deck.settings) || {}) });

// The settings of each card of the Settings page, for "Reset to defaults". The OBS connection is not a board setting: it
// is the deck's "obs" object, and its default is not having one.
export const SETTING_GROUPS = {
  sound: ['sounds', 'navSounds', 'soundTheme', 'volume'],
  screen: ['brightness', 'dimAfter', 'sleepAfter', 'flip', 'sleepWithPc'],
  desktops: ['swipe', 'wrap', 'startDesktop'],
  keys: ['keyRadius', 'keyGap', 'keyPad'],
  obs: [],
};

export function atDefaults(deck, groups) {
  const s = resolveSettings(deck);
  return groups.every((g) => (g === 'obs' ? !deck.obs : SETTING_GROUPS[g].every((name) => s[name] === SETTING_DEFAULTS[name])));
}

// A setting at its default is one that is not in the deck, so resetting removes it.
export function resetToDefaults(deck, groups) {
  for (const g of groups) {
    if (g === 'obs') delete deck.obs;
    else if (deck.settings) for (const name of SETTING_GROUPS[g]) delete deck.settings[name];
  }
  if (deck.settings && !Object.keys(deck.settings).length) delete deck.settings;
}

// ---- keys ------------------------------------------------------------------------------------------

export const isEmptyKey = (k) => !k || Object.keys(k).length === 0;
export const keyKind = (k) => (isEmptyKey(k) ? 'empty' : k.folder ? 'folder' : 'action');

// Same math as the firmware (buildPage in src/ui.cpp); the tile is 320 x 210. The keys fill what is inside the side
// padding to the pixel, the division's leftover spread over them. keyW / keyH are the smallest key (the board picks its
// fonts by it); pos(i) gives each key's own place and size.
export function geometry(cols, rows, w = SCREEN.w, h = SCREEN.h - SCREEN.header, gap = SETTING_DEFAULTS.keyGap, pad = SETTING_DEFAULTS.keyPad) {
  const spanW = w - 2 * pad + gap, spanH = h - 2 * pad + gap; // each column / row: a key and the gap after it
  const keyW = Math.floor(spanW / cols) - gap;
  const keyH = Math.floor(spanH / rows) - gap;
  const left = (c) => pad + Math.floor((c * spanW) / cols);
  const top = (r) => pad + Math.floor((r * spanH) / rows);
  return {
    keyW, keyH, pad, gap,
    iconPx: keyH >= 86 ? 32 : keyH >= 62 ? 24 : 20,
    textPx: keyH >= 86 ? 16 : 14,
    pos: (i) => {
      const c = i % cols, r = Math.floor(i / cols);
      return { x: left(c), y: top(r), w: left(c + 1) - gap - left(c), h: top(r + 1) - gap - top(r) };
    },
  };
}

export function padKeys(page) {
  const n = page.cols * page.rows;
  page.keys = (page.keys || []).slice(0, n);
  while (page.keys.length < n) page.keys.push({});
  return page;
}

// Changes the grid size keeping every key at its row/column. Returns how many non-empty keys no longer fit.
export function resizePage(page, cols, rows) {
  cols = Math.min(Math.max(cols | 0, 1), LIMITS.cols);
  rows = Math.min(Math.max(rows | 0, 1), LIMITS.rows);
  const old = padKeys({ ...page, keys: page.keys });
  const next = Array.from({ length: cols * rows }, () => ({}));
  let dropped = 0;
  old.keys.forEach((k, i) => {
    const r = Math.floor(i / page.cols), c = i % page.cols;
    if (r < rows && c < cols) next[r * cols + c] = k;
    else if (!isEmptyKey(k)) dropped++;
  });
  page.cols = cols;
  page.rows = rows;
  page.keys = next;
  return dropped;
}

// How many non-empty keys would be lost by resizing (for the confirmation).
export function resizeLoss(page, cols, rows) {
  return resizePage(clone(page), cols, rows);
}

export function swapKeys(page, a, b) {
  padKeys(page);
  [page.keys[a], page.keys[b]] = [page.keys[b], page.keys[a]];
}

export function firstEmptySlot(page) {
  padKeys(page);
  const i = page.keys.findIndex(isEmptyKey);
  return i < 0 ? null : i;
}

// ---- desktops and pages ----------------------------------------------------------------------------

export function uniqueId(existing, prefix) {
  for (let n = 1; ; n++) if (!existing.includes(prefix + n)) return prefix + n;
}

export function newKeysDesktop(deck, name) {
  const title = name || `Desktop ${deck.desktops.length + 1}`;
  return { name: title, pages: [padKeys({ id: 'root', title, cols: 4, rows: 3, keys: [] })] };
}

export const specialDesktop = (kind) => ({ name: { stats: 'PC', nowplaying: 'Playing', mixer: 'Mixer', clock: 'Clock' }[kind] || 'Screen', kind });
export const hasKind = (deck, kind) => deck.desktops.some((d) => d.kind === kind);
export const desktopKind = (d) => d.kind || 'keys';

export function addDesktop(deck, kind) {
  if (deck.desktops.length >= LIMITS.desktops) return null;
  if (kind !== 'keys' && hasKind(deck, kind)) return null;
  const d = kind === 'keys' ? newKeysDesktop(deck) : specialDesktop(kind);
  deck.desktops.push(d);
  return deck.desktops.length - 1;
}

export function removeDesktop(deck, i) {
  if (deck.desktops.length <= 1) return false;
  deck.desktops.splice(i, 1);
  return true;
}

export function moveInList(list, from, to) {
  if (to < 0 || to >= list.length || from === to) return false;
  list.splice(to, 0, list.splice(from, 1)[0]);
  return true;
}

// Drag and drop reordering: where does the dragged item `from` end up when it is dropped on the upper
// (`after` = false) or lower half of item `over`? The result is the index to pass to moveInList.
export function dropTarget(from, over, after) {
  let to = over + (after ? 1 : 0); // insertion point in the list that still contains `from`
  if (from < to) to -= 1;          // taking `from` out shifts everything behind it up by one
  return to;
}

export function uniqueName(names, name) {
  if (!names.includes(name)) return name;
  for (let n = 2; ; n++) if (!names.includes(`${name} ${n}`)) return `${name} ${n}`;
}

// Adds a copy of a ready-made desktop (from templates.json). Returns its index, or null at the desktop limit.
export function addTemplateDesktop(deck, desktop) {
  if (deck.desktops.length >= LIMITS.desktops) return null;
  const d = clone(desktop);
  d.name = uniqueName(deck.desktops.map((x) => x.name), d.name);
  for (const p of d.pages || []) { p.cols = p.cols || 4; p.rows = p.rows || 3; padKeys(p); }
  deck.desktops.push(d);
  return deck.desktops.length - 1;
}

export function addPage(desktop, title, cols = 3, rows = 2) {
  if (desktop.pages.length >= LIMITS.pages) return null;
  const id = uniqueId(desktop.pages.map((p) => p.id), 'p');
  desktop.pages.push(padKeys({ id, title: title || `Page ${desktop.pages.length + 1}`, cols, rows, keys: [] }));
  return desktop.pages.length - 1;
}

export function foldersTo(desktop, pageId) {
  let n = 0;
  for (const p of desktop.pages) for (const k of p.keys || []) if (k.folder === pageId) n++;
  return n;
}

// The root page (index 0) cannot be removed. Folder keys that pointed to the page become empty slots.
export function removePage(desktop, index) {
  if (index <= 0 || index >= desktop.pages.length) return null;
  const id = desktop.pages[index].id;
  let cleared = 0;
  for (const p of desktop.pages) {
    p.keys = (p.keys || []).map((k) => (k.folder === id ? (cleared++, {}) : k));
  }
  desktop.pages.splice(index, 1);
  return { cleared };
}

// ---- hotkeys ---------------------------------------------------------------------------------------

const MODS = ['ctrl', 'shift', 'alt', 'win'];
const MOD_ALIASES = { control: 'ctrl', super: 'win', cmd: 'win' };
export const NAMED_KEYS = [
  'enter', 'esc', 'tab', 'space', 'backspace', 'delete', 'insert', 'home', 'end', 'pageup', 'pagedown',
  'left', 'up', 'right', 'down', 'printscreen', 'capslock', 'menu',
  'volume_mute', 'volume_down', 'volume_up', 'next_track', 'prev_track', 'stop', 'play_pause',
];
const ALIASES = { return: 'enter', escape: 'esc', del: 'delete' };
const PUNCT = ';=,-./`[\\]\'';

// Same rules as keys.py: is this a key name the companion understands?
export function isValidKey(name) {
  const n = String(name).trim().toLowerCase();
  if (MODS.includes(n) || n in MOD_ALIASES) return true;
  if (NAMED_KEYS.includes(n) || n in ALIASES) return true;
  if (n.length === 1 && (/[a-z0-9]/.test(n) || PUNCT.includes(n))) return true;
  const f = /^f(\d{1,2})$/.exec(n);
  return !!f && +f[1] >= 1 && +f[1] <= 24;
}

// "ctrl+shift+s" -> { ctrl: true, shift: true, alt: false, win: false, key: 's' }. Keys other than the
// modifiers (and any text that is not a clean combination) are kept in `key` as written.
export function parseHotkey(text) {
  const out = { ctrl: false, shift: false, alt: false, win: false, key: '' };
  const parts = String(text || '').split('+').map((p) => p.trim().toLowerCase());
  const rest = [];
  for (const p of parts) {
    const m = MOD_ALIASES[p] || p;
    if (MODS.includes(m) && !out[m]) out[m] = true;
    else rest.push(p);
  }
  out.key = rest.join('+');
  return out;
}

export function composeHotkey({ ctrl, shift, alt, win, key }) {
  const parts = [];
  if (ctrl) parts.push('ctrl');
  if (shift) parts.push('shift');
  if (alt) parts.push('alt');
  if (win) parts.push('win');
  if (key) parts.push(String(key).trim().toLowerCase());
  return parts.join('+');
}

// KeyboardEvent.code -> key name used in deck.json (null for modifiers and unknown keys).
export function keyNameFromCode(code) {
  let m;
  if ((m = /^Key([A-Z])$/.exec(code))) return m[1].toLowerCase();
  if ((m = /^Digit([0-9])$/.exec(code))) return m[1];
  if ((m = /^Numpad([0-9])$/.exec(code))) return m[1];
  if ((m = /^F(\d{1,2})$/.exec(code))) return +m[1] >= 1 && +m[1] <= 24 ? code.toLowerCase() : null;
  const map = {
    Enter: 'enter', NumpadEnter: 'enter', Escape: 'esc', Tab: 'tab', Space: 'space', Backspace: 'backspace',
    Delete: 'delete', Insert: 'insert', Home: 'home', End: 'end', PageUp: 'pageup', PageDown: 'pagedown',
    ArrowLeft: 'left', ArrowUp: 'up', ArrowRight: 'right', ArrowDown: 'down', PrintScreen: 'printscreen',
    CapsLock: 'capslock', ContextMenu: 'menu',
    Semicolon: ';', Equal: '=', Comma: ',', Minus: '-', Period: '.', Slash: '/', Backquote: '`',
    BracketLeft: '[', Backslash: '\\', BracketRight: ']', Quote: "'",
    AudioVolumeMute: 'volume_mute', AudioVolumeDown: 'volume_down', AudioVolumeUp: 'volume_up',
    MediaTrackNext: 'next_track', MediaTrackPrevious: 'prev_track', MediaStop: 'stop', MediaPlayPause: 'play_pause',
  };
  return map[code] || null;
}

// ---- commands --------------------------------------------------------------------------------------

// "wt -d \"C:\\My Dir\"" -> ["wt", "-d", "C:\\My Dir"] (quotes group words; no escapes).
export function splitCommand(text) {
  const out = [];
  let cur = '', quote = null, started = false;
  for (const ch of String(text)) {
    if (quote) {
      if (ch === quote) quote = null;
      else cur += ch;
    } else if (ch === '"' || ch === "'") {
      quote = ch;
      started = true;
    } else if (/\s/.test(ch)) {
      if (started || cur) out.push(cur);
      cur = '';
      started = false;
    } else {
      cur += ch;
      started = true;
    }
  }
  if (started || cur) out.push(cur);
  return out;
}

export const joinCommand = (list) => list.map((a) => (/[\s"']/.test(a) || a === '' ? `"${a}"` : a)).join(' ');

// ---- actions ---------------------------------------------------------------------------------------

export const ACTION_TYPES = ['hotkey', 'text', 'open', 'run', 'lock', 'http', 'obs', 'multi'];

export function defaultAction(type) {
  switch (type) {
    case 'hotkey': return { type, keys: 'ctrl+shift+a' };
    case 'text': return { type, text: 'Hello!' };
    case 'open': return { type, target: 'https://www.google.com' };
    case 'run': return { type, command: 'notepad', shell: true };
    case 'lock': return { type };
    case 'http': return { type, method: 'GET', url: 'http://localhost:8080/' };
    case 'obs': return { type, request: 'ToggleRecord' };
    case 'multi': return { type, steps: [{ type: 'hotkey', keys: 'ctrl+c' }] };
    default: return null;
  }
}

// One line describing an action, for tooltips and preset cards.
export function describeAction(a) {
  if (!a) return 'no action';
  switch (a.type) {
    case 'hotkey': return `press ${a.keys}`;
    case 'text': return `type "${String(a.text).replace(/\n/g, '\u21B5')}"`;
    case 'open': return `open ${a.target}`;
    case 'run': return `run ${Array.isArray(a.command) ? joinCommand(a.command) : a.command}`;
    case 'lock': return 'lock the PC';
    case 'http': return `${String(a.method || 'GET').toUpperCase()} ${String(a.url || '').split('?')[0]}`;  // the query may hold a token
    case 'obs': return `OBS: ${a.request}`;
    case 'multi': return `${(a.steps || []).length} steps`;
    default: return String(a.type);
  }
}

// ---- HTTP and OBS actions ---------------------------------------------------------------------------------

export const HTTP_METHODS = ['GET', 'POST', 'PUT', 'PATCH', 'DELETE'];

// "Name: value" lines <-> a headers object. The value keeps everything after the first colon (a Bearer token, a URL ...).
export function parseHeaders(text) {
  const out = {};
  for (const line of String(text || '').split(/\r?\n/)) {
    const i = line.indexOf(':');
    if (i < 1) continue;
    const name = line.slice(0, i).trim();
    if (name) out[name] = line.slice(i + 1).trim();
  }
  return out;
}
export const joinHeaders = (headers) => Object.entries(headers || {}).map(([k, v]) => `${k}: ${v}`).join('\n');

// A JSON body or request data typed into a text box: an object or a list, else why not.
export function parseJsonBody(text) {
  let value;
  try { value = JSON.parse(text); } catch (e) { return { ok: false, error: `Invalid JSON, not saved: ${e.message}` }; }
  if (value === null || typeof value !== 'object') return { ok: false, error: 'Not saved: use an object {...} or a list [...]' };
  return { ok: true, value };
}

// obs-websocket 5 requests the editor knows by name; `fields` are the parameters (data key, label, example).
export const OBS_REQUESTS = [
  { id: 'ToggleRecord', label: 'Start / stop recording' },
  { id: 'ToggleRecordPause', label: 'Pause / resume recording' },
  { id: 'ToggleStream', label: 'Go live / stop the stream' },
  { id: 'SetCurrentProgramScene', label: 'Switch to a scene', fields: [['sceneName', 'Scene name', 'Game']] },
  { id: 'ToggleInputMute', label: 'Mute / unmute an input', fields: [['inputName', 'Input name', 'Mic/Aux']] },
  { id: 'SaveReplayBuffer', label: 'Save the replay buffer' },
  { id: 'ToggleVirtualCam', label: 'Start / stop the virtual camera' },
];
export const obsRequestInfo = (name) => OBS_REQUESTS.find((r) => r.id === name) || null;

// ---- hold, repeat and state keys; desktops that follow an app ----------------------------------------------

// A program name the way the companion compares it (deck.py / pcwatch.normalize_app): lower case, no ".exe".
export function normalizeApp(name) {
  const n = String(name || '').trim().toLowerCase();
  return n.endsWith('.exe') ? n.slice(0, -4) : n;
}
export const parseApps = (text) => [...new Set(String(text || '').split(/[,;\n]+/).map(normalizeApp).filter(Boolean))].slice(0, 10);

export const HOLD_MODES = [['none', 'Nothing'], ['repeat', 'Repeat'], ['action', 'Another action']];
export const holdMode = (k) => (k && k.repeat ? 'repeat' : k && k.hold ? 'action' : 'none');
export function setHoldMode(key, mode) {
  delete key.repeat;
  if (mode === 'none') delete key.hold;
  else if (mode === 'repeat') { key.repeat = true; delete key.hold; }
  else if (!key.hold) key.hold = defaultAction('hotkey');
}

export const STATE_SOURCES = [['none', 'Never'], ['mute', 'The speakers are muted'], ['mic_mute', 'The microphone is muted'], ['app', 'An app is running']];
export function stateKind(k) {
  const s = k && k.state && k.state.source;
  if (!s) return 'none';
  return String(s).startsWith('app:') ? 'app' : s;
}
export const stateApp = (k) => (stateKind(k) === 'app' ? String(k.state.source).slice(4) : '');
export function setStateKind(key, kind, app = 'obs64') {
  if (kind === 'none') { delete key.state; return; }
  key.state = { ...(key.state || {}), source: kind === 'app' ? `app:${normalizeApp(app) || 'obs64'}` : kind };
}
// What a key shows while its state is on: its own look with the state's changes on top (red for a mute, green for a
// running program, as deck.py decides).
export function stateLook(k) {
  const s = k.state || {};
  return {
    color: s.color || (stateKind(k) === 'app' ? '#2EA043' : '#B3261E'),
    icon: s.icon !== undefined ? s.icon : k.icon,
    label: s.label !== undefined ? s.label : k.label,
  };
}

// ---- what the board receives (for the size display) --------------------------------------------------

export function boardLayout(deck) {
  return {
    desktops: deck.desktops.map((d) => {
      if (d.kind && d.kind !== 'keys') {
        const o = liveOptions(d);
        // the player filter stays on the PC: the board only gets how the screen looks
        const board = d.kind === 'nowplaying' ? { tint: o.tint, album: o.album, controls: o.controls, appIcon: o.appIcon }
          : d.kind === 'mixer' ? {}
            : d.kind === 'clock' ? { hour12: o.hour12, seconds: o.seconds, timer: o.timer, weather: o.weather && !!o.place }  // the place stays on the PC
              : { style: o.style, items: o.items };
        return { name: d.name, kind: d.kind, options: board };
      }
      return {
        name: d.name,
        pages: d.pages.map((p) => ({
          id: p.id, title: p.title, cols: p.cols, rows: p.rows,
          keys: (p.keys || []).map((k) => {
            const o = {};
            for (const f of ['label', 'icon', 'id', 'color', 'folder']) if (k[f]) o[f] = k[f];
            if (!k.folder) {
              if (k.hold) o.hold = true;
              if (k.repeat) o.repeat = true;
              if (stateKind(k) !== 'none') {
                const look = stateLook(k);
                o.on = { color: look.color };
                if (k.state.icon !== undefined) o.on.icon = k.state.icon;
                if (k.state.label !== undefined) o.on.label = k.state.label;
              }
            }
            return o;
          }),
        })),
      };
    }),
  };
}

export const layoutBytes = (deck) => new TextEncoder().encode(JSON.stringify(boardLayout(deck))).length;

// ---- undo history ----------------------------------------------------------------------------------

export class History {
  constructor(limit = 100) {
    this.limit = limit;
    this.past = [];
    this.future = [];
  }

  push(snapshot) {
    if (this.past[this.past.length - 1] === snapshot) return;
    this.past.push(snapshot);
    if (this.past.length > this.limit) this.past.shift();
    this.future = [];
  }

  undo(current) {
    if (!this.past.length) return null;
    this.future.push(current);
    return this.past.pop();
  }

  redo(current) {
    if (!this.future.length) return null;
    this.past.push(current);
    return this.future.pop();
  }

  get canUndo() { return this.past.length > 0; }
  get canRedo() { return this.future.length > 0; }
}

// ---- live screens: options ---------------------------------------------------------------------------

export const STAT_ITEMS = [
  { id: 'cpu', name: 'CPU' }, { id: 'gpu', name: 'GPU' }, { id: 'ram', name: 'RAM' },
  { id: 'vram', name: 'VRAM' }, { id: 'disk', name: 'Disk' }, { id: 'net', name: 'Network' },
];
export const STAT_STYLES = [{ id: 'gauges', name: 'Gauges' }, { id: 'bars', name: 'Bars' }, { id: 'tiles', name: 'Tiles' }];
// The options of a live desktop with every default filled in (the file may hold only some of them).
export function liveOptions(d) {
  const o = d.options || {};
  if (d.kind === 'nowplaying') {
    return { filter: normalizeFilter(o.filter), tint: o.tint ?? true, album: o.album ?? true, controls: o.controls || 'full', appIcon: o.appIcon ?? false };
  }
  if (d.kind === 'mixer') return { apps: (o.apps || []).slice(0, 4) };
  if (d.kind === 'clock') {
    return { hour12: o.hour12 ?? false, seconds: o.seconds ?? false, timer: o.timer ?? true, weather: o.weather ?? true,
      units: o.units || 'c', place: o.place || null };
  }
  return { style: o.style || 'gauges', items: o.items && o.items.length ? [...o.items] : STAT_ITEMS.map((x) => x.id) };
}

// "Oslo, Norway" for a place found by the search, and the short text for the one that is chosen.
export const placeText = (p) => (p ? `${p.name} (${p.lat}, ${p.lon})` : 'no place chosen');
export const foundPlaceText = (p) => [p.name, p.region, p.country].filter(Boolean).join(', ');

export function setOptions(d, patch) {
  d.options = { ...(d.options || {}), ...patch };
}

// ---- the Now playing player filter -------------------------------------------------------------------
// Two lists: "allow" (when not empty, only these players can be shown) and "block" (these never are; it wins).
// Players are matched by a part of the name Windows gives them, case-insensitive (same rule as nowplaying.py).

// Reads the current form {allow, block} and the older {mode: all|only|except, apps}.
export function normalizeFilter(f) {
  f = f || {};
  if ('mode' in f || 'apps' in f) {
    const apps = f.apps || [];
    return { allow: f.mode === 'only' ? [...apps] : [], block: f.mode === 'except' ? [...apps] : [] };
  }
  return { allow: [...(f.allow || [])], block: [...(f.block || [])] };
}

export function appMatches(appId, patterns) {
  const low = String(appId || '').toLowerCase();
  return patterns.some((p) => p && low.includes(String(p).toLowerCase()));
}

const sameName = (a, b) => String(a).toLowerCase() === String(b).toLowerCase();
const otherList = (which) => (which === 'allow' ? 'block' : 'allow');
export const FILTER_LIST_LIMIT = 30;

// Puts names on one list ('allow' or 'block') and takes them off the other (a player is on at most one list).
// Blank, over-long and duplicate names are skipped; a list holds at most 30. Returns how many were added.
export function addFilterApps(filter, names, which) {
  let added = 0;
  for (const raw of names) {
    const name = String(raw).trim();
    if (!name || name.length > 80) continue;
    filter[otherList(which)] = filter[otherList(which)].filter((x) => !sameName(x, name));
    if (filter[which].some((x) => sameName(x, name)) || filter[which].length >= FILTER_LIST_LIMIT) continue;
    filter[which].push(name);
    added++;
  }
  return added;
}

export function removeFilterApps(filter, which, names) {
  filter[which] = filter[which].filter((x) => !names.some((n) => sameName(x, n)));
}

// Is any of these names on the list?
export const inFilterList = (filter, which, names) => names.some((n) => filter[which].some((x) => sameName(x, n)));

// What else an icon can be found by in the picker (its own name always counts).
export const ICON_WORDS = {
  audio: 'music note song sound', video: 'film movie clip', list: 'menu rows', ok: 'check tick done yes', close: 'x cancel exit quit',
  power: 'shutdown on off', settings: 'gear cog options', home: 'house start', download: 'pull save', upload: 'push send',
  refresh: 'reload sync again', mute: 'speaker volume off quiet', volume_down: 'speaker sound quieter', volume_up: 'speaker sound louder audio',
  image: 'picture photo', edit: 'pencil write rename', prev: 'previous back track', next: 'forward skip track', stop: 'square end',
  play: 'start run', pause: 'hold', eject: 'remove disc', left: 'arrow chevron back', right: 'arrow chevron forward', up: 'arrow chevron',
  down: 'arrow chevron', plus: 'add new', minus: 'remove less', eye: 'view show see', eye_off: 'hide hidden invisible',
  warning: 'alert error caution', shuffle: 'random mix', loop: 'repeat cycle', directory: 'folder files explorer', file: 'document page new',
  call: 'phone', cut: 'scissors clip', copy: 'duplicate', paste: 'clipboard', save: 'floppy disk', bell: 'notification alert unread',
  keyboard: 'type typing hotkey', gps: 'location arrow', wifi: 'network signal wireless', bluetooth: 'wireless', trash: 'delete bin remove',
  usb: 'cable plug', charge: 'bolt lightning power energy', battery: 'power charge', drive: 'disk storage', backspace: 'erase delete clear',
  mic: 'microphone voice talk', mic_off: 'microphone muted voice silent', headphones: 'headset deafen audio ears',
  speaker_off: 'mute muted sound volume silent', camera: 'photo screenshot picture snap', webcam: 'video camera cam',
  webcam_off: 'video camera off cam', record: 'rec dot circle', broadcast: 'live stream antenna on air', layers: 'scene scenes stack',
  lightbulb: 'light lamp bulb idea smart home', lock: 'secure password padlock', desktop: 'monitor screen computer pc',
  search: 'find magnifier look', smile: 'emoji face happy shrug', clipboard: 'paste history', chart: 'task manager graph stats usage',
  task_view: 'windows overview', exchange: 'switch swap alt tab transition', maximize: 'window big', minimize: 'window small hide',
  fullscreen: 'expand full screen', columns: 'split snap studio side', undo: 'revert back', redo: 'again forward',
  globe: 'web website browser internet address', code: 'developer brackets vscode dev tools', terminal: 'console command shell prompt',
  calculator: 'math calc sum', envelope: 'mail email gmail message', youtube: 'video', spotify: 'music', discord: 'chat voice',
  steam: 'games', gamepad: 'game controller gaming', branch: 'git merge version', bug: 'debug error', history: 'recent clock reopen replay',
  incognito: 'private secret spy', send: 'webhook post paper plane request', script: 'file code automation macro',
  text: 'font type letters write', bold: 'strong format', italic: 'slant format', underline: 'format line', print: 'printer paper',
  zoom_in: 'magnify bigger plus', zoom_out: 'smaller minus',
};

export function iconMatches(name, query) {
  const words = `${name.replace(/_/g, ' ')} ${ICON_WORDS[name] || ''}`.toLowerCase();
  return String(query || '').toLowerCase().split(/\s+/).every((w) => words.includes(w));
}

// The board's fonts hold plain ASCII, the degree sign and the bullet: accents, emoji and the like do not show.
export const boardCannotShow = (text) => /[^\x20-\x7E\u00B0\u2022]/.test(String(text || ''));

// A page as the editor names it: its title, plus its id only when another page of the desktop has the same title.
export function pageName(desktop, p) {
  const name = p.title || p.id;
  const twin = desktop.pages.some((q) => q !== p && (q.title || q.id) === name);
  return twin ? `${name} (${p.id})` : name;
}

// A key as an error names it: its label, with its place on the page when it has none or another key has the same one.
export function keyTitle(p, i) {
  const keys = (p && p.keys) || [], cols = (p && p.cols) || 4;
  const label = keys[i] && keys[i].label;
  const place = `row ${Math.floor(i / cols) + 1}, column ${(i % cols) + 1}`;
  if (!label) return `unlabeled key (${place})`;
  return keys.filter((k) => k && k.label === label).length > 1 ? `${label} (${place})` : label;
}

// A message from the companion that starts with a place in the file ("desktops[0].pages[1].keys[2].action: ...") said with
// the names on screen ("Main \u203A Media \u203A Mute: ..."). Anything else is returned as it is.
export function describeProblem(message, deck) {
  const at = /^desktops\[(\d+)\](?:\.pages\[(\d+)\])?(?:\.keys\[(\d+)\])?((?:\.[a-z]+(?:\[\d+\])?)*):\s*/i.exec(message);
  const d = at && deck.desktops[+at[1]];
  if (!d) return message;
  const parts = [d.name || 'Untitled'];
  const p = at[2] !== undefined && d.pages && d.pages[+at[2]];
  const inKey = at[3] !== undefined; // a key's page goes without saying when it is the only one
  if (p && !(inKey && d.pages.length === 1)) parts.push(pageName(d, p));
  if (inKey) {
    parts.push(keyTitle(p, +at[3]) + (/\.hold\b/.test(at[4]) ? ' (hold)' : ''));
    for (const step of at[4].matchAll(/steps\[(\d+)\]/g)) parts.push(`step ${+step[1] + 1}`);
  }
  return `${parts.join(' \u203A ')}: ${message.slice(at[0].length)}`;
}

// The catalog app (apps.json) a name belongs to: an exact match of one of its patterns, else a name that contains one
// ("SpotifyAB.SpotifyMusic_...!Spotify" is Spotify). null for custom names.
export function catalogApp(catalog, name) {
  const low = String(name).toLowerCase();
  const apps = catalog.apps;
  return apps.find((a) => a.patterns.some((q) => low === q.toLowerCase())) || apps.find((a) => a.patterns.some((q) => low.includes(q.toLowerCase()))) || null;
}

// A filter list as display entries: names of the same catalog app (Firefox has two) are one entry, custom names one each.
export function filterEntries(list, catalog) {
  const entries = [];
  for (const name of list) {
    const app = catalogApp(catalog, name);
    const key = app ? app.id : 'custom:' + name.toLowerCase();
    const found = entries.find((e) => e.key === key);
    if (found) found.names.push(name);
    else entries.push({ key, app, names: [name] });
  }
  return entries;
}

// Stats items: the enabled ones in screen order, then the others.
export function statItemOrder(items) {
  const all = STAT_ITEMS.map((x) => x.id);
  return { enabled: items.filter((i) => all.includes(i)), disabled: all.filter((i) => !items.includes(i)) };
}

// Turns an item on (appended) or off. The last enabled item cannot be turned off. Returns whether it changed.
export function toggleStatItem(items, id, on) {
  const at = items.indexOf(id);
  if (on && at < 0) { items.push(id); return true; }
  if (!on && at >= 0 && items.length > 1) { items.splice(at, 1); return true; }
  return false;
}
