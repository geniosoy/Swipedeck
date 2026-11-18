// Pure logic of the deck editor (no DOM): deck edits, grid geometry, hotkey handling, undo history.
// Used by app.js in the browser and by tests/js/model.test.mjs in Node.

export const LIMITS = { desktops: 8, pages: 16, cols: 6, rows: 5 };
export const SCREEN = { w: 320, h: 240, header: 30 };
export const DEFAULT_KEY_COLOR = '#2A3A4D';

export const clone = (v) => JSON.parse(JSON.stringify(v));

// ---- keys ------------------------------------------------------------------------------------------

export const isEmptyKey = (k) => !k || Object.keys(k).length === 0;
export const keyKind = (k) => (isEmptyKey(k) ? 'empty' : k.folder ? 'folder' : 'action');

// Same math as the firmware (buildPage in src/ui.cpp): padding 6, gap 6, the tile is 320 x 210.
export function geometry(cols, rows, w = SCREEN.w, h = SCREEN.h - SCREEN.header) {
  const pad = 6, gap = 6;
  const keyW = Math.floor((w - 2 * pad - (cols - 1) * gap) / cols);
  const keyH = Math.floor((h - 2 * pad - (rows - 1) * gap) / rows);
  return {
    keyW, keyH, pad, gap,
    iconPx: keyH >= 86 ? 32 : keyH >= 62 ? 24 : 20,
    textPx: keyH >= 86 ? 16 : 14,
    pos: (i) => ({ x: pad + (i % cols) * (keyW + gap), y: pad + Math.floor(i / cols) * (keyH + gap) }),
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

export const specialDesktop = (kind) => ({ name: kind === 'stats' ? 'PC' : 'Playing', kind });
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

export const ACTION_TYPES = ['hotkey', 'text', 'open', 'run', 'lock', 'multi'];

export function defaultAction(type) {
  switch (type) {
    case 'hotkey': return { type, keys: 'ctrl+shift+a' };
    case 'text': return { type, text: 'Hello!' };
    case 'open': return { type, target: 'https://www.google.com' };
    case 'run': return { type, command: 'notepad', shell: true };
    case 'lock': return { type };
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
    case 'multi': return `${(a.steps || []).length} steps`;
    default: return String(a.type);
  }
}

// ---- what the board receives (for the size display) --------------------------------------------------

export function boardLayout(deck) {
  return {
    desktops: deck.desktops.map((d) => {
      if (d.kind && d.kind !== 'keys') return { name: d.name, kind: d.kind };
      return {
        name: d.name,
        pages: d.pages.map((p) => ({
          id: p.id, title: p.title, cols: p.cols, rows: p.rows,
          keys: (p.keys || []).map((k) => {
            const o = {};
            for (const f of ['label', 'icon', 'id', 'color', 'folder']) if (k[f]) o[f] = k[f];
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
