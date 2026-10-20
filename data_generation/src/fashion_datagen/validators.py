"""Data-quality validators.

Rules enforced (see PROJECT BRIEF §4):
* required fields non-null, correct types/enums
* price consistency: ``selling <= mrp``, discount matches, band sanity per tier
* rating aggregates exactly derivable from reviews
* review sentiment consistent with rating
* product-consistency of review text (no cross-category nouns)
* no duplicate titles / ids; comment uniqueness ratio reported
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Iterable

from .taxonomy import GenerationConfig

REQUIRED_PRODUCT_FIELDS = [
    "product_id", "title", "description", "category", "subcategory", "gender",
    "age_group", "brand", "color", "size_options", "fit", "fabric", "pattern",
    "occasion", "season", "style_tags", "care_instructions", "price_mrp",
    "price_selling", "discount_percent", "currency", "stock_status",
    "rating_avg", "rating_count", "rating_distribution", "image_url",
    "created_at", "embedding_text",
]
REQUIRED_REVIEW_FIELDS = [
    "review_id", "product_id", "user_name", "rating", "title", "comment",
    "verified_purchase", "helpful_votes", "size_feedback", "sentiment", "created_at",
]

# Nouns that must never appear in a review for the wrong subcategory.
CROSS_CATEGORY_TERMS: dict[str, set[str]] = {
    # subcategory -> forbidden terms borrowed from other subcategories
    "shirt": {"heels", "saree", "kurta", "jeans", "blazer", "dress", "bra", "loafers"},
    "t-shirt": {"heels", "saree", "blazer", "trouser", "bra"},
    "blazer": {"heels", "saree", "kurta", "shorts", "bra"},
    "dress": {"sherwani", "kurta", "briefs"},
    "saree": {"blazer", "jeans", "kurta set"},
    "innerwear": {"blazer", "saree", "heels"},
    "footwear": {"shirt", "saree", "kurta", "blazer", "trouser"},
    "jeans": {"heels", "saree", "blazer"},
}

SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


@dataclass
class ValidationIssue:
    kind: str
    ident: str
    message: str


@dataclass
class ValidationResult:
    issues: list[ValidationIssue] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)
    n_products: int = 0
    n_reviews: int = 0

    @property
    def ok(self) -> bool:
        return not self.issues

    def add(self, kind: str, ident: str, msg: str) -> None:
        if len(self.issues) < 1000:  # cap memory on pathological runs
            self.issues.append(ValidationIssue(kind, ident, msg))

    def issue_counts(self) -> dict[str, int]:
        return dict(Counter(i.kind for i in self.issues))


class Validators:
    """Stateless-ish validator bound to a config."""

    def __init__(self, cfg: GenerationConfig) -> None:
        self.cfg = cfg
        self.result = ValidationResult()
        self._titles: set[str] = set()
        self._ids: set[str] = set()
        self._comments: Counter[str] = Counter()
        self._review_ids: set[str] = set()
        self._per_product_ratings: dict[str, list[int]] = {}
        self._category_counter: Counter[str] = Counter()

    # -- products -----------------------------------------------------------
    def check_product(self, p: dict[str, Any]) -> None:
        pid = str(p.get("product_id", "<missing>"))
        self.result.n_products += 1
        self._category_counter[str(p.get("category"))] += 1

        for f in REQUIRED_PRODUCT_FIELDS:
            if p.get(f) is None or p.get(f) == "" or p.get(f) == []:
                self.result.add("missing_field", pid, f"required field {f!r} missing/empty")

        if pid in self._ids:
            self.result.add("duplicate_id", pid, "product_id already seen")
        self._ids.add(pid)
        title = str(p.get("title", ""))
        if title in self._titles:
            self.result.add("duplicate_title", pid, f"title duplicated: {title!r}")
        self._titles.add(title)

        cat = p.get("category")
        if cat not in ("men", "women", "kids", "accessories"):
            self.result.add("bad_category", pid, f"unknown category {cat!r}")
        else:
            valid_subs = set(self.cfg.taxonomy.categories[cat]["subcategories"].keys())
            if p.get("subcategory") not in valid_subs:
                self.result.add("bad_subcategory", pid, f"{p.get('subcategory')} not under {cat}")

        # prices
        mrp = float(p.get("price_mrp") or 0)
        sell = float(p.get("price_selling") or 0)
        disc = float(p.get("discount_percent") or 0)
        if sell > mrp + 0.005:
            self.result.add("price_order", pid, f"selling {sell} > mrp {mrp}")
        expected_disc = round((mrp - sell) / mrp * 100.0, 1) if mrp > 0 else 0.0
        if abs(expected_disc - disc) > 0.11:
            self.result.add("discount_mismatch", pid, f"disc {disc} vs derived {expected_disc}")
        tier = str(p.get("brand_tier", "mid"))
        lo, hi = self.cfg.prices.band(str(p.get("subcategory")), tier)
        if not (lo * 0.98 <= sell <= hi * 1.02):
            self.result.add("price_band", pid, f"selling {sell} outside band [{lo},{hi}] for {p.get('subcategory')}/{tier}")

        if p.get("currency") != "INR":
            self.result.add("bad_currency", pid, str(p.get("currency")))
        if not p.get("size_options"):
            self.result.add("empty_sizes", pid, "size_options empty")
        if p.get("stock_status") not in ("in_stock", "low_stock", "out_of_stock", "preorder"):
            self.result.add("bad_stock", pid, str(p.get("stock_status")))
        desc_sentences = [s for s in SENTENCE_SPLIT.split(str(p.get("description", ""))) if s.strip()]
        if not 2 <= len(desc_sentences) <= 6:
            self.result.add("desc_length", pid, f"description has {len(desc_sentences)} sentences")

    def finalize_product_rating(self, p: dict[str, Any]) -> None:
        """Check aggregates after all reviews for ``p`` were fed."""
        pid = str(p["product_id"])
        ratings = self._per_product_ratings.get(pid, [])
        if not ratings:
            self.result.add("no_reviews", pid, "product has zero reviews")
            return
        count = p.get("rating_count")
        avg = p.get("rating_avg")
        dist = p.get("rating_distribution") or {}
        if count != len(ratings):
            self.result.add("rating_count_mismatch", pid, f"{count} != {len(ratings)}")
        exp_avg = round(sum(ratings) / len(ratings), 2)
        if avg is None or abs(float(avg) - exp_avg) > 0.005:
            self.result.add("rating_avg_mismatch", pid, f"{avg} != {exp_avg}")
        exp_dist = {str(s): ratings.count(s) for s in range(1, 6)}
        got_dist = {str(k): int(v) for k, v in dist.items()}
        if got_dist != exp_dist:
            self.result.add("rating_dist_mismatch", pid, f"{got_dist} != {exp_dist}")

    # -- reviews ------------------------------------------------------------
    def check_review(self, r: dict[str, Any], product: dict[str, Any]) -> None:
        rid = str(r.get("review_id", "<missing>"))
        self.result.n_reviews += 1
        if rid in self._review_ids:
            self.result.add("duplicate_review_id", rid, "review_id already seen")
        self._review_ids.add(rid)
        for f in REQUIRED_REVIEW_FIELDS:
            if r.get(f) is None or r.get(f) == "":
                self.result.add("review_missing_field", rid, f"field {f!r} missing")

        rating = int(r.get("rating", 0))
        if not 1 <= rating <= 5:
            self.result.add("bad_rating", rid, f"rating {rating}")
        sentiment = r.get("sentiment")
        expected_sent = self.cfg.reviews.sentiment_by_rating.get(rating)
        if sentiment != expected_sent:
            self.result.add("sentiment_rating_mismatch", rid, f"{sentiment} for rating {rating}")
        if r.get("product_id") != product.get("product_id"):
            self.result.add("review_orphan", rid, "product_id mismatch")
        if r.get("size_feedback") not in ("runs_small", "true_to_size", "runs_large"):
            self.result.add("bad_size_feedback", rid, str(r.get("size_feedback")))

        comment = str(r.get("comment", ""))
        self._comments[comment] += 1
        low = comment.lower()
        forbidden = CROSS_CATEGORY_TERMS.get(str(product.get("subcategory")), set())
        for term in forbidden:
            if re.search(rf"\b{re.escape(term)}\b", low):
                self.result.add("cross_category_term", rid, f"{term!r} in review for {product.get('subcategory')}")
        # negative reviews must mention a complaint topic word
        if sentiment == "negative":
            complaint_markers = ("fabric", "colour", "color", "stitch", "fit", "smell", "odour",
                                 "worth", "overpriced", "deliver", "exchange", "return", "small",
                                 "enormous", "thin", "rough", "loose", "uneven", "faded", "bleed",
                                 "different", "painful", "torn", "clammy", "itchy", "useless")
            if not any(m in low for m in complaint_markers):
                self.result.add("negative_no_complaint", rid, comment[:80])
        self._per_product_ratings.setdefault(str(product["product_id"]), []).append(rating)

    # -- summary ------------------------------------------------------------
    def build_stats(self) -> dict[str, Any]:
        total_rev_comments = sum(self._comments.values())
        unique = len(self._comments)
        uniq_ratio = (unique / total_rev_comments) if total_rev_comments else 1.0
        cats = self._category_counter
        total = sum(cats.values()) or 1
        self.result.stats = {
            "products_per_category": dict(cats),
            "category_shares": {k: round(v / total, 4) for k, v in cats.items()},
            "review_comment_uniqueness_ratio": round(uniq_ratio, 4),
            "total_reviews": self.result.n_reviews,
            "total_products": self.result.n_products,
        }
        return self.result.stats


def validate_stream(
    pairs: Iterable[tuple[dict[str, Any], list[dict[str, Any]]]],
    cfg: GenerationConfig,
) -> ValidationResult:
    """Validate a stream of (product, reviews) pairs and return the result."""
    v = Validators(cfg)
    for product, reviews in pairs:
        v.check_product(product)
        for r in reviews:
            v.check_review(r, product)
        v.finalize_product_rating(product)
    v.build_stats()
    return v.result
