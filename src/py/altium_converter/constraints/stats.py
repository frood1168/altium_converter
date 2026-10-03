"""Distribution summaries and the rule estimate shared by every constraint module."""

from __future__ import annotations

import numpy as np

MIL = 0.0254


def rule_estimate(values: np.ndarray, weights: np.ndarray | None = None, bin_mm: float = 0.0025,
                  tail: float = 0.10) -> float | None:
    """The value the design was held to: the most populated bin in the tightest ``tail``.

    The absolute minimum of observed clearances is usually an outlier (a neck-down,
    a fanout, an importer artefact). Designers route *at* the rule, so the rule
    shows up as a pile-up at the low end; this finds that pile-up.
    """
    values = np.asarray(values, dtype=float)
    if not len(values):
        return None
    w = np.ones_like(values) if weights is None else np.asarray(weights, dtype=float)
    cut = weighted_quantile(values, w, tail)
    sel = values <= cut
    bins = np.round(values[sel] / bin_mm).astype(np.int64)
    if not len(bins):
        return None
    uniq, inv = np.unique(bins, return_inverse=True)
    mass = np.bincount(inv, weights=w[sel])
    return round(float(uniq[np.argmax(mass)] * bin_mm), 4)


def weighted_quantile(values, weights, q):
    order = np.argsort(values)
    v, w = np.asarray(values)[order], np.asarray(weights, dtype=float)[order]
    c = np.cumsum(w)
    return float(v[np.searchsorted(c, q * c[-1], side="left").clip(0, len(v) - 1)])


def summary(values, weights=None) -> dict:
    values = np.asarray(values, dtype=float)
    if not len(values):
        return {"n": 0}
    w = np.ones_like(values) if weights is None else np.asarray(weights, dtype=float)
    est = rule_estimate(values, w)
    q = {f"p{p * 100:g}": round(weighted_quantile(values, w, p), 4) for p in (0.005, 0.01, 0.05, 0.25, 0.5)}
    return {"n": int(len(values)), "min": round(float(values.min()), 4), **q, "rule_mm": est,
            "rule_mil": None if est is None else round(est / MIL, 2)}


def weighted_modes(values, weights, top: int = 6, bin_mm: float = 0.0001) -> list[tuple[float, float]]:
    """(value, total weight) of the heaviest ``top`` bins."""
    values = np.asarray(values, dtype=float)
    if not len(values):
        return []
    bins = np.round(values / bin_mm).astype(np.int64)
    uniq, inv = np.unique(bins, return_inverse=True)
    mass = np.bincount(inv, weights=np.asarray(weights, dtype=float))
    order = np.argsort(-mass)[:top]
    return [(round(float(uniq[i] * bin_mm), 4), float(mass[i])) for i in order]


def mil(mm: float | None) -> str:
    return "-" if mm is None else f"{mm / MIL:.2f}"
