---
name: Swipedeck editor
description: A quiet light-grey table with the board on it, and one orange for what you are editing.
colors:
  ground: "#e7e7e4"
  bar: "#efefec"
  panel: "#f7f7f5"
  raised: "#fdfdfc"
  field: "#ffffff"
  soft: "#dcdcd8"
  line: "#d6d6d2"
  line-strong: "#c3c3be"
  graphite: "#1c1d1e"
  muted: "#55575a"
  signal-orange: "#e8641b"
  good: "#2c6a45"
  warn: "#85560a"
  bad: "#a82a20"
  ground-dark: "#19191b"
  panel-dark: "#212224"
  graphite-dark: "#ececea"
  signal-orange-dark: "#f27a30"
  board: "#0b1220"
typography:
  headline:
    fontFamily: "Hanken Grotesk, Segoe UI, system-ui, sans-serif"
    fontSize: "24px"
    fontWeight: 700
    letterSpacing: "-0.01em"
  title:
    fontFamily: "Hanken Grotesk, Segoe UI, system-ui, sans-serif"
    fontSize: "17px"
    fontWeight: 700
  body:
    fontFamily: "Hanken Grotesk, Segoe UI, system-ui, sans-serif"
    fontSize: "14px"
    fontWeight: 400
    lineHeight: 1.4
  label:
    fontFamily: "Hanken Grotesk, Segoe UI, system-ui, sans-serif"
    fontSize: "13px"
rounded:
  field: "10px"
  card: "16px"
  device: "30px"
  pill: "999px"
spacing:
  xs: "4px"
  sm: "8px"
  md: "14px"
  lg: "20px"
components:
  button:
    backgroundColor: "{colors.raised}"
    textColor: "{colors.graphite}"
    rounded: "{rounded.pill}"
    height: "34px"
  button-primary:
    backgroundColor: "{colors.signal-orange}"
    textColor: "#17120e"
    rounded: "{rounded.pill}"
  field:
    backgroundColor: "{colors.field}"
    textColor: "{colors.graphite}"
    rounded: "{rounded.field}"
    height: "36px"
  switch-on:
    backgroundColor: "{colors.graphite}"
    textColor: "{colors.field}"
    rounded: "{rounded.pill}"
---

# Design System: Swipedeck editor

## Overview

**Creative North Star: "The board on the table"**

A matte, quiet shell after Braun product design under Dieter Rams. The board is the one dark object in a light room; the
controls around it are flat, aligned and sparing. The dark theme turns the table graphite and changes nothing else.

**Key Characteristics:**
* The preview is the largest thing on screen and keeps the board's own colors in both themes.
* One signal orange, used rarely.
* Sections are separated by hairlines and space, not boxes.
* Pill buttons and switches; softly rounded fields.

## Colors

Warm-neutral greys and graphite with one signal orange. Values for the dark theme are in `style.css` under
`prefers-color-scheme: dark`; the theme follows the system.

### Primary
- **Signal orange** (`signal-orange`): the Save button, the Undo button in a message, the ring around the key being edited,
  the current page in the desktop list, keyboard focus.

### Neutral
- **Table** (`ground`), **bar** and **panel**: the three layers of the shell. **Raised** is the selected desktop and
  buttons; **soft** is a switch's track.
- **Graphite** (`graphite`): text, the selected side of a switch, checkboxes and sliders.

### Named Rules
**The One Orange Rule.** Orange means "this is what you are editing" or "do it". Checkboxes, sliders, chips and
decoration are graphite.

## Typography

**Font:** Hanken Grotesk, bundled (SIL Open Font License), with Segoe UI as fallback. The board preview and preset tiles
use Segoe UI, as before, so they do not change with the shell.

### Hierarchy
- **Headline** (700, 24px): the Settings page.
- **Title** (700, 17px): the panel's subject (the key's name, "Desktop").
- **Section** (700, 15px): Look, Action, Presets, Desktops.
- **Body** (400, 14px). **Label** (400, 13px, muted): field names and hints.

## Layout

A 64px top bar, then three columns: desktops (252px), the board with its tray below, and a panel (384px). The tray is
`clamp(212px, 31vh, 372px)` tall (196px on short windows). Those three sizes can be changed by dragging the edges, which
show an orange line under the pointer. The board is drawn at 2x and scaled down to fit. Below 1280px
the side columns narrow; below 1024px everything stacks. Live desktops have no tray; their settings fill the panel.

## Elevation & Depth

Flat by default. Shadows only for things that sit above the table: the board, the selected desktop, floating menus and
messages. Never a border and a wide shadow on the same element.

## Shapes

Pills for buttons, switches, chips and messages; 10px fields; 16px cards and menus; the board's bezel is 30px.

## Components

### Buttons
Pill, 34px (28px small). Quiet variant for history and secondary actions, icon variant for rows. Discard changes is
outlined, next to the filled Save. Icons are authored SVG
on a 24px grid with a 2px round stroke; the board's own icons come from its font.

### Switches
A soft track with the chosen side raised, or filled graphite for the panel's Key / Desktop switch.

### Fields
White (near-black in dark) with a hairline; focus is a 2px orange outline.

### Navigation
Top-bar tabs underlined in graphite. Desktops are listed with a picture of their screen; their pages appear under the
selected one and as tabs above the board. Row actions appear on hover or keyboard focus.

### Renaming in place
A name with a small pencil turns into a box where it stands: the key's name at the top of its panel, and the desktop or
page name on the board preview. Enter or leaving keeps it, Escape takes it back. There are no separate name fields.

### Key head
A picture of the key and its name. No key number, page name or kind: the section below says Action or Folder, and an empty
key offers to add either one.

### Dragging to reorder
The place a dragged row will land is a 3px orange line in the gap between two rows, never a colored edge of a row.

### Folding sections
Less used parts (Hold, Change look when) are folded and say their current value while closed.

### Preset tile
The same proportion as a key, in the key's color, so a preset looks like what it becomes. Dragging one marks every key as
a place to drop it; a key it lands on settles into place.

## Do's and Don'ts

### Do:
- **Do** keep orange for the primary action and the thing being edited.
- **Do** keep the board preview faithful to the firmware in both themes.
- **Do** give every drag a click or keyboard way.

### Don't:
- **Don't** box a section inside another box.
- **Don't** use symbols or emoji as interface icons.
- **Don't** add a web font or script from another origin: the editor allows only its own.
