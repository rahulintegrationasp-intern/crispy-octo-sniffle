"""Product generator: deterministic, seeded catalogue rows.

Design notes
------------
* Category shares (men/women/kids/accessories) are enforced **exactly** by
  pre-allocating per-category counts from ``taxonomy.yaml`` weights.
* Titles are unique: a base title is composed from templates and then made
  distinct with attribute variation; if a collision still happens we append a
  style qualifier drawn from the pool. After N attempts we raise — this keeps
  the "no duplicate titles" rule hard-enforced rather than silently violated.
* Each product carries a latent ``quality`` in [0,1] which drives its review
  rating mix (see :mod:`fashion_datagen.review_generator`).
* Descriptions are assembled from composable sentence banks keyed on
  attributes so they read naturally and vary widely.
"""

from __future__ import annotations

import random
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator

from .pricing import generate_prices
from .taxonomy import GenerationConfig, SubcategorySpec

# ---------------------------------------------------------------------------
# Description sentence banks (composable, attribute-aware).
# ---------------------------------------------------------------------------

DESC_OPENERS = [
    "A {fit_desc} {pattern_desc} {word} in {color} from {brand}'s {occasion_word} edit.",
    "{brand} brings you this {fabric_desc} {word}, cut for a {fit_desc} silhouette.",
    "Upgrade your wardrobe with this {pattern_desc} {word} by {brand}.",
    "This {season_word} essential from {brand} pairs {fabric_desc} comfort with a {fit_desc} cut.",
    "Meet your new favourite {word}: a {fit_desc} piece from {brand} in {color}.",
]

DESC_BODY = [
    "Crafted from breathable {fabric}, it holds shape wash after wash.",
    "Made of {fabric} that feels light on the skin and moves with you.",
    "The {fabric} weave gives it a subtle texture and an easy drape.",
    "Finished with reinforced stitching and a clean {pattern_desc} surface.",
    "Details include a structured collar, flat-lock seams and a tonneled button finish.",
    "Designed for all-day comfort with a softly lined interior.",
    "A versatile {color} shade that transitions from day to evening effortlessly.",
    "Cut and sewn to a true size chart, with room to layer in cooler months.",
]

DESC_CLOSER = [
    "Style it with tailored trousers for the office or denim for weekends.",
    "Perfect for {occasion} wear — dress it up or keep it easy.",
    "An ideal pick for {occasion} days when you want to look sharp without trying too hard.",
    "Pair with neutral footwear and let the {word} do the talking.",
    "Makes a thoughtful gift too, thanks to tidy retail packaging.",
    "Wear it through the {season_word} season and beyond.",
]

FABRIC_ADJ = {
    "cotton": "soft cotton",
    "linen": "airy linen",
    "silk": "lustrous silk",
    "wool": "cosy wool",
    "denim": "sturdy denim",
    "georgette": "flowy georgette",
    "chiffon": "feather-light chiffon",
    "khadi": "handloom khadi",
    "viscose": "buttery viscose",
    "modal": "silky modal",
    "bamboo_rayon": "eco bamboo rayon",
    "polyester": "crisp polyester",
    "nylon": "water-resistant nylon",
    "canvas": "rugged canvas",
    "leather": "full-grain leather",
    "synthetic_leather": "vegan leather",
    "faux_leather": "supple faux leather",
    "suede": "matte suede",
    "mesh": "breathable mesh",
    "fleece": "plush fleece",
    "satin": "glossy satin",
    "velvet": "rich velvet",
    "blend": "easy-care blend",
    "flannel": "brushed flannel",
}

SEASON_WORDS = {
    "summer": "warm-weather",
    "winter": "cold-weather",
    "monsoon": "rainy-season",
    "spring": "spring",
    "autumn": "autumn",
    "all_season": "round-the-year",
}

OCCASION_WORDS = {
    "casual": "everyday",
    "formal": "formal",
    "party": "party",
    "wedding": "wedding",
    "office": "workwear",
    "sports": "active",
    "festive": "festive",
}


def _fmt_pattern(pattern: str) -> str:
    return pattern.replace("_", " ").replace("-", " ")


class ProductGenerator:
    """Generate product dicts one at a time (streaming friendly)."""

    def __init__(self, cfg: GenerationConfig, seed: int, now: datetime | None = None) -> None:
        self.cfg = cfg
        self.rng = random.Random(seed)
        self.now = now or datetime(2025, 1, 1, tzinfo=timezone.utc)
        self._titles: set[str] = set()
        self._ids: set[str] = set()

    # -- helpers ------------------------------------------------------------
    def _pick_age_group(self, category: str) -> str:
        cspec = self.cfg.taxonomy.categories[category]
        weights = cspec["age_groups"]  # list of single-key dicts
        keys = [list(w.keys())[0] for w in weights]
        ws = [list(w.values())[0] for w in weights]
        return self.rng.choices(keys, weights=ws, k=1)[0]

    def _size_options(self, spec: SubcategorySpec) -> list[str]:
        sizes = list(spec.sizes)
        if len(sizes) <= 1:
            return sizes
        n = self.rng.randint(1 if len(sizes) == 1 else max(2, len(sizes) - 2), len(sizes))
        chosen = sorted(self.rng.sample(sizes, n), key=sizes.index)
        return chosen

    def _compose_title(self, spec: SubcategorySpec, brand: str, attrs: dict[str, Any]) -> str:
        tpl = self.rng.choice(spec.title_patterns)
        fields = {
            "brand": brand,
            "color": attrs["color"],
            "pattern": _fmt_pattern(attrs["pattern"]),
            "fabric": FABRIC_ADJ.get(attrs["fabric"], attrs["fabric"]).title(),
            "fit": attrs["fit"].replace("_", " ").title(),
            "occasion": OCCASION_WORDS.get(self.rng.choice(list(attrs["occasion"])), "everyday").title(),
            "subcategory": spec.name.replace("_", " ").title(),
            "style": self.rng.choice([t.split("-")[0] for t in spec.style_tags]).title(),
            "size_range": "S-XL" if "S" in spec.sizes else "",
        }
        try:
            title = tpl.format(**fields)
        except KeyError:
            title = f"{brand} {attrs['color'].title()} {spec.name.replace('_', ' ').title()}"
        return title.strip()

    def _unique_title(self, spec: SubcategorySpec, brand: str, attrs: dict[str, Any]) -> str:
        base = self._compose_title(spec, brand, attrs)
        title = base
        qualifiers = ["Premium", "Classic Edition", "Everyday", "Signature", "Limited Run",
                      "New Season", "Essentials", "Studio Cut"]
        for i in range(6):
            if title not in self._titles:
                break
            title = f"{base} — {qualifiers[i % len(qualifiers)]}" if i < len(qualifiers) else f"{base} v{i}"
        if title in self._titles:
            raise RuntimeError(f"Title space exhausted near {base!r}; increase template variety")
        self._titles.add(title)
        return title

    def _description(self, spec: SubcategorySpec, brand: str, p: dict[str, Any]) -> str:
        rng = self.rng
        word = self.cfg.reviews.word(spec.name)
        fabric_desc = FABRIC_ADJ.get(p["fabric"], p["fabric"])
        pattern_desc = _fmt_pattern(p["pattern"])
        fit_desc = p["fit"].replace("_", " ")
        occasion = p["occasion"][0]
        season = SEASON_WORDS[p["season"][0]]
        ctx = {
            "fit_desc": fit_desc,
            "pattern_desc": pattern_desc,
            "word": word,
            "color": p["color"],
            "brand": brand,
            "occasion_word": OCCASION_WORDS.get(occasion, "everyday"),
            "fabric_desc": fabric_desc,
            "season_word": season,
            "fabric": fabric_desc,
            "occasion": occasion,
            "fit": fit_desc,
        }
        opener = rng.choice(DESC_OPENERS).format(**ctx)
        body = rng.choice(DESC_BODY).format(**ctx)
        closer = rng.choice(DESC_CLOSER).format(**ctx)
        extra = ""
        if rng.random() < 0.35:
            extra = " " + rng.choice(DESC_BODY).format(**ctx)
        return f"{opener} {body}{extra} {closer}"

    def _embedding_text(self, p: dict[str, Any]) -> str:
        bits = [
            p["title"],
            p["description"],
            f"Category: {p['category']}, Type: {p['subcategory'].replace('_', ' ')}, Gender: {p['gender']}",
            f"Brand: {p['brand']}, Color: {p['color']}, Fabric: {p['fabric']}, Fit: {p['fit'].replace('_', ' ')}, Pattern: {p['pattern'].replace('_', ' ')}",
            f"Occasions: {', '.join(p['occasion'])}. Seasons: {', '.join(p['season'])}. Style: {', '.join(p['style_tags'])}.",
        ]
        return " | ".join(bits)

    def finalize_embedding_text(self, p: dict[str, Any]) -> None:
        """Rebuild embedding text once rating aggregates are known."""
        base = self._embedding_text(p)
        tail = (
            f"SIZES:{'/'.join(p['size_options'])} PRICE:Rs{int(p['price_selling'])} "
            f"RATING:{p['rating_avg']} ({p['rating_count']} reviews)"
        )
        p["embedding_text"] = f"{base} | {tail}"

    # -- main ---------------------------------------------------------------
    def make_product(self, category: str, seq: int) -> dict[str, Any]:
        spec = self.rng.choice(self.cfg.taxonomy.subcats_for(category))
        brands = self.cfg.brands.brands_for(category)
        brand_spec = self.rng.choices(brands, weights=[b.weight for b in brands], k=1)[0]
        brand = brand_spec.name

        color = self.rng.choice(spec.colors)
        secondary = self.rng.choice([s for s in spec.secondary_colors if s != "null"])
        if secondary == color:
            secondary = None
        fabric = self.rng.choice(spec.fabrics)
        pattern = self.rng.choice(spec.patterns)
        fit = self.rng.choice(spec.fits)
        n_occ = self.rng.randint(1, min(3, len(spec.occasions)))
        occasions = self.rng.sample(list(spec.occasions), n_occ)
        if self.rng.random() < 0.7:
            seasons = [self.rng.choice(["all_season", "summer", "winter", "monsoon", "spring", "autumn"])]
        else:
            seasons = self.rng.sample(self.cfg.taxonomy.shared["seasons"], self.rng.randint(1, 2))
        style_tags = self.rng.sample(list(spec.style_tags), self.rng.randint(1, min(3, len(spec.style_tags))))

        mrp, selling, disc = generate_prices(
            self.rng, self.cfg, spec.base_price[0], spec.base_price[1], brand_spec.tier, spec.name
        )

        stock_weights = self.cfg.taxonomy.shared["stock_status_weights"]
        stock = self.rng.choices(list(stock_weights.keys()), weights=list(stock_weights.values()), k=1)[0]

        # latent quality drives reviews; slight premium-brand bump
        q_bias = {"budget": -0.04, "mid": 0.0, "premium": 0.05}[brand_spec.tier]
        quality = min(0.98, max(0.05, self.rng.betavariate(4.2, 1.8) + q_bias))

        created = self.now - timedelta(days=self.rng.randint(0, 900), hours=self.rng.randint(0, 23))
        pid = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"fashion.{seq}"))
        if pid in self._ids:
            raise RuntimeError("internal id collision")
        self._ids.add(pid)

        attrs_for_title = {"color": color, "pattern": pattern, "fabric": fabric, "fit": fit, "occasion": occasions}
        title = self._unique_title(spec, brand, attrs_for_title)

        product: dict[str, Any] = {
            "product_id": pid,
            "sku": f"{category[:2].upper()}{spec.name[:3].upper()}{seq:07d}",
            "title": title,
            "description": self._description(spec, brand, {
                "fabric": fabric, "pattern": pattern, "fit": fit, "color": color,
                "occasion": occasions, "season": seasons,
            }),
            "category": category,
            "subcategory": spec.name,
            "gender": self.cfg.taxonomy.categories[category]["gender"],
            "age_group": self._pick_age_group(category),
            "brand": brand,
            "brand_tier": brand_spec.tier,
            "color": color,
            "secondary_color": secondary,
            "size_options": self._size_options(spec),
            "fit": fit,
            "fabric": fabric,
            "pattern": pattern,
            "occasion": occasions,
            "season": seasons,
            "style_tags": style_tags,
            "care_instructions": self.cfg.taxonomy.shared["care_by_fabric"].get(
                fabric, self.cfg.taxonomy.shared["care_by_fabric"]["mixed"]
            ),
            "price_mrp": mrp,
            "price_selling": selling,
            "discount_percent": disc,
            "currency": "INR",
            "stock_status": stock,
            # placeholders; recomputed exactly from generated reviews later
            "rating_avg": None,
            "rating_count": None,
            "rating_distribution": None,
            "image_url": f"https://cdn.example.com/fashion/{category}/{spec.name}/{pid[:8]}.jpg",
            "created_at": created.isoformat(),
            "_quality": quality,  # internal, stripped before writing
        }
        product["embedding_text"] = self._embedding_text(product)
        return product

    def stream(self, plan: list[str]) -> Iterator[dict[str, Any]]:
        """Yield products following a category plan (list of category names)."""
        for seq, cat in enumerate(plan):
            yield self.make_product(cat, seq)


def build_category_plan(total: int, weights: dict[str, float], rng: random.Random) -> list[str]:
    """Allocate exact per-category counts and shuffle them into a plan."""
    cats = list(weights.keys())
    shares = [weights[c] for c in cats]
    alloc = [int(round(total * s)) for s in shares]
    diff = total - sum(alloc)
    # fix rounding drift on the largest bucket
    alloc[cats.index(max(cats, key=lambda c: alloc[cats.index(c)]))] += diff
    plan: list[str] = []
    for c, n in zip(cats, alloc, strict=True):
        plan.extend([c] * n)
    rng.shuffle(plan)
    return plan
