"""CLI entry point: ``python -m fashion_datagen generate --products 100000``.

Streams products+reviews to JSONL (and optional Parquet), validates every row
in-line, and writes a human-readable report into ``data/reports/``.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

from .product_generator import ProductGenerator, build_category_plan
from .review_generator import ReviewGenerator
from .taxonomy import REPO_ROOT, default_config
from .validators import Validators
from .writers import JsonlWriter, ParquetWriter

DEFAULT_PRODUCTS = 100_000
MAX_PRODUCTS = 500_000
SMALL_PRODUCTS = 2_000


def _histogram(values: list[float], bins: int = 10) -> list[str]:
    if not values:
        return []
    lo, hi = min(values), max(values)
    if hi == lo:
        return [f"{lo:.0f}"]
    step = (hi - lo) / bins
    counts = [0] * bins
    for v in values:
        idx = min(int((v - lo) / step), bins - 1)
        counts[idx] += 1
    lines = []
    for i, c in enumerate(counts):
        b0, b1 = lo + i * step, lo + (i + 1) * step
        bar = "#" * max(1, int(40 * c / max(counts)))
        lines.append(f"  ₹{b0:>8.0f}–₹{b1:>8.0f} | {bar} {c}")
    return lines


def generate(
    n_products: int,
    seed: int,
    out_dir: Path,
    parquet: bool = False,
    sample_products: int = 100,
    quiet: bool = False,
) -> dict[str, Any]:
    """Run the full generation pipeline; returns the stats dict."""
    cfg = default_config()
    plan_rng = random.Random(seed ^ 0x5EED)
    plan = build_category_plan(n_products, cfg.taxonomy.category_weights(), plan_rng)

    pg = ProductGenerator(cfg, seed=seed)
    rg = ReviewGenerator(cfg, seed=seed)
    validator = Validators(cfg)

    raw_dir = out_dir / "raw"
    reports_dir = out_dir / "reports"
    samples_dir = out_dir / "samples"
    raw_dir.mkdir(parents=True, exist_ok=True)
    reports_dir.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    subcat_prices: dict[str, list[float]] = {}
    rating_hist: Counter[int] = Counter()
    sample_products_buf: list[dict[str, Any]] = []
    sample_reviews_buf: list[dict[str, Any]] = []

    pq_products = ParquetWriter(raw_dir / "products.parquet") if parquet else None
    pq_reviews = ParquetWriter(raw_dir / "reviews.parquet") if parquet else None
    pq_batch: list[dict[str, Any]] = []
    pq_rev_batch: list[dict[str, Any]] = []

    with JsonlWriter(raw_dir / "products.jsonl") as pw, JsonlWriter(raw_dir / "reviews.jsonl") as rw:
        for product in pg.stream(plan):
            reviews = rg.make_reviews(product)
            pg.finalize_embedding_text(product)

            validator.check_product(product)
            for r in reviews:
                validator.check_review(r, product)
            validator.finalize_product_rating(product)

            pw.write(product)
            for r in reviews:
                rw.write(r)

            key = f"{product['category']}/{product['subcategory']}"
            subcat_prices.setdefault(key, []).append(float(product["price_selling"]))
            for r in reviews:
                rating_hist[r["rating"]] += 1

            if len(sample_products_buf) < sample_products:
                sample_products_buf.append(dict(product))
                sample_reviews_buf.extend(dict(r) for r in reviews)

            if pq_products is not None:
                from .writers import clean_row
                pq_batch.append(clean_row(product))
                if len(pq_batch) >= 5000:
                    pq_products.write_table(pq_batch)
                    pq_batch.clear()
            if pq_reviews is not None:
                from .writers import clean_row as _cr
                pq_rev_batch.extend(_cr(r) for r in reviews)
                if len(pq_rev_batch) >= 10000:
                    pq_reviews.write_table(pq_rev_batch)
                    pq_rev_batch.clear()

            if not quiet and validator.result.n_products % 10_000 == 0:
                rate = validator.result.n_products / (time.time() - t0)
                print(f"  {validator.result.n_products:>7} products @ {rate:,.0f}/s", file=sys.stderr)

    if pq_products is not None and pq_batch:
        pq_products.write_table(pq_batch)
        pq_products.close()
    if pq_reviews is not None and pq_rev_batch:
        pq_reviews.write_table(pq_rev_batch)
        pq_reviews.close()

    stats = validator.build_stats()
    elapsed = time.time() - t0

    # ---- write report ------------------------------------------------------
    lines: list[str] = []
    lines.append("# Data Generation Validation Report")
    lines.append("")
    lines.append(f"- seed: {seed}")
    lines.append(f"- products requested: {n_products}, written: {stats['total_products']}")
    lines.append(f"- reviews written: {stats['total_reviews']}")
    lines.append(f"- avg reviews/product: {stats['total_reviews'] / max(1, stats['total_products']):.2f}")
    lines.append(f"- elapsed: {elapsed:.1f}s ({stats['total_products'] / max(elapsed, 1e-9):.0f} products/s)")
    lines.append("")
    lines.append("## Category distribution (target vs actual)")
    targets = cfg.taxonomy.category_weights()
    for cat, share in stats["category_shares"].items():
        lines.append(f"- {cat}: actual {share:.3%} (target {targets.get(cat, 0):.1%}) — count {stats['products_per_category'][cat]}")
    lines.append("")
    lines.append("## Global star distribution of reviews")
    total_r = sum(rating_hist.values()) or 1
    for star in range(5, 0, -1):
        pct = rating_hist[star] / total_r
        lines.append(f"- {star}★: {pct:.1%} ({rating_hist[star]})")
    lines.append("")
    lines.append("## Price histograms per category/subcategory (selling price, INR)")
    for key in sorted(subcat_prices):
        vals = subcat_prices[key]
        lines.append(f"### {key}  (n={len(vals)}, median ₹{sorted(vals)[len(vals)//2]:.0f})")
        lines.extend(_histogram(vals, bins=8))
    lines.append("")
    lines.append("## Uniqueness")
    lines.append(f"- review comment uniqueness ratio: {stats['review_comment_uniqueness_ratio']:.3f}")
    lines.append("- duplicate titles: see validation issues below")
    lines.append("")
    counts = validator.result.issue_counts()
    lines.append("## Validation summary")
    if validator.result.ok:
        lines.append("PASS — no violations found.")
    else:
        lines.append(f"FAIL — {sum(counts.values())} issue(s) across kinds: {json.dumps(counts)}")
        for issue in validator.result.issues[:20]:
            lines.append(f"- [{issue.kind}] {issue.ident}: {issue.message}")
    lines.append("")

    report_path = reports_dir / "validation_report.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")
    (reports_dir / "stats.json").write_text(json.dumps({**stats, "elapsed_sec": round(elapsed, 2)}, indent=2), encoding="utf-8")

    # committed samples
    from .writers import write_samples
    write_samples(sample_products_buf, sample_reviews_buf, samples_dir, n_products=sample_products)

    return {"result": validator.result, "stats": stats, "report_path": str(report_path)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fashion_datagen", description="Synthetic fashion data generator")
    sub = parser.add_subparsers(dest="cmd", required=True)

    gen = sub.add_parser("generate", help="Generate products + reviews")
    gen.add_argument("--products", type=int, default=DEFAULT_PRODUCTS, help=f"number of products (max {MAX_PRODUCTS})")
    gen.add_argument("--small", action="store_true", help=f"dev/CI mode: {SMALL_PRODUCTS} products")
    gen.add_argument("--seed", type=int, default=42, help="deterministic seed")
    gen.add_argument("--out-dir", type=Path, default=REPO_ROOT / "data", help="output root (default ./data)")
    gen.add_argument("--parquet", action="store_true", help="also write Parquet copies")
    gen.add_argument("--quiet", action="store_true")

    val = sub.add_parser("validate", help="Validate existing JSONL files in --out-dir/raw")
    val.add_argument("--out-dir", type=Path, default=REPO_ROOT / "data")
    val.add_argument("--limit", type=int, default=0)

    args = parser.parse_args(argv)
    cfg = default_config()

    if args.cmd == "generate":
        n = SMALL_PRODUCTS if args.small else args.products
        if not 1 <= n <= MAX_PRODUCTS:
            parser.error(f"--products must be between 1 and {MAX_PRODUCTS}")
        out = generate(n_products=n, seed=args.seed, out_dir=args.out_dir, parquet=args.parquet, quiet=args.quiet)
        ok = out["result"].ok
        print(json.dumps(out["stats"], indent=2))
        print(f"Report: {out['report_path']}")
        print("VALIDATION PASS" if ok else f"VALIDATION FAIL: {out['result'].issue_counts()}")
        return 0 if ok else 1

    if args.cmd == "validate":
        from .writers import read_jsonl
        products = read_jsonl(args.out_dir / "raw" / "products.jsonl")
        reviews_by_p: dict[str, list[dict[str, Any]]] = {}
        for r in read_jsonl(args.out_dir / "raw" / "reviews.jsonl"):
            reviews_by_p.setdefault(r["product_id"], []).append(r)
        pairs = [(p, reviews_by_p.get(p["product_id"], [])) for p in products[: args.limit or None]]
        res = validate_pairs(pairs, cfg)
        print(json.dumps(res.stats, indent=2))
        print("PASS" if res.ok else f"FAIL {res.issue_counts()}")
        return 0 if res.ok else 1

    return 2


def validate_pairs(pairs: list[tuple[dict[str, Any], list[dict[str, Any]]]], cfg: Any) -> Any:
    from .validators import validate_stream
    return validate_stream(iter(pairs), cfg)


if __name__ == "__main__":
    raise SystemExit(main())
