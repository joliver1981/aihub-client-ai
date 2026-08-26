# Round 2 Addendum — subtle · clean · dual-theme (concepts #13–#22)

Round 2 exists because of James's feedback on round 1: *"some designs were neat
but still have not found what I am looking for… I need something that works with
both a light and dark mode. Something a little more subtle and clean and clear
for people to visually easily know what to focus on."*

Everything in DESIGN_BRIEF.md still applies (same fixed IA, same six sections,
same exact demo data, single self-contained file, no external requests, working
nav, reduced-motion guards, corner badge linking to ./index.html). Round 2 adds
three hard requirements:

## 1. Dual theme — light AND dark, both first-class

- Implement BOTH themes with CSS custom properties. Default follows
  `prefers-color-scheme`; a visible, elegant theme toggle in the chrome flips it
  live (set `data-theme="light|dark"` on `<html>`; `:root[data-theme=…]`
  overrides must beat the media-query default in both directions).
- Both themes must be DESIGNED, not inverted: tuned surface steps, borders,
  shadows (shadows in light ≈ subtle; in dark prefer edge-lighting/elevation via
  surface tint), chart strokes, tag tints, focus rings. Test every section in
  both. Nothing may become illegible or muddy in either mode.
- Keep contrast accessible: body text ≥ WCAG AA against its surface in both themes.

## 2. Subtle, clean, restrained

- Quiet palettes: neutral chrome, few accents, no saturated floods, no heavy
  texture, no loud gradients. Whitespace and type hierarchy do the work.
- Corners, borders, shadows: refined and consistent (pick one radius scale, one
  border weight, one shadow scale — no mixing).
- Motion: sparse, fast (≤250ms), purposeful; never decorative loops (a gentle
  agent-presence pulse is allowed if quiet).
- Type: system stacks, disciplined scale (3–4 sizes + 1 micro-label style),
  restrained weights. Tabular numerals for data.

## 3. Focus clarity — the eye must always know where to go

This is the design problem round 2 is judged on. Each concept has a stated
FOCUS MECHANISM (its thesis) and must demonstrably deliver:

- ONE unmistakable primary focus per screen (e.g. in My Work: the selected
  item's decision actions; in Assistant: the newest agent answer + input).
- Pending-on-you items (the 4 work items, the approve/deny buttons, the badge)
  must visually outrank everything decorative.
- Secondary chrome (platform links, metadata, timestamps) must clearly recede.
- Squint test: blur your eyes — the important thing should still be findable by
  contrast/weight/position alone in BOTH themes.

## Practical notes

- Files are numbered #13–#22, e.g. `13_halo.html`. Corner badge format
  unchanged (`#13 · Halo`), linking to ./index.html.
- The theme toggle is part of the concept — style it; don't bolt on a checkbox.
- Charts must re-skin per theme (no hardcoded dark-only strokes).
- Verb tags (Approval / Email / Question / FYI) still need distinct treatments,
  but subtle ones — tint + label, not neon.
