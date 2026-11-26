# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

People who built a Swipedeck: the owner and others who flashed the board from the public repository. They sit at a Windows
PC with the board next to the keyboard. A first visit must make sense without reading the README; a daily visit ("change
that key") must be quick. Where the two pull apart, the first visit wins.

## Product Purpose

Swipedeck is a touch macro deck: a 2.8" ESP32-S3 board plus a PC companion. The web editor is where its owner decides what
the board shows and does: desktops to swipe between, pages and folders of keys, live desktops (Now playing, PC stats,
volume mixer, clock) and device settings. Success: someone changes a key and sees it on the board without having to work
out how the editor is organised.

## Positioning

The editor's preview is drawn with the board's own geometry and icon glyphs, so what is on screen is what the board shows.

## Operating Context

* Served by the companion on 127.0.0.1; opened in a desktop browser, with the pointer and keyboard.
* The board may be connected or not; editing works either way and a save reaches it when it is there.
* Most visits are about one key: pick it, give it a preset or an action, a label, an icon and a color, save.
  (Assumption from the owner's usage so far, not measured.)

## Capabilities and Constraints

* Plain HTML, CSS and JavaScript modules; no build step and no new runtime dependency.
* The content security policy allows only the editor's own origin: fonts, icons and images must be bundled.
* The board is 320 x 240; the preview shows it at 2x. Limits: 8 desktops, 16 pages per desktop, 6 x 5 keys, labels of 10
  characters. The board's fonts have no accents or emoji.
* Drag and drop stays: reordering desktops, swapping keys, dragging presets onto keys, ordering stats items, moving
  players between lists. Every drag also has a click or keyboard way.
* Features, behaviour and the current wording stay (the text was reviewed on 2026-11-25).
* The browser tests (`tools/e2e_editor.py`) drive the real DOM and must keep passing; they may be updated where the
  structure changes on purpose.

## Brand Commitments

* Name: Swipedeck. Logo: `assets/logo.svg`, unchanged.
* One name per thing: desktop, page, key, app, board, city. "Desktop" stays for now.

## Evidence on Hand

Real editor screenshots in `docs/images/`; the demo deck in `companion/tests/fixtures/deck.json`. No testimonials, user
research or usage numbers exist, and none may be invented.

## Product Principles

1. The board preview is the main thing: show the result, then the controls that change it.
2. Show what is needed for the current choice; keep the rest one step away.
3. Use patterns people already know before inventing one.
4. Nothing is lost by trying: edits can be undone.
5. Say it once, in the words a user would use.

## Accessibility & Inclusion

Keyboard shortcuts and click alternatives to dragging are existing features and stay.
