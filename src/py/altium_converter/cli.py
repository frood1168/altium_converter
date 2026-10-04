"""Command-line interface for altium_converter.

Subcommands:
    convert       Allegro .brd / other kicad-cli import / .kicad_pcb -> KiCad 10 + Altium-ready KiCad copies.
    dump          Board -> neutral geometry model (.board.json.gz) for analysis.
    constraints   Model (or board) -> observed constraints (.constraints.json + .md).
    rules         constraints.json -> altium_drc master TOML + altium_netclass TOML.
    check-layers  Which Altium layer each KiCad layer's tracks landed on in an imported PcbDoc.
    drc           Run kicad-cli DRC (optionally re-pouring zones) to JSON.
    drc-compare   Unconnected items per net: reference DRC JSON vs candidate.
"""

from __future__ import annotations

import argparse
import datetime
import json
import sys
from pathlib import Path

from . import kicad_env


def _stamp() -> str:
    return datetime.datetime.now().strftime("%y%m%d_%H%M%S")


def cmd_convert(a) -> int:
    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    name = a.name or Path(a.board).stem
    return kicad_env.run_side("convert_board.py", Path(a.board).resolve(), out.resolve(), name, timeout=a.timeout)


def _dump(board: Path, dst: Path, timeout) -> Path:
    code = kicad_env.run_side("dump_board.py", board.resolve(), dst.resolve(), timeout=timeout)
    if code:
        sys.exit(f"dump failed ({code})")
    return dst


def cmd_dump(a) -> int:
    dst = Path(a.output or Path(a.board).with_suffix("").name + ".board.json.gz")
    _dump(Path(a.board), dst, a.timeout)
    return 0


def cmd_constraints(a) -> int:
    from . import constraints, model
    from .constraints import report

    src = Path(a.input)
    if src.suffix == ".kicad_pcb":
        model_path = Path(a.out_dir or src.parent) / f"{src.stem}.board.json.gz"
        print(f"dumping {src.name} ...")
        _dump(src, model_path, a.timeout)
        src = model_path
    m = model.load(src)
    sections = a.only.split(",") if a.only else constraints.SECTIONS
    result = constraints.run(m, sections)
    stem = src.name.replace(".board.json.gz", "").replace(".board.json", "")
    out_dir = Path(a.out_dir or src.parent)
    out_dir.mkdir(parents=True, exist_ok=True)
    base = out_dir / f"{stem}-constraints-{_stamp()}" if a.stamp else out_dir / f"{stem}.constraints"
    Path(f"{base}.json").write_text(json.dumps(result, indent=1, default=str), encoding="utf-8")
    Path(f"{base}.md").write_text(report.markdown(result), encoding="utf-8")
    print(f"wrote {base}.json and .md")
    return 0


def cmd_rules(a) -> int:
    from . import emit

    src = Path(a.constraints)
    c = json.loads(src.read_text(encoding="utf-8"))
    names = None
    if a.layers_from:
        from . import verify

        names = verify.pcbdoc_copper_layer_names(a.layers_from, len(c["layers"]))
        print("target layers: " + ", ".join(f"{s}->{t}" for s, t in zip(c["layers"], names)))
    em = emit.build(c, tol=a.tol, min_coupled=a.min_coupled, layer_names=names)
    stem = src.name.removesuffix(".json").removesuffix(".constraints")
    out_dir = Path(a.out_dir or src.parent)
    out_dir.mkdir(parents=True, exist_ok=True)
    board = Path(str(c.get("source") or stem)).name
    rules_path = out_dir / f"{stem}.rules.toml"
    nets_path = out_dir / f"{stem}.netclass.toml"
    rules_path.write_text(emit.master_toml(em, board, datetime.date.today().isoformat()), encoding="utf-8")
    nets_path.write_text(emit.netclass_toml(em, board), encoding="utf-8")
    kinds: dict[str, int] = {}
    for r in em.rules:
        kinds[r.kind] = kinds.get(r.kind, 0) + 1
    print(f"{len(em.rules)} rules ({', '.join(f'{k} {n}' for k, n in sorted(kinds.items()))}), "
          f"{len(em.classes)} net classes")
    print(f"wrote {rules_path}")
    print(f"wrote {nets_path}")
    if em.notes:
        print("not carried over:")
        print(emit.notes_text(em))
    return 0


def cmd_check_layers(a) -> int:
    from . import verify

    bad = 0
    print(f"{'kind':6} {'KiCad layer':12} {'count':>7}  {'expected':14} {'there':>7}  same count on")
    for r in verify.layer_counts(a.pcbdoc, a.kicad_pcb):
        bad += not r["ok"]
        print(f"{r['kind']:6} {r['kicad']:12} {r['count']:7}  {r['expected']:14} {r['altium_there']:7}  "
              f"{'ok' if r['ok'] else ', '.join(r['same_count_on']) or 'MISSING'}")
    print("all layers where expected" if not bad else f"{bad} layer(s) not where expected")
    return 1 if bad else 0


def cmd_drc(a) -> int:
    import subprocess

    args = [str(kicad_env.kicad_cli()), "pcb", "drc", "--format", "json", "--severity-all", "-o", a.output]
    if a.refill:
        args.append("--refill-zones")
    return subprocess.call(args + [a.board])


def cmd_drc_compare(a) -> int:
    from . import verify

    r = verify.drc_compare(a.reference, a.candidate)
    print(f"unconnected: reference {r['reference']['unconnected']} in {r['reference']['nets']} nets | "
          f"candidate {r['candidate']['unconnected']} in {r['candidate']['nets']} nets")
    for label in ("worse", "better"):
        rows = r[label]
        print(f"{label}: {len(rows)} nets")
        for x in rows[: a.top]:
            print(f"  {x['net']:32} {x['reference']:5} -> {x['candidate']:5}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="altium-converter", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("convert", help="board -> KiCad 10 + Altium-ready KiCad copies")
    s.add_argument("board"); s.add_argument("out_dir")
    s.add_argument("--name", help="output name prefix (default: board file stem)")
    s.add_argument("--timeout", type=float, default=1800)
    s.set_defaults(fn=cmd_convert)

    s = sub.add_parser("dump", help="board -> neutral geometry model")
    s.add_argument("board"); s.add_argument("-o", "--output")
    s.add_argument("--timeout", type=float, default=1800)
    s.set_defaults(fn=cmd_dump)

    s = sub.add_parser("constraints", help="observed constraints from a model or a .kicad_pcb")
    s.add_argument("input", help=".board.json.gz from `dump`, or a .kicad_pcb (dumped first)")
    s.add_argument("-d", "--out-dir")
    s.add_argument("--only", help=f"comma list of sections ({','.join(('diffpairs', 'widths', 'vias', 'clearances', 'planes'))})")
    s.add_argument("--stamp", action="store_true", help="name outputs <stem>-constraints-YYMMDD_HHMMSS")
    s.add_argument("--timeout", type=float, default=1800)
    s.set_defaults(fn=cmd_constraints)

    s = sub.add_parser("rules", help="constraints.json -> altium_drc master TOML + altium_netclass TOML")
    s.add_argument("constraints", help=".constraints.json from `constraints`")
    s.add_argument("-d", "--out-dir")
    s.add_argument("--tol", type=float, default=0.02,
                   help="relative min/max band around observed diff-pair width and gap (default 0.02)")
    s.add_argument("--min-coupled", type=float, default=2.0,
                   help="ignore a pair's geometry on a layer with less coupled run than this, mm (default 2)")
    s.add_argument("--layers-from", metavar="PCBDOC",
                   help="name OnLayer scopes after this imported PcbDoc's copper layers (matched by stack order)")
    s.set_defaults(fn=cmd_rules)

    s = sub.add_parser("check-layers", help="where each KiCad layer's tracks landed in an imported PcbDoc")
    s.add_argument("pcbdoc"); s.add_argument("kicad_pcb")
    s.set_defaults(fn=cmd_check_layers)

    s = sub.add_parser("drc", help="kicad-cli DRC to JSON")
    s.add_argument("board"); s.add_argument("output")
    s.add_argument("--refill", action="store_true", help="re-pour every zone first (what Altium does)")
    s.set_defaults(fn=cmd_drc)

    s = sub.add_parser("drc-compare", help="unconnected items per net: reference vs candidate DRC JSON")
    s.add_argument("reference"); s.add_argument("candidate")
    s.add_argument("--top", type=int, default=20)
    s.set_defaults(fn=cmd_drc_compare)

    a = p.parse_args(argv)
    return a.fn(a) or 0


if __name__ == "__main__":
    sys.exit(main())
