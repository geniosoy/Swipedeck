// Run: node --test companion/tests/js
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import * as M from '../../streamdeck_companion/web/model.js';

const deckFile = new URL('../fixtures/deck.json', import.meta.url)  // a frozen demo deck, not your live one;
const sample = () => JSON.parse(readFileSync(deckFile, 'utf8'));

test('geometry matches the firmware for the layouts in deck.json', () => {
  // 4x3: (210 - 12 - 12) / 3 = 62 -> 24 px icons; width (320 - 12 - 18) / 4 = 72
  let g = M.geometry(4, 3);
  assert.deepEqual([g.keyW, g.keyH, g.iconPx, g.textPx], [72, 62, 24, 14]);
  assert.deepEqual(g.pos(5), { x: 6 + 78, y: 6 + 68 });
  // 3x2: keyH (210 - 12 - 6) / 2 = 96 -> 32 px icons, 16 px text; keyW (320 - 12 - 12) / 3 = 98
  g = M.geometry(3, 2);
  assert.deepEqual([g.keyW, g.keyH, g.iconPx, g.textPx], [98, 96, 32, 16]);
  // 6x5: tiny keys
  g = M.geometry(6, 5);
  assert.equal(g.iconPx, 20);
});

test('the key gap changes the geometry like the firmware does', () => {
  // 4x3 with a gap of 2: keyW = (320 - 12 - 3 * 2) / 4 = 75, keyH = (210 - 12 - 2 * 2) / 3 = 64
  let g = M.geometry(4, 3, undefined, undefined, 2);
  assert.deepEqual([g.keyW, g.keyH, g.gap], [75, 64, 2]);
  assert.deepEqual(g.pos(5), { x: 6 + 77, y: 6 + 66 });
  g = M.geometry(4, 3, undefined, undefined, 12); // the widest gap
  assert.deepEqual([g.keyW, g.keyH], [(320 - 12 - 36) / 4, Math.floor((210 - 12 - 24) / 3)]);
  assert.deepEqual([M.geometry(4, 3).keyW, M.geometry(4, 3).gap], [72, 6]); // no gap given: the default
});

test('settings missing from the deck are at their defaults, and the defaults match the board', () => {
  assert.deepEqual(M.resolveSettings({}), M.SETTING_DEFAULTS);
  assert.deepEqual(M.resolveSettings(null), M.SETTING_DEFAULTS);
  const got = M.resolveSettings({ settings: { volume: 10, flip: true } });
  assert.equal(got.volume, 10);
  assert.equal(got.flip, true);
  assert.equal(got.keyGap, 6);
  assert.deepEqual(M.SOUND_THEMES.map(([id]) => id), ['classic', 'soft', 'click', 'chirp']);
  assert.ok(M.SOUND_THEMES.some(([id]) => id === M.SETTING_DEFAULTS.soundTheme));
});

test('resizePage keeps keys at their row and column and reports what is lost', () => {
  const page = { cols: 3, rows: 2, keys: [{ label: 'a' }, { label: 'b' }, { label: 'c' }, { label: 'd' }, {}, { label: 'f' }] };
  assert.equal(M.resizeLoss(page, 2, 2), 2); // b? c and f fall off (column 3) -> 'c' and 'f'
  assert.equal(page.cols, 3); // resizeLoss does not modify
  const dropped = M.resizePage(page, 4, 2);
  assert.equal(dropped, 0);
  assert.equal(page.keys.length, 8);
  assert.equal(page.keys[0].label, 'a');
  assert.equal(page.keys[3] && Object.keys(page.keys[3]).length, 0); // new column is empty
  assert.equal(page.keys[4].label, 'd'); // row 2 starts at index 4 now
  assert.equal(page.keys[6].label, "f"); // old row 2, column 3 -> index 4 + 2
  assert.equal(M.resizePage(page, 9, 9), 0);
  assert.equal(page.cols, M.LIMITS.cols); // clamped
  assert.equal(page.rows, M.LIMITS.rows);
});

test('swapKeys, padKeys and firstEmptySlot', () => {
  const page = { cols: 2, rows: 2, keys: [{ label: 'a' }] };
  assert.equal(M.firstEmptySlot(page), 1);
  assert.equal(page.keys.length, 4);
  M.swapKeys(page, 0, 3);
  assert.equal(page.keys[3].label, 'a');
  assert.equal(M.firstEmptySlot({ cols: 1, rows: 1, keys: [{ label: 'x' }] }), null);
});

test('desktops: limits, one live screen of each kind, removal and moving', () => {
  const deck = { desktops: [M.newKeysDesktop({ desktops: [] }, 'Main')] };
  assert.equal(deck.desktops[0].pages[0].keys.length, 12);
  assert.equal(M.addDesktop(deck, 'nowplaying'), 1);
  assert.equal(M.addDesktop(deck, 'nowplaying'), null); // only one
  assert.equal(M.addDesktop(deck, 'stats'), 2);
  assert.equal(M.addDesktop(deck, 'stats'), null);
  while (deck.desktops.length < M.LIMITS.desktops) assert.notEqual(M.addDesktop(deck, 'keys'), null);
  assert.equal(M.addDesktop(deck, 'keys'), null);
  assert.equal(M.moveInList(deck.desktops, 2, 0), true);
  assert.equal(deck.desktops[0].kind, 'stats');
  assert.equal(M.moveInList(deck.desktops, 0, -1), false);
  assert.equal(M.removeDesktop({ desktops: [{ name: 'only' }] }, 0), false); // never remove the last one
});

test('pages: unique ids, limit, folder references cleared on removal, root protected', () => {
  const d = M.newKeysDesktop({ desktops: [] }, 'Main');
  const a = M.addPage(d, 'A');
  const b = M.addPage(d, 'B');
  assert.deepEqual(d.pages.map((p) => p.id), ['root', 'p1', 'p2']);
  d.pages[0].keys[0] = { label: 'A', folder: 'p1' };
  d.pages[0].keys[1] = { label: 'A again', folder: 'p1' };
  d.pages[0].keys[2] = { label: 'B', folder: 'p2' };
  assert.equal(M.foldersTo(d, 'p1'), 2);
  assert.deepEqual(M.removePage(d, a), { cleared: 2 });
  assert.equal(d.pages.length, 2);
  assert.equal(Object.keys(d.pages[0].keys[0]).length, 0);
  assert.equal(d.pages[0].keys[2].folder, 'p2'); // the other folder is untouched
  assert.equal(M.removePage(d, 0), null); // root
  assert.equal(M.addPage(d, 'C'), 2);
  assert.equal(d.pages[2].id, 'p1'); // the freed id is reused, no clash
  while (d.pages.length < M.LIMITS.pages) M.addPage(d, 'x');
  assert.equal(M.addPage(d, 'one too many'), null);
});

test('hotkeys: parse, compose, validity, and keys captured from the keyboard', () => {
  assert.deepEqual(M.parseHotkey('Ctrl+Shift+S'), { ctrl: true, shift: true, alt: false, win: false, key: 's' });
  assert.deepEqual(M.parseHotkey('win+e'), { ctrl: false, shift: false, alt: false, win: true, key: 'e' });
  assert.equal(M.parseHotkey('volume_mute').key, 'volume_mute');
  assert.equal(M.composeHotkey(M.parseHotkey('shift+ctrl+alt+win+f5')), 'ctrl+shift+alt+win+f5');
  assert.equal(M.composeHotkey({ ctrl: false, shift: false, alt: false, win: false, key: '' }), '');
  for (const ok of ['a', '5', 'f1', 'F24', 'enter', 'volume_up', 'play_pause', ';', '`', 'ctrl', 'Escape'])
    assert.equal(M.isValidKey(ok), true, ok);
  for (const bad of ['', 'f25', 'f0', 'ab', 'nonsense', '+'])
    assert.equal(M.isValidKey(bad), false, bad);
  assert.equal(M.keyNameFromCode('KeyA'), 'a');
  assert.equal(M.keyNameFromCode('Digit7'), '7');
  assert.equal(M.keyNameFromCode('F13'), 'f13');
  assert.equal(M.keyNameFromCode('F30'), null);
  assert.equal(M.keyNameFromCode('ArrowLeft'), 'left');
  assert.equal(M.keyNameFromCode('MediaPlayPause'), 'play_pause');
  assert.equal(M.keyNameFromCode('ControlLeft'), null);
  // every name the editor can produce must be one the companion accepts
  for (const code of ['KeyZ', 'Digit0', 'F1', 'Enter', 'Escape', 'Space', 'Semicolon', 'BracketRight', 'AudioVolumeUp'])
    assert.equal(M.isValidKey(M.keyNameFromCode(code)), true, code);
});

test('commands: quote-aware splitting and joining', () => {
  assert.deepEqual(M.splitCommand('wt -d "C:\\My Dir" --x'), ['wt', '-d', 'C:\\My Dir', '--x']);
  assert.deepEqual(M.splitCommand("  a   'b c'  "), ['a', 'b c']);
  assert.deepEqual(M.splitCommand(''), []);
  assert.deepEqual(M.splitCommand('""'), ['']);
  const list = ['a', 'b c', ''];
  assert.deepEqual(M.splitCommand(M.joinCommand(list)), list);
});

test('actions: defaults are valid shapes and have descriptions', () => {
  for (const t of M.ACTION_TYPES) {
    const a = M.defaultAction(t);
    assert.equal(a.type, t);
    assert.ok(M.describeAction(a).length > 0);
  }
  assert.equal(M.defaultAction('nope'), null);
  assert.equal(M.describeAction(null), 'no action');
});

test('default deck: board layout has no actions and the size is small', () => {
  const deck = sample();
  const layout = M.boardLayout(deck);
  assert.ok(!JSON.stringify(layout).includes('"action"'));
  assert.equal(layout.desktops.length, deck.desktops.length);
  assert.ok(M.layoutBytes(deck) > 500 && M.layoutBytes(deck) < 6000);
  assert.deepEqual(layout.desktops[1], { name: 'Playing', kind: 'nowplaying', options: { tint: true, album: true, controls: 'full', appIcon: false } });
});

test('history: undo, redo, and a new change clears redo', () => {
  const h = new M.History(3);
  h.push('a'); h.push('b'); h.push('b');
  assert.equal(h.past.length, 2); // no duplicate snapshots
  assert.equal(h.undo('c'), 'b');
  assert.equal(h.canRedo, true);
  assert.equal(h.redo('b'), 'c');
  h.undo('c');
  h.push('x');
  assert.equal(h.canRedo, false);
  h.push('1'); h.push('2'); h.push('3');
  assert.equal(h.past.length, 3); // limit
  assert.equal(new M.History().undo('z'), null);
});

test('dropTarget: where a dragged item lands', () => {
  assert.equal(M.dropTarget(3, 0, false), 0);  // onto the top half of the first item
  assert.equal(M.dropTarget(3, 0, true), 1);   // onto its lower half
  assert.equal(M.dropTarget(0, 3, true), 3);   // down to after the 4th item (it moves up by one first)
  assert.equal(M.dropTarget(0, 3, false), 2);
  assert.equal(M.dropTarget(1, 2, false), 1);  // dropped right where it already is
  assert.equal(M.dropTarget(2, 1, true), 2);
  const list = ['a', 'b', 'c', 'd'];
  M.moveInList(list, 0, M.dropTarget(0, 3, true));
  assert.deepEqual(list, ['b', 'c', 'd', 'a']);
  M.moveInList(list, 3, M.dropTarget(3, 0, false));
  assert.deepEqual(list, ['a', 'b', 'c', 'd']);
  assert.equal(M.moveInList(list, 1, M.dropTarget(1, 2, false)), false); // no-op
});

test('template desktops: copied, padded, uniquely named, limited', () => {
  const tpl = JSON.parse(readFileSync(new URL('../../streamdeck_companion/web/templates.json', import.meta.url), 'utf8')).templates;
  const deck = { desktops: [M.newKeysDesktop({ desktops: [] }, 'Main')] };
  const i = M.addTemplateDesktop(deck, tpl.find((t) => t.id === 'obs').desktop);
  assert.equal(i, 1);
  assert.equal(deck.desktops[1].name, 'OBS');
  assert.equal(deck.desktops[1].pages[0].keys.length, 12);   // padded to the grid
  deck.desktops[1].pages[0].keys[0].label = 'changed';
  assert.notEqual(tpl.find((t) => t.id === 'obs').desktop.pages[0].keys[0].label, 'changed'); // a copy, not a reference
  M.addTemplateDesktop(deck, tpl.find((t) => t.id === 'obs').desktop);
  assert.equal(deck.desktops[2].name, 'OBS 2');
  assert.equal(M.uniqueName(['A', 'A 2'], 'A'), 'A 3');
  while (deck.desktops.length < M.LIMITS.desktops) M.addTemplateDesktop(deck, tpl[0].desktop);
  assert.equal(M.addTemplateDesktop(deck, tpl[0].desktop), null);
});

const catalog = JSON.parse(readFileSync(new URL('../../streamdeck_companion/web/apps.json', import.meta.url), 'utf8'));

test('live options: defaults, partial options, and what the board receives', () => {
  const np = { name: 'P', kind: 'nowplaying' };
  assert.deepEqual(M.liveOptions(np), { filter: { allow: [], block: [] }, tint: true, album: true, controls: 'full', appIcon: false });
  M.setOptions(np, { tint: false, filter: { allow: ['tidal'], block: ['chrome'] } });
  assert.deepEqual(M.liveOptions(np), { filter: { allow: ['tidal'], block: ['chrome'] }, tint: false, album: true, controls: 'full', appIcon: false });
  M.setOptions(np, { appIcon: true });
  assert.equal(M.liveOptions(np).appIcon, true);
  assert.equal(M.boardLayout({ desktops: [np] }).desktops[0].options.appIcon, true); // the board has to know
  M.setOptions(np, { appIcon: false });
  const stats = { name: 'S', kind: 'stats', options: { style: 'tiles', items: [] } };
  assert.deepEqual(M.liveOptions(stats).items, ['cpu', 'gpu', 'ram', 'vram', 'disk', 'net']); // empty = all
  assert.equal(M.liveOptions(stats).style, 'tiles');
  const board = M.boardLayout({ desktops: [np, stats] }).desktops;
  assert.deepEqual(board[0].options, { tint: false, album: true, controls: 'full', appIcon: false });
  assert.ok(!JSON.stringify(board).includes('tidal'), 'the player filter must stay on the PC');
});

test('player filter: the older form is read, lists are exclusive, limits hold', () => {
  assert.deepEqual(M.normalizeFilter(undefined), { allow: [], block: [] });
  assert.deepEqual(M.normalizeFilter({ mode: 'only', apps: ['a'] }), { allow: ['a'], block: [] });
  assert.deepEqual(M.normalizeFilter({ mode: 'except', apps: ['b'] }), { allow: [], block: ['b'] });
  assert.deepEqual(M.normalizeFilter({ mode: 'all', apps: ['x'] }), { allow: [], block: [] });
  const np = { name: 'P', kind: 'nowplaying', options: { filter: { mode: 'except', apps: ['chrome'] } } };
  assert.deepEqual(M.liveOptions(np).filter, { allow: [], block: ['chrome'] }); // an older deck.json shows up in the new form

  const f = { allow: [], block: [] };
  assert.equal(M.addFilterApps(f, ['Tidal', 'tidal', '  ', 'Spotify'], 'allow'), 2); // duplicates (any case) and blanks skipped
  assert.deepEqual(f.allow, ['Tidal', 'Spotify']);
  assert.equal(M.addFilterApps(f, ['x'.repeat(81)], 'allow'), 0);
  M.addFilterApps(f, ['spotify'], 'block'); // moving: it leaves the other list
  assert.deepEqual(f, { allow: ['Tidal'], block: ['spotify'] });
  assert.equal(M.inFilterList(f, 'block', ['SPOTIFY']), true);
  assert.equal(M.inFilterList(f, 'allow', ['spotify']), false);
  M.removeFilterApps(f, 'block', ['Spotify']);
  assert.deepEqual(f.block, []);
  for (let i = 0; i < 40; i++) M.addFilterApps(f, ['app' + i], 'allow');
  assert.equal(f.allow.length, 30); // the companion accepts at most 30 per list
});

test('player filter: matching', () => {
  assert.equal(M.appMatches('SpotifyAB.SpotifyMusic_zpdnekdrzrea0!Spotify', ['spotify']), true);
  assert.equal(M.appMatches('Chrome', ['tidal', '']), false);
  assert.equal(M.appMatches('', ['x']), false);
});

test('app catalog: every app has an icon source, and names resolve to apps', () => {
  const ids = new Set();
  for (const a of catalog.apps) {
    assert.ok(!ids.has(a.id), 'duplicate id ' + a.id);
    ids.add(a.id);
    assert.ok(a.name && a.patterns.length && /^#[0-9A-Fa-f]{6}$/.test(a.color), a.id);
    assert.ok(/^[0-9a-f]{4}$/.test(a.glyph || '') || (a.letter && a.letter.length === 1), a.id + ' needs a glyph or a letter');
    assert.ok(catalog.categories.some((c) => c.id === a.category), a.id);
  }
  assert.ok(catalog.apps.length >= 20);
  assert.equal(M.catalogApp(catalog, 'spotify').id, 'spotify');
  assert.equal(M.catalogApp(catalog, 'SpotifyAB.SpotifyMusic_zpdnekdrzrea0!Spotify').id, 'spotify');
  assert.equal(M.catalogApp(catalog, 'Spotify.exe').id, 'spotify');
  assert.equal(M.catalogApp(catalog, 'Chrome').id, 'chrome');
  assert.equal(M.catalogApp(catalog, 'MSEdge').id, 'edge');
  assert.equal(M.catalogApp(catalog, '308046B0AF4A39CB').id, 'firefox');
  assert.equal(M.catalogApp(catalog, 'my-own-player'), null);
  const entries = M.filterEntries(['firefox', 'Chrome', '308046B0AF4A39CB', 'my-own-player', 'MyOwnPlayer'], catalog);
  assert.deepEqual(entries.map((e) => [e.key, e.names.length]), [['firefox', 2], ['chrome', 1], ['custom:my-own-player', 1], ['custom:myownplayer', 1]]);
  assert.equal(entries[2].app, null);
});

test('stats items: order, toggling, never empty', () => {
  assert.deepEqual(M.statItemOrder(['ram', 'cpu']), { enabled: ['ram', 'cpu'], disabled: ['gpu', 'vram', 'disk', 'net'] });
  const items = ['cpu'];
  assert.equal(M.toggleStatItem(items, 'cpu', false), false); // the last one stays
  assert.equal(M.toggleStatItem(items, 'net', true), true);
  assert.equal(M.toggleStatItem(items, 'net', true), false);  // already on
  assert.deepEqual(items, ['cpu', 'net']);
  assert.equal(M.toggleStatItem(items, 'cpu', false), true);
  assert.deepEqual(items, ['net']);
  assert.equal(M.STAT_ITEMS.length, 6);
  assert.deepEqual(M.STAT_STYLES.map((s) => s.id), ['gauges', 'bars', 'tiles']);
});

test('program names: compared without case, spaces and .exe; lists are cleaned up', () => {
  assert.equal(M.normalizeApp(' OBS64.exe '), 'obs64');
  assert.deepEqual(M.parseApps('OBS64.exe, code ; Code\n discord'), ['obs64', 'code', 'discord']);
  assert.deepEqual(M.parseApps(''), []);
  assert.deepEqual(M.parseApps(',, ,'), []);
  assert.equal(M.parseApps(Array.from({ length: 20 }, (_, i) => 'app' + i).join(',')).length, 10);
});

test('text the board cannot show is noticed', () => {
  for (const ok of ['', 'Vol +', '9\u00B0', 'a \u2022 b']) assert.equal(M.boardCannotShow(ok), false, ok);
  for (const no of ['M\u00FAsica', 'Ni\u00F1o', '\uD83C\uDFB5']) assert.equal(M.boardCannotShow(no), true, no);
});

test('a page is named by its title, with the id only to tell two apart', () => {
  const pages = [{ id: 'root', title: 'Main' }, { id: 'a', title: 'Git' }, { id: 'b', title: 'Git' }];
  assert.deepEqual(pages.map((p) => M.pageName({ pages }, p)), ['Main', 'Git (a)', 'Git (b)']);
});

test('errors are said with the names on screen', () => {
  const root = { id: 'root', title: 'Main', cols: 4, keys: [{ label: 'Lock' }, {}, { label: 'Vol' }, { label: 'Vol' }] };
  const media = { id: 'm', title: 'Media', cols: 4, keys: [{}, {}, { label: 'Next' }] };
  const deck = { desktops: [{ name: 'Main', pages: [root, media] }, { name: 'Clock', kind: 'clock' }, { name: 'Work', pages: [{ ...root, title: 'Work' }] }] };
  const say = (m) => M.describeProblem(m, deck);
  assert.equal(say('desktops[0].pages[1].keys[2].action: Enter the text to type.'), 'Main \u203A Media \u203A Next: Enter the text to type.');
  assert.equal(say('desktops[0].pages[0].keys[0].hold.steps[1]: Enter a command.'), 'Main \u203A Main \u203A Lock (hold) \u203A step 2: Enter a command.');
  assert.equal(say('desktops[2].pages[0].keys[0]: x'), 'Work \u203A Lock: x'); // one page: it goes without saying
  assert.equal(say('desktops[0].pages[0].keys[3]: x'), 'Main \u203A Main \u203A Vol (row 1, column 4): x'); // two keys named alike
  assert.equal(say('desktops[0].pages[1].keys[5]: x'), 'Main \u203A Media \u203A unlabeled key (row 2, column 2): x');
  assert.equal(say('desktops[1].options.filter: both'), 'Clock: both');
  assert.equal(say('settings.volume must be an integer'), 'settings.volume must be an integer');
  assert.equal(say('desktops[7]: gone'), 'desktops[7]: gone');
});

test('hold and repeat are exclusive, and switching clears the other', () => {
  const key = { label: 'Vol', action: { type: 'hotkey', keys: 'volume_up' } };
  assert.equal(M.holdMode(key), 'none');
  M.setHoldMode(key, 'repeat');
  assert.deepEqual([M.holdMode(key), key.repeat, key.hold], ['repeat', true, undefined]);
  M.setHoldMode(key, 'action');
  assert.equal(M.holdMode(key), 'action');
  assert.equal(key.repeat, undefined);
  assert.equal(key.hold.type, 'hotkey');
  const chosen = key.hold;
  M.setHoldMode(key, 'action');                // choosing it again keeps what was set up
  assert.equal(key.hold, chosen);
  M.setHoldMode(key, 'none');
  assert.deepEqual(key, { label: 'Vol', action: { type: 'hotkey', keys: 'volume_up' } });
});

test('state keys: the source, the look and what the board receives', () => {
  const key = { label: 'Mute', icon: 'mute', action: { type: 'hotkey', keys: 'volume_mute' } };
  assert.equal(M.stateKind(key), 'none');
  M.setStateKind(key, 'mute');
  assert.deepEqual([M.stateKind(key), key.state], ['mute', { source: 'mute' }]);
  assert.deepEqual(M.stateLook(key), { color: '#B3261E', icon: 'mute', label: 'Mute' });   // red unless told otherwise
  key.state.color = '#112233';
  key.state.label = 'Muted';
  assert.deepEqual(M.stateLook(key), { color: '#112233', icon: 'mute', label: 'Muted' });
  M.setStateKind(key, 'app', ' OBS64.exe ');
  assert.deepEqual([M.stateKind(key), M.stateApp(key), key.state.color], ['app', 'obs64', '#112233']);  // the look survives a change of source
  delete key.state.color;
  assert.equal(M.stateLook(key).color, '#2EA043');                                           // green for a running program
  M.setStateKind(key, 'app', '');
  assert.equal(M.stateApp(key), 'obs64');                                                    // never an empty program name
  M.setStateKind(key, 'none');
  assert.equal(key.state, undefined);

  const deck = { desktops: [{ name: 'D', pages: [{ id: 'r', title: 'T', cols: 2, rows: 1, keys: [
    { label: 'Mute', action: { type: 'lock' }, hold: { type: 'lock' }, state: { source: 'mic_mute', icon: 'mute', label: 'Off' } },
    { label: 'Vol', action: { type: 'lock' }, repeat: true },
  ] }] }] };
  const [a, b] = M.boardLayout(deck).desktops[0].pages[0].keys;
  assert.deepEqual(a, { label: 'Mute', hold: true, on: { color: '#B3261E', icon: 'mute', label: 'Off' } });   // no actions, no source
  assert.deepEqual(b, { label: 'Vol', repeat: true });
});

test('http and obs actions: defaults, descriptions, headers and JSON bodies', () => {
  assert.deepEqual(M.defaultAction('http'), { type: 'http', method: 'GET', url: 'http://localhost:8080/' });
  assert.deepEqual(M.defaultAction('obs'), { type: 'obs', request: 'ToggleRecord' });
  assert.ok(M.ACTION_TYPES.includes('http') && M.ACTION_TYPES.includes('obs'));
  assert.equal(M.describeAction({ type: 'http', method: 'post', url: 'https://h.example/api/x?token=SECRET' }), 'POST https://h.example/api/x');
  assert.equal(M.describeAction({ type: 'http', url: 'http://a.b/' }), 'GET http://a.b/');
  assert.equal(M.describeAction({ type: 'obs', request: 'ToggleStream' }), 'OBS: ToggleStream');

  assert.deepEqual(M.parseHeaders('Authorization: Bearer abc:def\n  X-Key :  1  \n\nnocolon\n: empty name\r\nAccept: */*'),
    { Authorization: 'Bearer abc:def', 'X-Key': '1', Accept: '*/*' });   // everything after the first colon is the value
  assert.deepEqual(M.parseHeaders(''), {});
  assert.equal(M.joinHeaders({ A: '1', B: 'two' }), 'A: 1\nB: two');
  assert.deepEqual(M.parseHeaders(M.joinHeaders({ Authorization: 'Bearer ${HA_TOKEN}' })), { Authorization: 'Bearer ${HA_TOKEN}' });

  assert.deepEqual(M.parseJsonBody('{"a": [1, 2]}'), { ok: true, value: { a: [1, 2] } });
  assert.deepEqual(M.parseJsonBody('[1]'), { ok: true, value: [1] });
  for (const bad of ['', '{', '5', '"text"', 'null', 'true']) assert.equal(M.parseJsonBody(bad).ok, false, bad);

  assert.equal(M.obsRequestInfo('SetCurrentProgramScene').fields[0][0], 'sceneName');
  assert.equal(M.obsRequestInfo('ToggleRecord').fields, undefined);
  assert.equal(M.obsRequestInfo('SomethingElse'), null);
  assert.equal(new Set(M.OBS_REQUESTS.map((r) => r.id)).size, M.OBS_REQUESTS.length);
});

test('http and obs keys are sent to the board without their actions', () => {
  const deck = { desktops: [{ name: 'D', pages: [{ id: 'r', title: 'T', cols: 2, rows: 1, keys: [
    { label: 'Hook', action: { type: 'http', url: 'http://x.y/', headers: { A: 'secret' } } },
    { label: 'Rec', action: { type: 'obs', request: 'ToggleRecord' } },
  ] }] }] };
  const text = JSON.stringify(M.boardLayout(deck));
  assert.ok(!text.includes('secret') && !text.includes('ToggleRecord') && !text.includes('http://'));
});

test('mixer and clock desktops: adding, options, and what the board receives', () => {
  const deck = { desktops: [{ name: 'K', pages: [{ id: 'r', title: 'K', cols: 4, rows: 3, keys: [] }] }] };
  assert.equal(M.addDesktop(deck, 'mixer'), 1);
  assert.equal(M.addDesktop(deck, 'mixer'), null);       // one of each
  assert.equal(M.addDesktop(deck, 'clock'), 2);
  assert.equal(M.addDesktop(deck, 'clock'), null);
  assert.deepEqual(deck.desktops.slice(1).map((d) => [d.name, d.kind]), [['Mixer', 'mixer'], ['Clock', 'clock']]);

  assert.deepEqual(M.liveOptions({ kind: 'mixer' }), { apps: [] });
  assert.deepEqual(M.liveOptions({ kind: 'mixer', options: { apps: ['a', 'b', 'c', 'd', 'e'] } }).apps, ['a', 'b', 'c', 'd']);
  assert.deepEqual(M.liveOptions({ kind: 'clock' }), { hour12: false, seconds: false, timer: true, weather: true, units: 'c', place: null });
  const place = { name: 'Oslo', lat: 59.9139, lon: 10.7522 };
  const clock = { kind: 'clock', options: { hour12: true, units: 'f', place } };
  assert.deepEqual(M.liveOptions(clock), { hour12: true, seconds: false, timer: true, weather: true, units: 'f', place });

  const sent = M.boardLayout({ desktops: [{ name: 'Mix', kind: 'mixer', options: { apps: ['spotify'] } }, { name: 'Clock', ...clock },
    { name: 'NoPlace', kind: 'clock' }, { name: 'Off', kind: 'clock', options: { weather: false, place } }] }).desktops;
  assert.deepEqual(sent[0], { name: 'Mix', kind: 'mixer', options: {} });                          // the pins stay on the PC
  assert.deepEqual(sent[1].options, { hour12: true, seconds: false, timer: true, weather: true });
  assert.equal(sent[2].options.weather, false);                                                    // on, but no place chosen
  assert.equal(sent[3].options.weather, false);
  assert.ok(!JSON.stringify(sent).includes('59.9') && !JSON.stringify(sent).includes('Oslo'));       // where you are stays on the PC
  assert.equal(M.placeText(place), 'Oslo (59.9139, 10.7522)');
  assert.equal(M.placeText(null), 'no place chosen');
  assert.equal(M.foundPlaceText({ name: 'Paris', region: 'Texas', country: 'United States' }), 'Paris, Texas, United States');
  assert.equal(M.foundPlaceText({ name: 'Oslo', region: '', country: 'Norway' }), 'Oslo, Norway');
});
