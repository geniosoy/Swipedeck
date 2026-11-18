// Run: node --test companion/tests/js
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import * as M from '../../streamdeck_companion/web/model.js';

const deckFile = new URL('../../deck.json', import.meta.url);
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
  assert.deepEqual(layout.desktops[1], { name: 'Playing', kind: 'nowplaying' });
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
