"""Review generator: rating-consistent, product-consistent synthetic reviews.

Key rules (enforced here and re-checked in ``validators.py``):
* A product's latent quality shifts the global star prior; ratings are drawn
  per product so some products end up genuinely low-rated.
* Sentiment is a deterministic function of rating (5/4 positive, 3 neutral,
  2/1 negative) and comment sentences come only from the matching bank.
* Slots ({item}, {color}, {fabric}, {size}, {brand}) are filled from the
  product record, so a shirt review can never mention heels — vocabulary is
  scoped by subcategory via ``noun_bank``/``subcategory_words``.
* The product's ``rating_avg``, ``rating_count`` and ``rating_distribution``
  are recomputed **exactly** from the generated reviews.
"""

from __future__ import annotations

import random
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator

from .taxonomy import GenerationConfig

# Topic draw weights per sentiment: which aspects a reviewer talks about.
TOPIC_WEIGHTS: dict[str, dict[str, float]] = {
    "positive": {"quality": 0.34, "fit": 0.2, "comfort": 0.16, "value": 0.12, "style": 0.12, "service": 0.06},
    "neutral": {"quality": 0.3, "fit": 0.22, "expectation": 0.24, "value": 0.14, "mixed": 0.1},
    "negative": {"fabric": 0.22, "color": 0.16, "stitching": 0.18, "fit": 0.16, "comfort": 0.08, "value": 0.08, "service": 0.06, "smell": 0.06},
}

FIT_DESCS = {
    "slim": "close-fitting",
    "regular": "classic",
    "relaxed": "easy-going",
    "oversized": "roomy oversized",
    "straight": "straight",
    "tapered": "tapered",
    "bootcut": "bootcut",
    "crop": "cropped",
    "high_waist": "high-rise",
    "wide_leg": "wide-leg",
    "cigarette": "slim cigarette",
    "palazzo": "flowy palazzo",
    "skinny": "skinny",
    "mom": "mom-fit",
    "boyfriend": "boyfriend",
    "flare": "flared",
    "fit_and_flare": "fit-and-flare",
    "bodycon": "bodycon",
    "a_line": "A-line",
    "shift": "shift",
    "wrap": "wrap",
    "maxi": "maxi",
    "a_line_skirt": "A-line",
    "max": "maxi",
    "midi": "midi",
    "mini": "mini",
    "pleated": "pleated",
    "tiered": "tiered",
    "double_breasted": "double-breasted",
    "cropped": "cropped",
    "fitted": "fitted",
    "jogster": "jogger-style",
    "angrakha": "angrakha",
    "high_slit": "high-slit",
    "straight_kurti": "straight",
}


def _pattern_desc(pattern: str) -> str:
    return pattern.replace("_", " ").replace("-", " ")


class ReviewGenerator:
    """Generate reviews for one product and derive its rating aggregates."""

    def __init__(self, cfg: GenerationConfig, seed: int, now: datetime | None = None) -> None:
        self.cfg = cfg
        self.rng = random.Random(seed + 7919)  # independent stream from products
        self.now = now or datetime(2025, 6, 1, tzinfo=timezone.utc)

    # -- rating mix -----------------------------------------------------------
    def rating_probs(self, quality: float) -> list[float]:
        """Shift the global star prior toward ``quality`` (0..1)."""
        prior = self.cfg.reviews.rating_weights
        base = [prior[i] for i in range(1, 6)]
        stars = [1, 2, 3, 4, 5]
        # pull mass toward the quality-implied centre star
        centre = 1 + 4 * quality  # 1..5
        shifted = []
        for s, b in zip(stars, base, strict=True):
            affinity = max(0.0, 1.0 - abs(s - centre) / 4.0)
            shifted.append(b * (0.35 + affinity))
        total = sum(shifted)
        return [w / total for w in shifted]

    def draw_ratings(self, n: int, quality: float) -> list[int]:
        probs = self.rating_probs(quality)
        return self.rng.choices([1, 2, 3, 4, 5], weights=probs, k=n)

    # -- text -----------------------------------------------------------------
    def _slots(self, product: dict[str, Any], size: str) -> dict[str, str]:
        word = self.cfg.reviews.word(product["subcategory"])
        item_pool = self.cfg.reviews.noun_bank(product["subcategory"])
        item = self.rng.choice(item_pool).format(subcategory_word=word)
        fabric_label = product["fabric"].replace("_", " ")
        return {
            "item": item,
            "items": item,
            "color": product["color"],
            "fabric": fabric_label,
            "size": size,
            "brand": product["brand"],
            "price": f"{int(product['price_selling'])}",
            "fit_desc": FIT_DESCS.get(product["fit"], product["fit"].replace("_", " ")),
            "pattern_desc": _pattern_desc(product["pattern"]),
        }

    def compose_comment(self, sentiment: str, slots: dict[str, str], occasion: str | None) -> str:
        rng = self.rng
        bank = self.cfg.reviews.bank(sentiment)
        topics = TOPIC_WEIGHTS[sentiment]
        n_sentences = {"positive": (1, 3), "neutral": (1, 2), "negative": (2, 4)}[sentiment]
        k = rng.randint(*n_sentences)
        chosen_topics = rng.choices(list(topics.keys()), weights=list(topics.values()), k=k)
        sentences: list[str] = []
        used: set[str] = set()
        for topic in chosen_topics:
            options = [s for s in bank[topic] if s not in used] or bank[topic]
            tpl = rng.choice(options)
            used.add(tpl)
            sentences.append(tpl.format(**slots))
        # occasional occasion context line
        ctx_lines = self.cfg.reviews.occasion_context(occasion or "")
        if ctx_lines and rng.random() < 0.25:
            sentences.insert(rng.randint(0, len(sentences)), rng.choice(ctx_lines).format(**slots))
        if rng.random() < 0.4:
            sentences.append(rng.choice(self.cfg.reviews.closers(sentiment)))
        return " ".join(sentences)

    # -- main -----------------------------------------------------------------
    def make_reviews(self, product: dict[str, Any]) -> list[dict[str, Any]]:
        rng = self.rng
        quality = float(product.get("_quality", 0.7))
        n = rng.randint(3, 15)
        # more popular items get more reviews
        if rng.random() < 0.15:
            n = min(15, n + rng.randint(0, 5))
        ratings = self.draw_ratings(n, quality)
        created_base = datetime.fromisoformat(product["created_at"])
        if created_base > self.now:
            created_base = self.now
        reviews: list[dict[str, Any]] = []
        dist = {str(i): 0 for i in range(1, 6)}
        sent_by_rating = self.cfg.reviews.sentiment_by_rating
        sf_weights = self.cfg.reviews.size_feedback_weights
        for rating in ratings:
            sentiment = sent_by_rating[rating]
            size = rng.choice(product["size_options"])
            slots = self._slots(product, size)
            occasion = product["occasion"][0] if product["occasion"] else None
            comment = self.compose_comment(sentiment, slots, occasion)
            title = rng.choice(self.cfg.reviews.titles(sentiment))
            sf_opts = sf_weights[sentiment]
            size_feedback = rng.choices(list(sf_opts.keys()), weights=list(sf_opts.values()), k=1)[0]
            days_after = rng.randint(1, 240)
            r_created = min(self.now, created_base + timedelta(days=days_after, hours=rng.randint(0, 23)))
            verified = rng.random() < 0.8
            helpful = int(max(0, rng.lognormvariate(-0.5, 1.2) - 1)) if rng.random() < 0.6 else 0
            reviews.append({
                "review_id": str(uuid.uuid4()),
                "product_id": product["product_id"],
                "user_name": self._fake_name(),
                "rating": rating,
                "title": title,
                "comment": comment,
                "verified_purchase": verified,
                "helpful_votes": helpful,
                "size_feedback": size_feedback,
                "sentiment": sentiment,
                "created_at": r_created.isoformat(),
            })
            dist[str(rating)] += 1
        count = len(reviews)
        avg = round(sum(r["rating"] for r in reviews) / count, 2)
        product["rating_avg"] = avg
        product["rating_count"] = count
        product["rating_distribution"] = dist
        return reviews

    _FIRST = ["Aarav", "Priya", "Rohan", "Sneha", "Vikram", "Ananya", "Karan", "Divya", "Arjun",
              "Meera", "Siddharth", "Nisha", "Aditya", "Pooja", "Nikhil", "Ritika", "Farhan",
              "Lakshmi", "Sameer", "Tanvi", "Yash", "Ishita", "Devansh", "Kavya", "Imran",
              "Neelam", "Rahul", "Shweta", "Varun", "Zoya"]
    _LAST = ["Sharma", "Verma", "Patel", "Reddy", "Nair", "Iyer", "Khan", "Gupta", "Joshi",
             "Menon", "Kapoor", "Chopra", "Desai", "Bhat", "Rao", "Singh", "Mishra", "Pillai",
             "Sethi", "Mehta"]

    def _fake_name(self) -> str:
        f = self.rng.choice(self._FIRST)
        l = self.rng.choice(self._LAST)
        style = self.rng.random()
        if style < 0.5:
            return f"{f} {l}"
        if style < 0.8:
            return f"{f[0]} {l}"
        return f"{f}{self.rng.randint(10, 99)}"

    def stream(self, products: Iterator[dict[str, Any]]) -> Iterator[tuple[dict[str, Any], list[dict[str, Any]]]]:
        """Pair each product with its reviews (and mutate aggregates)."""
        for product in products:
            yield product, self.make_reviews(product)
