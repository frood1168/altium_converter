"""Via styles: span, drill, outer pad, inner pad, and how often inner pads are removed."""

from __future__ import annotations

from collections import Counter, defaultdict

import shapely

from ..model import Board


def analyse(board: Board, progress=print) -> dict:
    vias = board.model["vias"]
    if not vias:
        return {"styles": []}
    outer = {board.layers[0], board.layers[-1]}
    in_bga = board.in_bga(shapely.points([(v["x"], v["y"]) for v in vias]))
    styles = defaultdict(lambda: {"count": 0, "in_bga": 0, "inner_flashed": 0, "inner_total": 0, "nets": Counter()})
    for v, bga in zip(vias, in_bga):
        span = [l for l in board.layers if l in v["layers"]]
        through = span[0] == board.layers[0] and span[-1] == board.layers[-1]
        inner = [v["layers"][l] for l in span if l not in outer]
        key = ("through" if through else f"{span[0]}-{span[-1]}", v["drill"],
               max((v["layers"][l] for l in span if l in outer), default=0), max(inner, default=0))
        s = styles[key]
        s["count"] += 1
        s["in_bga"] += int(bga)
        s["inner_total"] += len(inner)
        s["inner_flashed"] += sum(1 for d in inner if d)
        s["nets"][board.net_name(v["net"])] += 1
    rows = [{"span": span, "drill_mm": drill, "outer_pad_mm": od, "inner_pad_mm": idia,
             "count": s["count"], "in_bga": s["in_bga"],
             "inner_pads_kept": round(s["inner_flashed"] / s["inner_total"], 3) if s["inner_total"] else None,
             "top_nets": s["nets"].most_common(3)}
            for (span, drill, od, idia), s in sorted(styles.items(), key=lambda kv: -kv[1]["count"])]
    progress(f"  vias: {len(rows)} styles")
    return {"styles": rows}
