# demo-ri-nervous-system — agent rules

The rules in `../AGENTS.md` govern this directory; this file only adds to
them. Build order: claim → beat script → components on the showcase →
port the winners here → `just check`.

- The claim: **Three small devices, one tiny model each, and a few bytes between them are enough to let a room full of people fly a drone without a data centre** — every element on screen serves it or gets cut.
- Palette: stamped **dark** (declared as `color-scheme` in
  `dashboard/styles.css`). Neither dark nor light is the default — palette
  follows the subject and the host it lives in (`../AGENTS.md` → Theme). If
  this demo should be the other one, re-stamp with `--palette` rather than
  hand-flipping colours; the linter checks the ground agrees with the
  declaration.
- Design language: `../_demo-kit/DESIGN_SYSTEM.md`. Re-hue the tokens for this
  subject; keep the token names.
- Every quantitative claim cites `docs/RESEARCH.md` or is introduced as
  representative.
