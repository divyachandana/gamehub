# NightWatch brand

Original mark, drawn for this project. Offline only: plain SVG, no fonts, no external assets.

| File | Use |
| --- | --- |
| `logo.svg` | The mark. 64×64 viewBox, has a `<title>` for screen readers. Use it at 24 px and up. |
| `favicon.svg` | Same drawing without the title, for `<link rel="icon">`. |
| `apple-touch-icon.png` | 180×180 PNG for iOS / Android home screens (staff phones). Rendered from `logo.svg`. |

## What it shows

- **Doorway arch**: the exit NightWatch watches over. Elopement is about doors.
- **Crescent moon** inside the arch: the night shift.
- **Green light** where the handle would be: the one steady signal that all is well (matches `--nw-ok`).
- **Three bezel ticks**: a quiet watch face around the door. The floor line is the fourth side.

Colours come from the theme: `--nw-moon` #cdd9ff, `--nw-sun` #ffd27a, `--nw-ok` #3ddc97 on the
`--nw-surface` navy. It reads on dark backgrounds; on white, keep it on its own navy tile (it already is one).

## Adopting it on a page

```html
<link rel="icon" href="/assets/brand/favicon.svg" type="image/svg+xml">
<link rel="apple-touch-icon" href="/assets/brand/apple-touch-icon.png">
```

In the top bar, use it in place of the `NW.icon('logo')` glyph:

```html
<div class="nw-brand-mark" style="background:none;border:0"><img src="/assets/brand/logo.svg" alt="" width="38" height="38"></div>
```

(`alt=""` because the page name next to it already says "NightWatch".)

## Regenerating the PNG

On a Mac: `swift render.swift logo.svg apple-touch-icon.png 180 bg` (an 8-line AppKit script: `NSImage` loads
the SVG and draws it into a 180×180 bitmap). Any SVG rasteriser works; keep the navy background filled so
iOS doesn't put the mark on black.

Do not stretch, recolour per page, or add effects. Don't put it on medical documents as if it were a
certified device mark: NightWatch is a hackathon prototype.
