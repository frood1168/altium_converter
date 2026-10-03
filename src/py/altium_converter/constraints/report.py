"""Render a constraints result as Markdown, organised by where each value goes in Altium."""

from __future__ import annotations

from .stats import mil

ALTIUM_KIND = {"Track": "Track", "Via": "Via", "Via hole": "Via (no pad: hole)", "SMD Pad": "SMD Pad",
               "TH Pad": "TH Pad"}


def _fmt(v):
    return "-" if v is None else f"{v:.4f}".rstrip("0").rstrip(".")


def _row(cells):
    return "| " + " | ".join(str(c) for c in cells) + " |"


def _stats_table(title, entries, label="Pair"):
    lines = [f"#### {title}", "",
             _row([label, "n", "min", "p1", "p5", "median", "**rule est. mm**", "mil"]),
             _row(["---"] * 8)]
    for k, s in entries.items():
        if not s.get("n"):
            continue
        lines.append(_row([k, s["n"], _fmt(s["min"]), _fmt(s["p1"]), _fmt(s["p5"]), _fmt(s["p50"]),
                           f"**{_fmt(s['rule_mm'])}**", mil(s["rule_mm"])]))
    return lines + [""]


def markdown(c: dict) -> str:
    L = [f"# Observed constraints — `{c.get('source')}`", "",
         f"Generated {c['generated']}. Values are **observed** from routed copper, not read from the "
         "source tool's rule set. *rule est.* is the most common value in the tightest 10% of "
         "observations: the value the router was held to. The absolute minimum is often an "
         "outlier. BGA fields: " + (", ".join(c["bga_fields"]) or "none") + ".", ""]

    dp = c.get("diffpairs", {})
    if dp.get("profiles"):
        L += ["## Impedance profile inputs (Layer Stack Manager)", "",
              "Differential: one row per distinct (layer, width, gap) by coupled length. Enter each as a "
              "differential transmission line on that layer; the stackup decides the impedance.", "",
              _row(["Layer", "Width mm", "Gap mm", "Width mil", "Gap mil", "Coupled mm", "Families"]),
              _row(["---"] * 7)]
        for p in dp["profiles"]:
            L.append(_row([p["layer"], _fmt(p["width_mm"]), _fmt(p["gap_mm"]), mil(p["width_mm"]),
                           mil(p["gap_mm"]), p["coupled_mm"], ", ".join(p["families"][:4])
                           + (" …" if len(p["families"]) > 4 else "")]))
        L.append("")
    w = c.get("widths", {})
    if w:
        L += ["Single-ended: the widths carrying most of each layer's non-pair signal length "
              "(nets with a plane on the board are flagged; they are usually power).", "",
              _row(["Layer", "Width mm", "mil", "Share of length", "Nets", "of which plane nets", "Examples"]),
              _row(["---"] * 7)]
        for layer, rec in w.items():
            for r in rec.get("single_ended", [])[:3]:
                L.append(_row([layer, _fmt(r["width_mm"]), mil(r["width_mm"]), f"{r['share']:.0%}",
                               r["nets"], r["nets_with_planes"], ", ".join(r["examples"][:3])]))
        L.append("")

    if dp.get("families"):
        L += ["## Differential pair routing (DiffPairsRouting rules)", "",
              f"{len(dp['pairs'])} pairs matched by name. Per family and layer:", "",
              _row(["Family", "Layer", "Pairs", "Width mm", "Gap mm", "Gap modes (mm: coupled mm)", "Coupled mm"]),
              _row(["---"] * 7)]
        for f in dp["families"]:
            modes = "; ".join(f"{g:g}: {m:.0f}" for g, m in f["gap_modes"])
            L.append(_row([f["family"], f["layer"], f["pairs"], _fmt(f["width_mm"]), _fmt(f["gap_mm"]),
                           modes, f["coupled_mm"]]))
        L.append("")

    if w:
        L += ["## Width rules (per layer)", "",
              _row(["Layer", "Narrowest common SE mm", "Most common SE mm", "Pair width mm"]), _row(["---"] * 4)]
        for layer, rec in w.items():
            se = rec.get("single_ended", [])
            pr = rec.get("diff_pair", [])
            if not se and not pr:
                continue
            L.append(_row([layer, _fmt(min((r["width_mm"] for r in se if r["share"] >= 0.02), default=None)),
                           _fmt(se[0]["width_mm"]) if se else "-", _fmt(pr[0]["width_mm"]) if pr else "-"]))
        L.append("")

    v = c.get("vias", {}).get("styles", [])
    if v:
        L += ["## Via styles (RoutingVias / hole size rules)", "",
              _row(["Span", "Drill mm", "Outer pad mm", "Inner pad mm", "Count", "In BGA", "Inner pads kept", "Top nets"]),
              _row(["---"] * 8)]
        for s in v[:12]:
            L.append(_row([s["span"], _fmt(s["drill_mm"]), _fmt(s["outer_pad_mm"]), _fmt(s["inner_pad_mm"]),
                           s["count"], s["in_bga"], "-" if s["inner_pads_kept"] is None else f"{s['inner_pads_kept']:.0%}",
                           ", ".join(f"{n} ({k})" for n, k in s["top_nets"])]))
        L.append("")

    cl = c.get("clearances", {})
    if cl:
        L += ["## Clearance (Clearance rule object matrix)", "",
              f"Nearest different-net object of each kind, edge to edge, up to {cl['search_mm']} mm. "
              "`A -> B` is measured from each A to its nearest B.", ""]
        L += _stats_table("Board-wide", cl["board"])
    pl = c.get("planes", {})
    if pl.get("antipad"):
        L += ["## Planes (polygon clearance, plane clearance, polygon connect)", "",
              "From the source's poured copper. *from pad* is copper-to-copper (Altium Clearance with "
              "Copper); *from drill* is hole-referenced (Altium Power Plane Clearance).", ""]
        L += _stats_table("Plane to other-net via / pad", pl["antipad"], "Layer | object")
        L += _stats_table("Plane to other-net track", pl["to_tracks"], "Layer")
        L += ["#### Same-net connection style (PolygonConnect)", "",
              "*direct*: solid all round. *relief*: spokes, then an air gap (counted, gap measured). "
              "*partial*: the plane's edge, or plane cut back by routing. *isolated*: an antipad "
              "with no connection on this layer (gap = its clearance). *none*: no plane within 0.8 mm.", "",
              _row(["Layer | object", "direct", "relief", "partial", "isolated", "none",
                    "spokes (n×count)", "relief gap mm", "isolated gap mm"]), _row(["---"] * 9)]
        gaps = pl.get("connection_gap", {})
        for k, d in pl["connections"].items():
            sp = pl["relief_spokes"].get(k, {})
            rg = gaps.get(f"{k} | relief", {}).get("rule_mm")
            ig = gaps.get(f"{k} | isolated", {}).get("rule_mm")
            L.append(_row([k, d.get("direct", 0), d.get("relief", 0), d.get("partial", 0), d.get("isolated", 0),
                           d.get("none", 0), ", ".join(f"{s}×{n}" for s, n in sp.items()),
                           f"{_fmt(rg)} ({mil(rg)} mil)" if rg else "-", f"{_fmt(ig)} ({mil(ig)} mil)" if ig else "-"]))
        L.append("")
        L += _stats_table("Plane to board edge", pl["to_board_edge"], "Layer")
    elif pl.get("note"):
        L += ["## Planes", "", pl["note"], ""]
    if cl.get("layers"):
        L += ["## Clearance per layer", ""]
        for layer, entries in cl["layers"].items():
            L += _stats_table(layer, entries)
    return "\n".join(L)
