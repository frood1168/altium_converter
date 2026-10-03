"""Track widths per layer, length-weighted, split into diff-pair and single-ended nets.

The single-ended widths that carry most of a layer's signal length are the
other half of the impedance-profile inputs; wide widths on few nets are power.
"""

from __future__ import annotations

from collections import defaultdict

import numpy as np

from ..model import Board
from .stats import weighted_modes


def analyse(board: Board, pair_nets: set[str], progress=print) -> dict:
    m = board.model
    acc: dict[tuple[str, bool], dict] = defaultdict(lambda: {"w": [], "l": [], "net": []})
    zone_nets = {z["net"] for z in m["zones"] if z["net"] > 0}
    for lay, n, w, x1, y1, x2, y2 in m["tracks"]:
        if n <= 0:
            continue
        a = acc[(lay, board.net_name(n) in pair_nets)]
        a["w"].append(w); a["l"].append(((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5); a["net"].append(n)
    out = {}
    for layer in board.layers:
        rec = {}
        for diff in (False, True):
            a = acc.get((layer, diff))
            if not a or not a["w"]:
                continue
            w, l, net = np.array(a["w"]), np.array(a["l"]), np.array(a["net"])
            rows = []
            for width, mass in weighted_modes(w, l, top=8):
                sel = np.isclose(w, width, atol=5e-5)
                nets = np.unique(net[sel])
                power = sum(1 for x in nets if x in zone_nets)
                rows.append({"width_mm": width, "length_mm": round(mass, 1),
                             "share": round(mass / float(l.sum()), 3), "nets": int(len(nets)),
                             "nets_with_planes": int(power),
                             "examples": [board.net_name(int(x)) for x in nets[:4]]})
            rec["diff_pair" if diff else "single_ended"] = rows
        out[layer] = rec
    progress(f"  widths: {len(out)} layers")
    return out
