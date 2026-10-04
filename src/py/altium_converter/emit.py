"""Observed constraints -> `altium_drc` master TOML + `altium_netclass` TOML.

Pure text in, text out: no `altium_drc` / `altium_netclass` import, so this package stays
standalone. The two files are the siblings' own formats (`[[rule]]` + `[rule.values]`;
`[class.NAME] members`), written the way their writers write them, and the tests parse the
result back with `tomllib` and, when the siblings are checked out beside this repo, with
`altium_drc`'s own scope parser.

What is emitted, and why it is shaped this way
----------------------------------------------
Altium's net classes are the only scope handle the sibling tools can create, so every
per-net rule hangs off one. A differential pair is routed to a *different* width/gap on each
layer (impedance follows the stackup), so the class key is the exact observed geometry:

    NC_DP_<layer>_W<width mil>_G<gap mil>   <- the pairs observed with that width and gap there

and its rules are scoped ``InNetClass('<class>') And OnLayer('<layer>')``. A pair that uses
one geometry on L3 and another on L5 is a member of two classes, and each rule only reaches
its own layer, so a rule never speaks for copper it was not measured on.

Everything is an *estimate of what the router was held to* (the report's "rule est."), so
each emitted rule says where its numbers came from. Review before merging.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field

MIL = 0.0254

# A class this thin is a measurement artefact, not a routing rule.
MIN_COUPLED_MM = 2.0
# Diff-pair geometry tolerance on the emitted min/max, relative. Observed values are rounded
# by the measurement grid and by mm -> mil, so an exact min == max would fail its own copper.
DEFAULT_TOL = 0.02
# Style / layer evidence below this is noise (a handful of vias is not a via rule).
MIN_VIA_COUNT = 10
MIN_SAMPLES = 20


@dataclass
class Rule:
    kind: str
    name: str
    scope1: str
    scope2: str
    values: dict[str, str]
    comment: str = ""
    layer: str = "TOP"
    enabled: bool = True
    priority: int = 0


@dataclass
class Emission:
    rules: list[Rule] = field(default_factory=list)
    classes: dict[str, list[str]] = field(default_factory=dict)  # class name -> nets
    notes: list[str] = field(default_factory=list)               # what was not emitted, and why
    nets: list[str] = field(default_factory=list)                # every net on the board, if known
    components: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Formatting
# --------------------------------------------------------------------------- #
def mil_str(mm: float) -> str:
    """`'5.906mil'` -- the unit and precision `altium_drc` writes (`format_length`: `.4g` mil)."""
    return f"{mm / MIL:.4g}mil"


def _tag(mm: float) -> str:
    """A class-name fragment for a length: 0.1041 mm -> '4p1' (mil, one decimal, no dot)."""
    return f"{mm / MIL:.1f}".rstrip("0").rstrip(".").replace(".", "p")


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", name)


def _q(s: str) -> str:
    """A Master-TOML basic string (the siblings escape the same way)."""
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _lit(s: str) -> str:
    """A net name as a TOML literal string: Altium overbar names hold literal backslashes."""
    return "'" + s + "'" if "'" not in s and not any(ord(c) < 32 for c in s) else _q(s)


# --------------------------------------------------------------------------- #
# Building
# --------------------------------------------------------------------------- #
def _rule_mm(entry: dict | None) -> float | None:
    if not entry or not entry.get("n"):
        return None
    v = entry.get("rule_mm")
    return float(v) if v else None


def _min_rule(entries: dict, *, contains: str, kinds=None, min_n=MIN_SAMPLES) -> tuple[float, str] | None:
    """Smallest positive rule estimate over `entries` whose key contains `contains`.

    `kinds`: both objects of an `A -> B (region)` key must be in it."""
    best = None
    for key, e in entries.items():
        if contains not in key:
            continue
        if kinds and not all(o.split(" (")[0] in kinds for o in key.split(" -> ")):
            continue
        if (e.get("n") or 0) < min_n:
            continue
        v = _rule_mm(e)
        if v and (best is None or v < best[0]):
            best = (v, key)
    return best


def _width_rules(c: dict, rules: list[Rule], notes: list[str]) -> None:
    widths = c.get("widths", {})
    allse: dict[float, float] = {}
    lows, highs = [], []
    for layer, rec in widths.items():
        se = [r for r in rec.get("single_ended", []) if r["share"] >= 0.02]
        if not se:
            continue
        top = se[0]["width_mm"]
        lo, hi = min(r["width_mm"] for r in se), max(r["width_mm"] for r in se)
        lows.append(lo)
        highs.append(hi)
        for r in se:
            allse[r["width_mm"]] = allse.get(r["width_mm"], 0.0) + r["length_mm"]
        rules.append(Rule(
            "Width", f"Width_{_safe(layer)}", f"OnLayer('{layer}')", "All",
            {"minimum_width": mil_str(lo), "preferred_width": mil_str(top), "maximum_width": mil_str(hi)},
            comment=("single-ended widths carrying >=2% of this layer's non-pair length; preferred is "
                     f"the most common ({top:g} mm, {se[0]['share']:.0%}); min/max the extremes"),
            layer=layer))
    if allse:
        pref = max(allse, key=allse.get)
        rules.append(Rule(
            "Width", "Width", "All", "All",
            {"minimum_width": mil_str(min(lows)), "preferred_width": mil_str(pref),
             "maximum_width": mil_str(max(highs))},
            comment="catch-all: extremes of the per-layer rules, preferred = most common overall"))
    else:
        notes.append("no single-ended width data: Width rules not emitted")


def _diff_pair_classes(c: dict, tol: float, min_coupled: float):
    """(layer, width, gap) -> sorted nets, from per-pair per-layer coupled geometry."""
    groups: dict[tuple[str, float, float], dict] = {}
    for p in c.get("diffpairs", {}).get("pairs", []):
        for layer, g in p.get("layers", {}).items():
            w, gap = g.get("width_mm"), g.get("gap_mm")
            if not w or not gap or (g.get("coupled_mm") or 0.0) < min_coupled:
                continue
            e = groups.setdefault((layer, round(w, 4), round(gap, 4)), {"nets": set(), "coupled": 0.0, "pairs": 0})
            e["nets"].update((p["p"], p["n"]))
            e["coupled"] += g["coupled_mm"]
            e["pairs"] += 1
    return groups


def _diff_pair_rules(c: dict, em: Emission, tol: float, min_coupled: float) -> None:
    groups = _diff_pair_classes(c, tol, min_coupled)
    if not groups:
        em.notes.append("no differential pairs with a measured coupled run: DiffPairsRouting / pair Width "
                        "rules and classes not emitted")
        return
    order = {l: i for i, l in enumerate(c.get("layers", []))}
    for (layer, w, gap), e in sorted(groups.items(), key=lambda kv: (order.get(kv[0][0], 99), -kv[1]["coupled"])):
        cls = f"NC_DP_{_safe(layer)}_W{_tag(w)}_G{_tag(gap)}"
        em.classes[cls] = sorted(e["nets"])
        scope = f"InNetClass('{cls}') And OnLayer('{layer}')"
        why = (f"{e['pairs']} pair(s), {e['coupled']:.0f} mm coupled on {layer}: width {w:g} mm, gap {gap:g} mm; "
               f"min/max are +/-{tol:.0%} of the observed value")
        em.rules.append(Rule(
            "Width", f"Width_{cls[3:]}", scope, "All",
            {"minimum_width": mil_str(w * (1 - tol)), "preferred_width": mil_str(w),
             "maximum_width": mil_str(w * (1 + tol))}, comment=why, layer=layer))
        em.rules.append(Rule(
            "DiffPairsRouting", f"DiffPairsRouting_{cls[3:]}", scope, "All",
            {"minimum_limit": mil_str(gap * (1 - tol)), "maximum_limit": mil_str(gap * (1 + tol)),
             "preferred_gap": mil_str(gap), "impedance_profile_driven": "false"},
            comment=why, layer=layer))


def _clearance_rules(c: dict, em: Emission) -> None:
    cl = c.get("clearances", {})
    board = cl.get("board", {})
    if not board:
        em.notes.append("no clearance data: Clearance rules not emitted")
        return
    # Hole-to-copper is its own rule kind; pad<->pad inside a footprint is not a routing clearance.
    copper = {"Track", "Via", "SMD Pad", "TH Pad"}
    open_ = _min_rule(board, contains="(open)", kinds=copper)
    bga = _min_rule(board, contains="(BGA)", kinds=copper)
    if bga and c.get("bga_fields"):
        refs = " Or ".join(f"InComponent('{r}')" for r in c["bga_fields"])
        em.rules.append(Rule(
            "Clearance", "Clearance_BGA", refs, refs,
            {"gap": mil_str(bga[0]), "generic_clearance": mil_str(bga[0])},
            comment=f"tightest observed estimate inside the {len(c['bga_fields'])} BGA fields "
                    f"({', '.join(c['bga_fields'])}): {bga[1]}"))
    if open_:
        em.rules.append(Rule(
            "Clearance", "Clearance", "All", "All",
            {"gap": mil_str(open_[0]), "generic_clearance": mil_str(open_[0])},
            comment=f"tightest observed estimate on the open board: {open_[1]}"))
    em.notes.append("Clearance carries one generic gap per region; the per-object-pair matrix "
                    "(`Track -> Via`, `SMD Pad -> Track`, ...) is in the report, not the rule")


def _via_rule(c: dict, em: Emission) -> None:
    styles = [s for s in c.get("vias", {}).get("styles", []) if s.get("count", 0) >= MIN_VIA_COUNT]
    through = [s for s in styles if s["span"] == "through"]
    if not through:
        em.notes.append("no through-via style with enough samples: RoutingVias not emitted")
        return
    top = max(through, key=lambda s: s["count"])
    em.rules.append(Rule(
        "RoutingVias", "RoutingVias", "All", "All",
        {"via_style": "Through Hole",
         "minimum_width": mil_str(min(s["outer_pad_mm"] for s in through)),
         "preferred_width": mil_str(top["outer_pad_mm"]),
         "maximum_width": mil_str(max(s["outer_pad_mm"] for s in through)),
         "minimum_hole_width": mil_str(min(s["drill_mm"] for s in through)),
         "preferred_hole_width": mil_str(top["drill_mm"]),
         "maximum_hole_width": mil_str(max(s["drill_mm"] for s in through))},
        comment=f"through-hole styles with >={MIN_VIA_COUNT} vias; preferred is the most used "
                f"({top['drill_mm']:g} mm drill / {top['outer_pad_mm']:g} mm pad, {top['count']} vias)"))
    other = [s for s in styles if s["span"] != "through"]
    if other:
        em.notes.append("blind/buried via styles ("
                        + ", ".join(sorted({s["span"] for s in other}))
                        + ") are not emitted: RoutingVias holds one via style")


def _plane_rules(c: dict, em: Emission) -> None:
    pl = c.get("planes", {})
    ant = pl.get("antipad", {})
    # keys look like '02_GND | Via (open) from drill'
    best = _min_rule({k: v for k, v in ant.items() if k.endswith("from drill") and "(open)" in k},
                     contains="from drill")
    if best:
        em.rules.append(Rule(
            "PlaneClearance", "PlaneClearance", "All", "All", {"clearance": mil_str(best[0])},
            comment=f"tightest observed hole-to-plane antipad estimate ({best[1]})"))
    else:
        em.notes.append("no plane antipad data: PlaneClearance not emitted")
    if pl.get("connections"):
        em.notes.append("PolygonConnect (relief spokes / air gap) is measured but the master TOML does not "
                        "carry connect settings: set it from the report's 'Same-net connection style' table")


def build(c: dict, *, tol: float = DEFAULT_TOL, min_coupled: float = MIN_COUPLED_MM) -> Emission:
    """`constraints.json` dict -> rules, net classes, notes. Priority 1 is Altium's top."""
    em = Emission(nets=list(c.get("nets", [])), components=list(c.get("components", [])))
    # Emit order is priority order within a kind: pair rules (most specific scope) before the
    # per-layer and catch-all Width rules, and Clearance_BGA before the generic Clearance.
    _diff_pair_rules(c, em, tol, min_coupled)
    _width_rules(c, em.rules, em.notes)
    _clearance_rules(c, em)
    _via_rule(c, em)
    _plane_rules(c, em)
    per_kind: dict[str, int] = {}
    for r in em.rules:
        per_kind[r.kind] = per_kind.get(r.kind, 0) + 1
        r.priority = per_kind[r.kind]
    return em


# --------------------------------------------------------------------------- #
# Writing
# --------------------------------------------------------------------------- #
def master_toml(em: Emission, source: str, extracted: str) -> str:
    """The `altium_drc` master format, with a `[board]` table when the constraints carry the namespaces."""
    L = ["# Master PCB design-rule set",
         "# Derived from OBSERVED copper by `altium-converter rules` -- not read from a rule set.",
         "# Every number is an estimate of what the router was held to. Review before merging.",
         "# Pair rules are scoped to the NC_DP_* net classes in the sibling netclass file: apply that first.",
         "", "[meta]", f"source_board = {_q(source)}", f"extracted = {_q(extracted)}",
         f"rule_count = {len(em.rules)}", 'tool = "altium-converter rules"', ""]
    if em.nets:
        # Enumerated, so `master lint` / `netscope` can tell "no such net or class" from "not told".
        L += [f"# {'-' * 70}", "# Board namespaces the scopes above resolve against.", f"# {'-' * 70}",
              "[board]", 'namespaces = "enumerated"', "nets = [" + ", ".join(_lit(n) for n in em.nets) + "]",
              "components = [" + ", ".join(_lit(n) for n in em.components) + "]", ""]
        for name in sorted(em.classes):
            L += ["[[board.class]]", f"name = {_q(name)}", "kind = 0", "superclass = false",
                  "autogenerated = false", "enabled = true",
                  "members = [" + ", ".join(_lit(n) for n in em.classes[name]) + "]", ""]
    by_kind: dict[str, list[Rule]] = {}
    for r in em.rules:
        by_kind.setdefault(r.kind, []).append(r)
    for kind in sorted(by_kind):
        L += [f"# {'-' * 70}", f"# {kind}  ({len(by_kind[kind])} rule{'s' if len(by_kind[kind]) != 1 else ''})",
              f"# {'-' * 70}"]
        for r in by_kind[kind]:
            L += ["[[rule]]", f"kind = {_q(r.kind)}", f"name = {_q(r.name)}", f"enabled = {str(r.enabled).lower()}",
                  f"priority = {r.priority}", f"scope1 = {_q(r.scope1)}", f"scope2 = {_q(r.scope2)}"]
            if r.comment:
                L.append(f"comment = {_q(r.comment)}")
            L += [f"layer = {_q(r.layer)}", "[rule.values]"]
            for k in sorted(r.values):
                v = r.values[k]
                L.append(f"{k} = {v}" if v in ("true", "false") else f"{k} = {_q(v)}")
            L.append("")
    text = "\n".join(L) + "\n"
    tomllib.loads(text)  # a writer that emits what it cannot read is worse than one that fails
    return text


def netclass_toml(em: Emission, source: str) -> str:
    """The `altium_netclass` format; a `[board]` roster lets `diff` tell a deleted net from an unknown one."""
    L = ["# altium-netclass -- editable net class (NC_) lists.",
         "# Generated by `altium-converter rules` from observed differential-pair geometry.",
         "# Net names carry Altium's overbar encoding; backslashes are literal. Keep them.", "",
         "schema = 1", f"source = {_lit(source)}", ""]
    for name in sorted(em.classes):
        L += [f"[class.{name}]", "members = ["]
        L += [f"  {_lit(n)}," for n in em.classes[name]]
        L += ["]", ""]
    if em.nets:
        L += ["# Every net on the board when this file was generated. Do not hand-edit.", "[board]",
              "nets = [" + ", ".join(_lit(n) for n in em.nets) + "]", ""]
    text = "\n".join(L)
    tomllib.loads(text)
    return text


def notes_text(em: Emission) -> str:
    return "\n".join(f"- {n}" for n in em.notes)
