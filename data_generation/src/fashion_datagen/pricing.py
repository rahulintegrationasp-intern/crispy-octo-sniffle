"""Price model: MRP, discount and selling price generation + rounding.

MRP is drawn from the subcategory base range, scaled by a brand-tier
multiplier and per-product jitter, then snapped to retail-style endings.
Selling price = MRP * (1 - discount/100), rounded to whole rupees.
All magic numbers come from ``data/config/price_bands.yaml``.
"""

from __future__ import annotations

import random

from .taxonomy import GenerationConfig


def pick_discount(rng: random.Random, cfg: GenerationConfig) -> float:
    """Sample a discount percentage from the configured buckets."""
    weights = cfg.prices.rounding["discount_weights"]
    bucket = rng.choices(list(weights.keys()), weights=list(weights.values()), k=1)[0]
    if bucket == "none_0_5":
        return rng.uniform(0.0, 5.0)
    if bucket == "light_5_15":
        return rng.uniform(5.0, 15.0)
    if bucket == "mid_15_40":
        return rng.uniform(15.0, 40.0)
    max_disc = float(cfg.prices.rounding["max_discount_percent"])
    return rng.uniform(40.0, max_disc)


def snap_to_ending(value: float, endings: list[int]) -> int:
    """Snap a raw price to the nearest integer ending in a retail-style suffix.

    ``endings`` are 1-2 digit suffixes (e.g. 99 -> prices like 1099, 1199).
    """
    best: int | None = None
    value = max(float(value), 99.0)
    for end in endings:
        n_digits = len(str(end))
        mod = 10**n_digits
        base = int(value // mod) * mod + end
        for delta in (-mod, 0, mod):
            cand = base + delta
            if cand > 0 and (best is None or abs(cand - value) < abs(best - value)):
                best = cand
    assert best is not None
    return best


def generate_prices(
    rng: random.Random,
    cfg: GenerationConfig,
    base_lo: float,
    base_hi: float,
    tier: str,
    subcategory: str,
) -> tuple[float, float, float]:
    """Return ``(mrp, selling_price, discount_percent)`` consistent with bands.

    The MRP is generated first; the discount is sampled; the selling price is
    derived and hard-clamped into the configured band for (subcategory, tier).
    """
    lo_mult, hi_mult = cfg.brands.tier_range(tier)
    mult = rng.uniform(lo_mult, hi_mult)
    band_lo, band_hi = cfg.prices.band(subcategory, tier)
    raw_mrp = max(rng.uniform(base_lo, base_hi) * mult, band_lo * 1.05)
    endings = [int(e) for e in cfg.prices.rounding["mrp_endings"]]
    mrp = float(max(99, snap_to_ending(raw_mrp, endings)))
    max_disc = float(cfg.prices.rounding["max_discount_percent"])

    disc = round(pick_discount(rng, cfg), 1)
    selling = mrp * (1.0 - disc / 100.0)

    # clamp selling price into the band while keeping selling <= mrp
    selling = min(max(selling, band_lo), band_hi)
    if selling > mrp or mrp < band_lo:  # MRP too low for this band: lift it
        target_mrp = max(selling, band_lo) / (1.0 - min(disc, max_disc) / 100.0)
        mrp = float(max(mrp, snap_to_ending(target_mrp * 1.05, endings)))
    disc = round((mrp - selling) / mrp * 100.0, 1)
    disc = max(0.0, min(disc, max_disc))
    # recompute selling from final discount so the triple is exactly consistent
    selling = round(mrp * (1.0 - disc / 100.0), 2)

    # ---- hard safety: guarantee selling stays inside [band_lo, band_hi] ----
    # Strategy: selling is always derived as mrp * (1 - disc/100), so the
    # triple is always exactly consistent.  We iteratively adjust mrp and disc
    # until selling lands inside the band.
    for _ in range(5):
        if selling < band_lo:
            # Need selling >= band_lo.  With the current disc, compute the
            # minimum MRP that yields selling = band_lo.
            needed_mrp = band_lo / (1.0 - disc / 100.0)
            mrp = float(max(mrp, snap_to_ending(needed_mrp + 1, endings)))
            selling = round(mrp * (1.0 - disc / 100.0), 2)
        elif selling > band_hi:
            # Reduce discount so selling fits inside band_hi.
            # selling = mrp * (1 - disc/100) <= band_hi  ⟹  disc >= (1 - band_hi/mrp)*100
            min_disc = round((1.0 - band_hi / mrp) * 100.0, 1)
            disc = max(min_disc, 0.0)
            selling = round(mrp * (1.0 - disc / 100.0), 2)
        else:
            break  # selling is within band — done

    # Final: hard-set selling into the band and derive everything back.
    selling = round(min(max(selling, band_lo), band_hi), 2)
    if mrp < selling:
        mrp = float(snap_to_ending(selling * 1.05, endings))
    disc = round((mrp - selling) / mrp * 100.0, 1)
    disc = max(0.0, min(disc, max_disc))
    # Recompute selling one last time from the final triple.
    selling = round(mrp * (1.0 - disc / 100.0), 2)

    return mrp, selling, disc
