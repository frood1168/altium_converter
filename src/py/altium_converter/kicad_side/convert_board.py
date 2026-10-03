"""Convert a board to KiCad 10, keeping only the fabrication layers, plus Altium-ready copies.

Runs under KiCad's bundled Python (it needs ``pcbnew``); ``altium-converter
convert`` finds that interpreter and runs this for you.

Input: an Allegro ``.brd`` (imported with ``kicad-cli pcb import``; KiCad 10
reads Allegro 16-23 binaries without any Cadence software), any other format
``kicad-cli`` imports, or an existing ``.kicad_pcb``. Then:

- strips every item off the copper, silkscreen, solder/paste mask and outline
  layers (Allegro imports carry 80+ layers of assembly/dimension data);
- restores footprint reference designators and via nets from the importer's
  raw text (KiCad 10.0.3 clobbers refdes; pcbnew's save drops via nets);
- surveys zones: pour priority by area, nets for netless fills, empty zones.

Writes ``<name>-<YYMMDD_HHMMSS>.kicad_pcb`` (KiCad 10), ``-kicad9`` and
``-kicad9-nofill`` copies for Altium (whose reader is KiCad 6-era; see
``kicad_text``), the importer report and warnings, and a ``-layers.txt``
summary into ``out_dir``.

    <kicad>/bin/python.exe convert_board.py <in.brd|in.kicad_pcb> <out_dir> <name>
"""
import collections
import datetime
import os
import shutil
import subprocess
import sys
import tempfile

import pcbnew

# The shared text transforms live one level up, in the uv package.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from kicad_text import (MAX_ARC_RADIUS_MM, downgrade_to_kicad9, pin_via_nets,  # noqa: E402
                        plan_zones, raw_refdes, raw_via_nets)

# kicad-cli sits beside the python.exe running this script.
KICAD_CLI = os.path.join(os.path.dirname(sys.executable),
                         "kicad-cli.exe" if os.name == "nt" else "kicad-cli")
FAB_LAYERS = [pcbnew.F_SilkS, pcbnew.B_SilkS, pcbnew.F_Mask, pcbnew.B_Mask,
              pcbnew.F_Paste, pcbnew.B_Paste, pcbnew.Edge_Cuts]


def keep_set(board):
    """Layer ids to keep. A Python set: the SWIG LSET has no & operator."""
    return set(board.GetEnabledLayers().CuStack()) | set(FAB_LAYERS)


def to_lset(layers):
    lset = pcbnew.LSET()
    for layer in layers:
        lset.AddLayer(layer)
    return lset


def layers_of(item):
    return set(item.GetLayerSet().Seq())


def on_kept_layers(item, keep):
    return bool(layers_of(item) & keep)


def strip(board, keep):
    """Delete items off the kept layers; return per-layer counts of what went."""
    removed = collections.Counter()

    def note(item):
        removed[board.GetLayerName(item.GetLayer())] += 1

    doomed = [d for d in board.GetDrawings() if not on_kept_layers(d, keep)]
    for d in doomed:
        note(d)
        board.Delete(d)

    for z in list(board.Zones()):
        kept = layers_of(z) & keep
        if not kept:
            note(z)
            board.Delete(z)
        elif kept != layers_of(z):
            z.SetLayerSet(to_lset(kept))

    for fp in board.GetFootprints():
        silk = pcbnew.B_SilkS if fp.IsFlipped() else pcbnew.F_SilkS
        for g in [g for g in fp.GraphicalItems() if not on_kept_layers(g, keep)]:
            note(g)
            fp.Delete(g)
        for z in list(fp.Zones()):
            kept = layers_of(z) & keep
            if not kept:
                note(z)
                fp.Delete(z)
            elif kept != layers_of(z):
                z.SetLayerSet(to_lset(kept))
        # Fields (Reference, Value, ...) cannot be deleted. Park any on a
        # dropped layer, hidden, on the footprint's own silkscreen.
        for field in fp.GetFields():
            if field.GetLayer() not in keep:
                field.SetLayer(silk)
                field.SetVisible(False)
        for pad in fp.Pads():
            pad.SetLayerSet(to_lset(layers_of(pad) & keep))

    for t in board.GetTracks():
        if t.GetLayer() not in keep and t.GetClass() != "PCB_VIA":
            raise RuntimeError(f"track on non-copper layer {t.GetLayerName()}")
    return removed


def restore_refdes(board, refs):
    restored = 0
    for fp in board.GetFootprints():
        ref = refs.get(fp.m_Uuid.AsString())
        if ref:
            fp.SetReference(ref)
            restored += 1
    clobbered = sum(1 for fp in board.GetFootprints() if fp.GetReference().startswith("${"))
    if clobbered:
        raise RuntimeError(f"{clobbered} footprints still carry a placeholder refdes "
                           f"after restoring {restored}")
    return restored


def zone_plan(board):
    """Read-only survey of copper zones; the results are written into the text by uuid.

    - priority: every zone (and every netted copper polygon, which the Altium
      copies turn into zones) ranked by outline area, smallest pouring first.
      The importer leaves all at 0, so pour order is arbitrary; on this board a
      SYS_2V5 split sits wholly inside the UTIL_3V3 and GND planes. Allegro's
      dynamic shapes void round other nets whatever their order; smallest-first
      reproduces a plane with split islands.
    - nets: a netless zone whose Allegro fill touches one net (>= 95% of the
      netted vias, tracks and pads it covers) takes that net. The importer
      dropped GND from the TOP and BOTTOM floods.
    - empty: netless zones Allegro poured no copper into. Dropped from the
      Altium copies, where they would pour stray copper.
    """
    areas, nets, empty, ambiguous = {}, {}, set(), []
    for z in board.Zones():
        if z.GetIsRuleArea():
            continue
        uuid = z.m_Uuid.AsString()
        outline = pcbnew.SHAPE_POLY_SET(z.Outline())
        outline.ClearArcs()     # boolean ops on arcs assert, and the dialog blocks
        areas[uuid] = outline.Area()
        if z.GetNetCode():
            continue
        layer = z.GetFirstLayer()
        fill = pcbnew.SHAPE_POLY_SET(z.GetFilledPolysList(layer)) if z.IsFilled() else None
        if fill is None or fill.OutlineCount() == 0:
            empty.add(uuid)
            continue
        fill.ClearArcs()
        touch = collections.Counter()
        for t in board.GetTracks():
            if t.IsOnLayer(layer) and t.GetNetCode() and (
                    fill.Contains(t.GetPosition()) if t.GetClass() == "PCB_VIA"
                    else fill.Contains(t.GetStart()) or fill.Contains(t.GetEnd())):
                touch[t.GetNetname()] += 1
        for fp in board.GetFootprints():
            for p in fp.Pads():
                if p.GetNetCode() and p.IsOnLayer(layer) and fill.Contains(p.GetPosition()):
                    touch[p.GetNetname()] += 1
        top = touch.most_common(1)
        if top and top[0][1] >= 10 and top[0][1] >= 0.95 * sum(touch.values()):
            nets[uuid] = top[0][0]
        else:
            ambiguous.append((board.GetLayerName(layer), touch.most_common(3)))
    for d in board.GetDrawings():
        if (d.GetClass() == "PCB_SHAPE" and d.GetShape() == pcbnew.SHAPE_T_POLY
                and d.IsOnCopperLayer() and d.GetNetCode()):
            poly = pcbnew.SHAPE_POLY_SET(d.GetPolyShape())
            poly.ClearArcs()
            areas[d.m_Uuid.AsString()] = poly.Area()
    order = sorted(areas, key=lambda u: -areas[u])
    priority = {u: i for i, u in enumerate(order)}
    return {"priority": priority, "nets": nets, "empty": empty, "ambiguous": ambiguous}


def import_board(brd, raw, base):
    """Import a non-KiCad board (Allegro, PADS, Altium, ...) with kicad-cli."""
    run = subprocess.run(
        [KICAD_CLI, "pcb", "import", brd, "-o", raw,
         "--report-format", "text", "--report-file", base + "-import_report.txt"],
        capture_output=True, text=True)
    with open(base + "-import_warnings.log", "w", encoding="utf-8") as f:
        f.write(run.stdout + run.stderr)
    if run.returncode != 0 or not os.path.exists(raw):
        sys.exit(f"kicad-cli import failed ({run.returncode}); see {base}-import_warnings.log")


def main(brd, out_dir, name):
    stamp = datetime.datetime.now()
    tag = stamp.strftime("%y%m%d_%H%M%S")
    base = os.path.join(out_dir, f"{name}-{tag}")
    os.makedirs(out_dir, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        raw = os.path.join(tmp, "raw.kicad_pcb")
        if brd.lower().endswith(".kicad_pcb"):
            shutil.copyfile(brd, raw)
            for log in ("-import_report.txt", "-import_warnings.log"):
                with open(base + log, "w", encoding="utf-8") as f:
                    f.write("KiCad input: no import step.\n")
        else:
            import_board(brd, raw, base)

        refs = raw_refdes(raw)
        raw_vias = raw_via_nets(raw)
        board = pcbnew.LoadBoard(raw)
        restored = restore_refdes(board, refs)
        before =[board.GetLayerName(l) for l in board.GetEnabledLayers().Seq()]
        keep = keep_set(board)
        removed = strip(board, keep)
        board.SetEnabledLayers(to_lset(keep))
        board.SetVisibleLayers(to_lset(keep))
        after = [board.GetLayerName(l) for l in board.GetEnabledLayers().Seq()]
        plan = zone_plan(board)
        pcbnew.SaveBoard(base + ".kicad_pcb", board)
    vias_pinned = pin_via_nets(base + ".kicad_pcb", raw_vias)
    plan_zones(base + ".kicad_pcb", plan)
    nets, fixed = downgrade_to_kicad9(base + ".kicad_pcb", base + "-kicad9.kicad_pcb", plan)
    downgrade_to_kicad9(base + ".kicad_pcb", base + "-kicad9-nofill.kicad_pcb", plan, keep_fills=False)
    zone_nets = collections.Counter(plan["nets"].values())

    with open(base + "-layers.txt", "w", encoding="utf-8") as f:
        f.write(f"Compiled: {stamp:%Y-%m-%d %H:%M:%S}\nSource: {brd}\n\n")
        f.write(f"Restored {restored} of {len(board.GetFootprints())} footprint refdes "
                f"(importer leaves them as ${{REFERENCE}}).\n\n")
        f.write(f"Kept {len(after)} of {len(before)} layers:\n")
        f.writelines(f"  {n}\n" for n in after)
        f.write(f"\nRemoved {sum(removed.values())} items:\n")
        f.writelines(f"  {n:6}  {layer}\n" for layer, n in removed.most_common())
        f.write(f"\nVia nets restored from the importer's output: {vias_pinned} "
                f"(pcbnew's save drops some, unrepeatably).\n")
        f.write(f"\nZones: {len(plan['priority'])} given pour priority by area (smallest first); "
                f"nets inferred for {len(plan['nets'])} netless filled zones {dict(zone_nets)}; "
                f"{len(plan['empty'])} netless zones have no Allegro copper.\n")
        for layer, touch in plan["ambiguous"]:
            f.write(f"  ambiguous netless fill on {layer}: touches {touch}\n")
        f.write(f"\nAltium copies (-kicad9*) only: {fixed['arcs straightened']} near-straight arcs "
                f"made straight (radius > {MAX_ARC_RADIUS_MM} mm); {fixed['copper polygons zoned']} "
                f"netted copper polygons made zones; {fixed['empty netless zones dropped']} empty "
                f"netless zones dropped.\n")
    print(f"restored {restored} refdes; kept {len(after)}/{len(before)} layers, "
          f"removed {sum(removed.values())} items; {vias_pinned} via nets restored; "
          f"zone nets inferred {dict(zone_nets)}, ambiguous {len(plan['ambiguous'])}; "
          f"KiCad 9 copies have {nets} nets, {dict(fixed)}")
    print(base + ".kicad_pcb")


if __name__ == "__main__":
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    main(*sys.argv[1:])
