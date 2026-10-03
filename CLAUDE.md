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
- Units: mm everywhere; mil only in report columns.

## Tests

`uv run python -m pytest` — `kicad_text` transforms on synthetic s-expressions, and the constraint
statistics/diff-pair detection on a synthetic model. Real boards stay in `assets/Test/` (ignored).
