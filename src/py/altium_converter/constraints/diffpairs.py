"""Differential pairs: found by net name, measured along their coupled run.

A pair is two nets whose names differ only in a P/N (or +/-) marker. On each
layer the P traces are sampled every ``STEP_MM``; at each sample the nearest N
trace gives the edge-to-edge gap. Samples closer than ``COUPLED_MAX`` widths
count as coupled; width and gap are reported as length-weighted modes over the
coupled run, per pair and per *family* (the name with digits replaced by '#',
so PCIE_TX0_C_P..PCIE_TX15_C_P are one family). Those per-layer (width, gap)
combinations are the inputs an Altium impedance profile needs.
"""

from __future__ import annotations

import re
from collections import defaultdict

import numpy as np
import shapely

from ..model import Board
from .stats import weighted_modes

STEP_MM = 0.1
COUPLED_MAX = 3.0          # gap <= this many trace widths counts as coupled
COUPLED_CAP_MM = 0.6       # ... and never more than this

# (regex on the P net, how to build the N name); first match wins.
_PATTERNS = [
    (re.compile(r"^(?P<base>.*?)(?P<sep>[_\-]?)(?P<pn>P)(?P<idx>\d*)$"), "N"),
    (re.compile(r"^(?P<base>.*?)(?P<sep>[_\-]?)(?P<pn>p)(?P<idx>\d*)$"), "n"),
    (re.compile(r"^(?P<base>.*?)(?P<sep>)(?P<pn>\+)(?P<idx>)$"), "-"),
]


def find_pairs(nets: list[str], routed: set[str]) -> list[tuple[str, str, str]]:
    """[(base, p_net, n_net)] for nets whose partner exists and both are routed."""
    names = set(nets)
    pairs = []
    for name in sorted(names):
        for pat, other in _PATTERNS:
            m = pat.match(name)
            if not m:
                continue
            partner = f"{m['base']}{m['sep']}{other}{m['idx']}"
            if partner in names and name in routed and partner in routed:
                pairs.append((f"{m['base']}{m['sep']}{{P,N}}{m['idx']}", name, partner))
            break
    return pairs


def family(base: str) -> str:
    return re.sub(r"\d+", "#", base)


def analyse(board: Board, progress=print) -> dict:
    m = board.model
    by_net_layer: dict[tuple[int, str], list] = defaultdict(list)
    for t in m["tracks"]:
        if t[1] > 0:
            by_net_layer[(t[1], t[0])].append(t)
    routed = {board.net_name(n) for n, _ in by_net_layer}
    net_id = {name: i for i, name in enumerate(board.nets)}
    pairs = find_pairs(board.nets, routed)
    progress(f"  diff pairs: {len(pairs)} found by name")

    per_pair = []
    fam_layer: dict[tuple[str, str], dict] = defaultdict(lambda: {"w": [], "g": [], "len": [], "pairs": set()})
    for base, p_name, n_name in pairs:
        p, n = net_id[p_name], net_id[n_name]
        layers = sorted({l for (k, l) in by_net_layer if k == p})
        rec = {"pair": base, "p": p_name, "n": n_name, "layers": {}}
        for layer in layers:
            ps, ns = by_net_layer.get((p, layer), []), by_net_layer.get((n, layer), [])
            if not ps or not ns:
                continue
            n_lines = np.array([shapely.LineString([(t[3], t[4]), (t[5], t[6])]) for t in ns], dtype=object)
            n_w = np.array([t[2] for t in ns])
            pts, pw, plen = [], [], []
            for t in ps:
                x1, y1, x2, y2, w = t[3], t[4], t[5], t[6], t[2]
                L = ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5
                k = max(1, int(L / STEP_MM))
                f = (np.arange(k) + 0.5) / k
                pts.extend(zip(x1 + f * (x2 - x1), y1 + f * (y2 - y1)))
                pw.extend([w] * k); plen.extend([L / k] * k)
            pts = shapely.points(np.array(pts))
            pw, plen = np.array(pw), np.array(plen)
            tree = shapely.STRtree(n_lines)
            (qi, nj), dist = tree.query_nearest(pts, return_distance=True, all_matches=False)
            gap = np.full(len(pts), np.inf)
            gap[qi] = dist - pw[qi] / 2 - n_w[nj] / 2
            coupled = (gap >= 0) & (gap <= np.minimum(COUPLED_MAX * pw, COUPLED_CAP_MM))
            total = float(plen.sum())
            if not coupled.any():
                rec["layers"][layer] = {"length_mm": round(total, 2), "coupled_mm": 0.0}
                continue
            cw, cg, cl = pw[coupled], gap[coupled], plen[coupled]
            width = weighted_modes(cw, cl, top=1)[0][0]
            g = weighted_modes(cg, cl, top=1, bin_mm=0.0025)[0][0]
            rec["layers"][layer] = {"width_mm": width, "gap_mm": g, "length_mm": round(total, 2),
                                    "coupled_mm": round(float(cl.sum()), 2)}
            fl = fam_layer[(family(base), layer)]
            fl["w"].append(cw); fl["g"].append(cg); fl["len"].append(cl); fl["pairs"].add(base)
        if rec["layers"]:
            per_pair.append(rec)

    families = []
    for (fam, layer), d in sorted(fam_layer.items()):
        w, g, l = np.concatenate(d["w"]), np.concatenate(d["g"]), np.concatenate(d["len"])
        families.append({"family": fam, "layer": layer, "pairs": len(d["pairs"]),
                         "coupled_mm": round(float(l.sum()), 1),
                         "width_mm": weighted_modes(w, l, top=1)[0][0],
                         "gap_mm": weighted_modes(g, l, top=1, bin_mm=0.0025)[0][0],
                         "gap_modes": weighted_modes(g, l, top=3, bin_mm=0.0025)})

    # Impedance-profile candidates: distinct (layer, width, gap) by coupled length.
    combos: dict[tuple[str, float, float], dict] = defaultdict(lambda: {"coupled_mm": 0.0, "families": set()})
    for f in families:
        c = combos[(f["layer"], f["width_mm"], f["gap_mm"])]
        c["coupled_mm"] += f["coupled_mm"]; c["families"].add(f["family"])
    profiles = [{"layer": l, "width_mm": w, "gap_mm": g, "coupled_mm": round(c["coupled_mm"], 1),
                 "families": sorted(c["families"])}
                for (l, w, g), c in sorted(combos.items(), key=lambda kv: (board.layers.index(kv[0][0]), -kv[1]["coupled_mm"]))]
    return {"pairs": per_pair, "families": families, "profiles": profiles,
            "method": {"step_mm": STEP_MM, "coupled_max_widths": COUPLED_MAX, "coupled_cap_mm": COUPLED_CAP_MM}}
