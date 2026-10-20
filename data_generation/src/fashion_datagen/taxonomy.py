"""Load and validate the YAML configuration that drives generation.

All vocabulary (taxonomy, brands, price bands, review templates) lives in
``data/config/*.yaml``; this module loads it once and exposes typed accessors.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# parents: src -> fashion_datagen(0) ... file is at
# data_generation/src/fashion_datagen/taxonomy.py; repo root is 3 levels up.
REPO_ROOT = Path(__file__).resolve().parents[3]
CONFIG_DIR = REPO_ROOT / "data" / "config"

VALID_CATEGORIES = ("men", "women", "kids", "accessories")


@dataclass(frozen=True)
class SubcategorySpec:
    """Attribute pools for one subcategory."""

    name: str
    category: str
    size_unit: str
    sizes: tuple[str, ...]
    colors: tuple[str, ...]
    secondary_colors: tuple[Any, ...]
    fabrics: tuple[str, ...]
    patterns: tuple[str, ...]
    fits: tuple[str, ...]
    occasions: tuple[str, ...]
    style_tags: tuple[str, ...]
    base_price: tuple[float, float]
    title_patterns: tuple[str, ...]


@dataclass
class Taxonomy:
    """Parsed taxonomy.yaml with helpers."""

    categories: dict[str, dict[str, Any]]
    shared: dict[str, Any]
    meta: dict[str, Any]
    _subcats: dict[str, SubcategorySpec] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        for cat, cspec in self.categories.items():
            for sub, sdict in cspec["subcategories"].items():
                key = f"{cat}/{sub}"
                self._subcats[key] = SubcategorySpec(
                    name=sub,
                    category=cat,
                    size_unit=sdict.get("size_unit", "apparel"),
                    sizes=tuple(str(s) for s in sdict["sizes"]),
                    colors=tuple(sdict["colors"]),
                    secondary_colors=tuple(sdict.get("secondary_colors", [None])),
                    fabrics=tuple(sdict["fabrics"]),
                    patterns=tuple(sdict["patterns"]),
                    fits=tuple(sdict["fits"]),
                    occasions=tuple(sdict["occasions"]),
                    style_tags=tuple(sdict["style_tags"]),
                    base_price=(float(sdict["base_price"][0]), float(sdict["base_price"][1])),
                    title_patterns=tuple(sdict["title_patterns"]),
                )

    @property
    def subcategories(self) -> dict[str, SubcategorySpec]:
        return self._subcats

    def subcat(self, category: str, subcategory: str) -> SubcategorySpec:
        return self._subcats[f"{category}/{subcategory}"]

    def subcats_for(self, category: str) -> list[SubcategorySpec]:
        return [s for k, s in self._subcats.items() if k.startswith(f"{category}/")]

    def category_weights(self) -> dict[str, float]:
        return dict(self.meta["categories_distribution"])


@dataclass
class BrandSpec:
    name: str
    tier: str
    categories: tuple[str, ...]
    weight: float


@dataclass
class Brands:
    tiers: dict[str, dict[str, Any]]
    brands: list[BrandSpec]

    def brands_for(self, category: str) -> list[BrandSpec]:
        return [b for b in self.brands if category in b.categories]

    def tier_range(self, tier: str) -> tuple[float, float]:
        lo, hi = self.tiers[tier]["price_multiplier"]
        return float(lo), float(hi)


@dataclass
class PriceConfig:
    rounding: dict[str, Any]
    bands: dict[str, dict[str, tuple[float, float]]]

    def band(self, subcategory: str, tier: str) -> tuple[float, float]:
        sb = self.bands.get(subcategory) or self.bands["accessories"]
        return tuple(sb.get(tier, sb["mid"]))  # type: ignore[return-value]


@dataclass
class ReviewTemplates:
    raw: dict[str, Any]

    def noun_bank(self, subcategory: str) -> list[str]:
        bank = self.raw["noun_bank"]
        return list(bank.get(subcategory, bank["default"]))

    def word(self, subcategory: str) -> str:
        return str(self.raw["subcategory_words"].get(subcategory, "product"))

    def bank(self, sentiment: str) -> dict[str, list[str]]:
        return {k: list(v) for k, v in self.raw[f"{sentiment}_bank"].items()}

    def titles(self, sentiment: str) -> list[str]:
        return list(self.raw["titles"][sentiment])

    def closers(self, sentiment: str) -> list[str]:
        return list(self.raw["closing_lines"][sentiment])

    def occasion_context(self, occasion: str) -> list[str]:
        return list(self.raw["occasion_context"].get(occasion, []))

    @property
    def rating_weights(self) -> dict[int, float]:
        return {int(k): float(v) for k, v in self.raw["rating_weights"].items()}

    @property
    def sentiment_by_rating(self) -> dict[int, str]:
        return {int(k): str(v) for k, v in self.raw["sentiment_by_rating"].items()}

    @property
    def size_feedback_weights(self) -> dict[str, dict[str, float]]:
        return {k: dict(v) for k, v in self.raw["size_feedback_weights"].items()}


@dataclass
class GenerationConfig:
    taxonomy: Taxonomy
    brands: Brands
    prices: PriceConfig
    reviews: ReviewTemplates


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"Config file {path} must contain a mapping")
    return data


def load_config(config_dir: Path | str | None = None) -> GenerationConfig:
    """Load all four config files into a :class:`GenerationConfig`."""
    d = Path(config_dir) if config_dir else CONFIG_DIR
    tax_raw = _load_yaml(d / "taxonomy.yaml")
    br_raw = _load_yaml(d / "brands.yaml")
    pr_raw = _load_yaml(d / "price_bands.yaml")
    rv_raw = _load_yaml(d / "review_templates.yaml")

    taxonomy = Taxonomy(
        categories=tax_raw["categories"], shared=tax_raw["shared"], meta=tax_raw["meta"]
    )
    brands = Brands(
        tiers=br_raw["tiers"],
        brands=[
            BrandSpec(name=b["name"].strip(), tier=b["tier"], categories=tuple(b["categories"]), weight=float(b["weight"]))
            for b in br_raw["brands"]
        ],
    )
    prices = PriceConfig(
        rounding=pr_raw["rounding"],
        bands={k: {t: (float(v[0]), float(v[1])) for t, v in tiers.items()} for k, tiers in pr_raw["bands"].items()},
    )
    reviews = ReviewTemplates(raw=rv_raw)
    return GenerationConfig(taxonomy=taxonomy, brands=brands, prices=prices, reviews=reviews)


@functools.lru_cache(maxsize=4)
def default_config() -> GenerationConfig:
    """Cached default config loaded from the repo's ``data/config`` directory."""
    return load_config()
