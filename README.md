# altium_converter

Translate boards from other tools into something Altium imports cleanly, and measure the
**observed design constraints** of a routed board: clearances, track widths, differential pairs,
via styles, plane antipads. The constraint numbers feed the sibling tools:
`altium_drc` (rules), `altium_netclass` (classes), `altium_stackup` / Layer Stack Manager
(impedance profiles).

Born from converting the AMD VCU118 Allegro board (2026-10-02). Every fix below was found on
that board and is explained where it is made.

## Pipeline

```
Allegro .brd ──kicad-cli import──▶ raw .kicad_pcb ──convert──▶ <name>-<stamp>.kicad_pcb        (KiCad 10)
(or any format kicad-cli imports,                             ├─ -kicad9-nofill.kicad_pcb       (Altium: import this)
 or a .kicad_pcb)                                             └─ -kicad9.kicad_pcb              (Altium, source fills kept; unverified)

.kicad_pcb ──dump──▶ .board.json.gz (neutral model, mm) ──constraints──▶ .constraints.json + .md
                                                          └──rules──▶ .rules.toml (altium_drc) + .netclass.toml (altium_netclass)
```

Anything that loads a board through KiCad runs under **KiCad's bundled Python** (that is the only
place `pcbnew` imports); the CLI finds it (`C:\Program Files\KiCad\<ver>\bin`, or set `KICAD_BIN`).
The analysis itself runs in this package's own environment (shapely + numpy), on the model only.

## Use

```powershell
uv sync --extra test

# Allegro (or other) board -> KiCad 10 + Altium-ready copies
uv run altium-converter convert board.brd out\ --name MYBOARD

# Observed constraints (dumps the board first if given a .kicad_pcb)
uv run altium-converter constraints out\MYBOARD-<stamp>.kicad_pcb -d out\
uv run altium-converter constraints board.board.json.gz --only diffpairs,widths

# After importing into Altium: did every layer land where it should?
uv run --extra verify altium-converter check-layers imported.PcbDoc out\MYBOARD-<stamp>-kicad9-nofill.kicad_pcb

# Would a re-pour (what Altium does) keep the nets connected? Compare against the source fills.
uv run altium-converter drc out\MYBOARD-<stamp>.kicad_pcb ref.json
uv run altium-converter drc out\MYBOARD-<stamp>-kicad9-nofill.kicad_pcb repour.json --refill
uv run altium-converter drc-compare ref.json repour.json
```

## What `convert` fixes, and why

| Problem (found on VCU118) | Fix | Where |
|---|---|---|
| Allegro imports carry 85 layers (assembly, dimensions, drawings) | Keep copper, silkscreen, solder/paste mask, outline | `kicad_side/convert_board.py` `strip` |
| KiCad 10.0.3 writes each refdes then `"${REFERENCE}"`; the loader keeps the last | Restore from the raw text by footprint uuid | `kicad_text.raw_refdes` |
| pcbnew's save re-derives via nets, **unrepeatably** (9–31 vias lost per save) | Rewrite via nets from the importer's raw text by uuid | `kicad_text.pin_via_nets` |
| Every zone imports at priority 0: pour order arbitrary (a 2.5 V split inside a 3.3 V plane disconnects) | Priority by area, smallest first | `zone_plan` / `kicad_text.plan_zones` |
| Importer drops the net from some fills (VCU118 TOP/BOTTOM GND floods) | Infer from what the fill touches (≥ 95 % one net) | `zone_plan` |
| Netless zones Allegro poured nothing into | Dropped from the Altium copies | `kicad_text.altium_block` |
| **Altium's KiCad reader is KiCad 6-era** — KiCad 10 files import as an empty board | Down-convert to format 20241229 with a numbered net table | `kicad_text.downgrade_to_kicad9` |
| …and it places items by layer **number**, read the KiCad 6 way; KiCad 9 renumbered | Rewrite the layer table with KiCad 6 numbers | `kicad_text.renumber_layers` |
| Collinear (sub-µm) arcs: Altium overflows Int32 deriving the centre and abandons the import | Arcs with radius > 1 m become segments | `kicad_text.altium_block` |
| Line widths in `(stroke (width))` are skipped; everything lands at 0.254 mm | Also write the KiCad 6 sibling `(width W)` | `downgrade_to_kicad9` |
| Netted copper `gr_poly` imports as no-net copper | Rewritten as zones on the same net | `kicad_text.altium_block` |

All text-level fixes edit the s-expression line by line, never through pcbnew, so nothing else moves.

## What `constraints` measures

All values are **observed** from copper — the command-line Allegro import carries no constraint
sets and no stackup. *rule est.* is the most common value in the tightest 10 % of observations: the
value the router was held to (the absolute minimum is usually an outlier).

- **diffpairs** — pairs by net name (`_P/_N`, `P/N`, `+/-`); width and gap as length-weighted modes
  over the coupled run, per pair, per family (`PCIE_TX#_C`), and as distinct (layer, width, gap)
  **impedance-profile inputs**.
- **widths** — per layer, the widths carrying most single-ended and pair length.
- **vias** — styles by span, drill, outer/inner pad, and how often inner pads are removed.
- **clearances** — nearest different-net object of each kind (Track, Via, Via hole, SMD Pad, TH Pad),
  edge to edge, per layer, **BGA field vs open board** (footprints with ≥ 100 pads).
- **planes** — from the source fills: antipads to other-net vias/pads (from pad and from drill),
  plane-to-track, same-net connection style (direct / relief + spoke count), plane-to-edge.

## Layout

```
src/py/altium_converter/
  cli.py            the altium-converter command
  kicad_env.py      find KiCad; run kicad_side scripts under its Python (with a hang timeout)
  kicad_text.py     text-level .kicad_pcb transforms (no pcbnew; unit-tested)
  model.py          the neutral board model + shapely views
  constraints/      diffpairs, widths, vias, clearances, planes, stats, report
  verify.py         check-layers (PcbDoc), drc-compare
  kicad_side/       convert_board.py, dump_board.py  -- run under KiCad's Python
tests/
assets/fixtures/    small committed fixtures; assets/Test/ is local and ignored
```

## `rules` — observed constraints into the sibling tools

```powershell
uv run altium-converter rules out\MYBOARD.constraints.json -d out\      # --tol 0.02 --min-coupled 2
uv run --extra verify altium-converter rules out\MYBOARD.constraints.json --layers-from imported.PcbDoc
```

Writes a master rules TOML (the `altium_drc` format: `master lint`, `netscope`, `workingset`, `merge`)
and a net class TOML (`altium_netclass`). Apply the net classes **first**: the pair rules are scoped to
them. Both carry the board's full net list (so `master lint` can tell a missing class from an unknown
one), which means `constraints.json` must come from a build that records `nets` — re-run `constraints`.

A differential pair is routed to a different width/gap per layer, and net classes are the only scope
handle the sibling tools can create, so the class key is the exact observed geometry:
`NC_DP_<layer>_W<width mil>_G<gap mil>`, and each rule is scoped
`InNetClass('<class>') And OnLayer('<layer>')`. A pair using two geometries on two layers sits in two
classes and each rule reaches only its own layer.

| Rule | From | Notes |
|---|---|---|
| `Width_DP_*`, `DiffPairsRouting_DP_*` | per-pair, per-layer coupled width and gap | min/max = ±`--tol` of observed; geometry with < `--min-coupled` mm of coupled run is ignored |
| `Width_<layer>`, `Width` | single-ended widths carrying ≥ 2 % of a layer's length | preferred = most common; the catch-all is last |
| `Clearance_BGA`, `Clearance` | tightest copper-to-copper estimate, BGA fields (`InComponent(...)`) vs open board | holes and pad-to-pad samples under 20 are excluded; one generic gap, not the object matrix |
| `RoutingVias` | through-hole styles with ≥ 10 vias | blind/buried styles are reported, not emitted |
| `PlaneClearance` | tightest open-board antipad **from the drill** | |

**Layer names:** `OnLayer(...)` is resolved by Altium against *its* stack, and an import renames the source's
layers (`03_SIG1` becomes `In2.Cu`). Without `--layers-from <imported.PcbDoc>` the scopes keep the source
names and match **nothing**, silently; the flag maps by stack order. (`altium-drc netscope` only knows
`TOP`/`MIDn`/`BOTTOM`, so it cannot confirm stack names: its "DEAD" there means "unknown name".)

**Not carried:** `PolygonConnect` (the master TOML has no field for connect settings — use the report's
table) and the per-object-pair clearance matrix. Each run prints what it left out.
Every value is an estimate of what the router was held to; review before merging.
⚠️ **Unverified in Altium:** that a `DiffPairsRouting` rule accepts an `InNetClass` scope (it is normally
scoped to a differential-pair class, which the sibling tools cannot create).
