# altium_converter — codebase notes

See README.md for the pipeline and the fix table. Durable notes about the *work* live in
`C:\Workspace\gito\_knowledge\altium-tools\` (finding: `allegro-kicad-altium-translation.md`), not here.

## Two interpreters

- `kicad_side/*.py` run under **KiCad's bundled Python** (`pcbnew`). Stdlib only. They import
  `kicad_text` by putting the package directory on `sys.path` — keep `kicad_text` free of package
  imports and third-party deps.
- Everything else runs in the uv env (Python 3.12, shapely, numpy). Never import `pcbnew` there.

## Rules learned the hard way

- **Edit board text, not boards.** pcbnew's `SaveBoard` re-derives via nets from touching copper,
  and not repeatably. Any fix applied through pcbnew then saved moves things. Survey with pcbnew
  (read-only), write with `kicad_text`.
- **A KiCad assert opens a hidden modal dialog and blocks forever at ~0 CPU.** Boolean ops on
  arc-carrying polygons assert: call `ClearArcs()` on a copy first. KiCad 10 also blocks loading a
  format-20241229 file whose zones carry `filled_polygon`. Always run kicad_side with a timeout.
- **Altium's KiCad importer is KiCad 6-era**: unknown tokens are warnings (harmless), but layers
  are mapped by the *number* in the layer table, `stroke` is skipped, nets on `gr_poly` are
  ignored, and a collinear arc overflows Int32 and empties the whole import.
- **Altium's pour order is the zone `(priority N)`, ascending** — priority 0 gets the lowest `POURINDEX`,
  pours first and wins: the reverse of KiCad, where the higher number wins. File order does nothing
  (two imports written in opposite orders both ranked POURINDEX against priority at Spearman +1.0). So the
  Altium copies carry each copper zone's `pour_order` rank as its priority (`altium_priority`). Two wrong
  theories came before this one (file order; then file order reversed): test a cause on two inputs that
  differ in it alone.
- **Altium keeps only a zone outline's `(xy)` points** — arc-only outlines vanish, mixed ones lose their rounded
  parts and the vias in them. Copper outline arcs are written as points (`linearize_outline_arcs`); KiCad 10 writes
  an arc over five lines, so never match arcs line by line.
- Altium reads zone `min_thickness` as *Remove necks*, and KiCad's default thermal `connect_pads` as 0.5 mm reliefs:
  both are rewritten in the Altium copies (`ALTIUM_MIN_THICKNESS_MM`, `connect_pads yes`).
- Units: mm everywhere; mil only in report columns.

## Tests

`uv run python -m pytest` — `kicad_text` transforms on synthetic s-expressions, and the constraint
statistics/diff-pair detection on a synthetic model. Real boards stay in `assets/Test/` (ignored).
