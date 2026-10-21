# fashion_datagen — Synthetic Fashion Data Generator

Deterministic, seeded, **template- and rule-based** generator for a realistic Indian
fashion e-commerce catalogue: products (JSONL) plus 3–15 reviews per product.
No LLM calls are made per row — generation is fast (~thousands of products/s), fully
reproducible with `--seed`, and streams to disk in chunks so 500k products don't
blow memory.

This package produces everything under `../data/`:

| Output | Path | Notes |
|---|---|---|
| Products | `data/raw/products.jsonl` | one JSON object per line (gitignored) |
| Reviews | `data/raw/reviews.jsonl` | linked by `product_id` (gitignored) |
| Parquet copies | `data/raw/*.parquet` | optional, `--parquet` (needs `pyarrow`) |
| Committed samples | `data/samples/products_sample.jsonl`, `reviews_sample.jsonl` | first 100 products + their reviews, used by tests |
| Validation report | `data/reports/validation_report.md` | distributions, histograms, pass/fail |
| Machine stats | `data/reports/stats.json` | counts, shares, uniqueness ratio |

## Install / run

The package uses a `src/` layout; no install required if you set `PYTHONPATH`:

```bash
cd data_generation
PYTHONPATH=src python -m fashion_datagen generate --small            # 2,000 products (dev/CI)
PYTHONPATH=src python -m fashion_datagen generate --products 100000  # default full run
PYTHONPATH=src python -m fashion_datagen generate --seed 7           # different world
```

Or install it into your environment (editable):

```bash
pip install -e ".[dev]"          # adds pytest/ruff/mypy extras
fashion-datagen --help 2>/dev/null || python -m fashion_datagen --help
```

From the repo root, the Makefile targets wrap the same commands:

```bash
make data-small      # 2,000 products, seed 42
make data            # 100,000 products, seed 42
```

CLI options for `generate`:

- `--products N` — number of products (1 … 500,000; default 100,000)
- `--small` — shortcut for 2,000 products
- `--seed S` — deterministic seed (default 42); same seed ⇒ byte-identical output
- `--out-dir DIR` — data root (default `./data` at repo root)
- `--parquet` — also write Parquet copies
- `--quiet` — suppress progress lines

`validate` re-checks existing `data/raw/*.jsonl` files without regenerating:

```bash
PYTHONPATH=src python -m fashion_datagen validate [--limit N]
```

Exit code is `0` only when all validators pass.

## Module map

```
src/fashion_datagen/
├── cli.py               # argparse entry point, streaming loop, report writer
├── taxonomy.py          # loads YAML configs; weighted pick helpers (REPO_ROOT, default_config)
├── product_generator.py # title/description/attribute synthesis, category quota plan
├── review_generator.py  # rating-driven, product-aware review text (composable phrase banks)
├── pricing.py           # brand-tier × subcategory price bands, discount logic
├── validators.py        # hard data-quality rules, issue collection, stats
└── writers.py           # chunked JSONL/Parquet writers, sample exporter
```

Config lives outside code, in `../data/config/`:

- `taxonomy.yaml` — categories → subcategories → attributes (colors, fabrics, fits, occasions…)
- `brands.yaml` — brands per segment with tier (budget / mid / premium)
- `price_bands.yaml` — min/max selling price per subcategory × tier
- `review_templates.yaml` — phrase banks keyed by sentiment, subcategory, and attribute

Edit these YAMLs to change the world; no code changes needed.

## How realism is enforced

1. **Category mix** — a quota plan (`build_category_plan`) allocates Men ≈30%,
   Women ≈35%, Kids ≈20%, Accessories ≈15% before generation starts.
2. **Pricing** — selling price is sampled inside the brand-tier band for the
   subcategory; `price_selling ≤ price_mrp`; `discount_percent` is derived from
   the two prices (never inconsistent).
3. **Reviews match ratings** — 5★ draws from positive banks, 1–2★ from complaint
   banks (fabric, fit, colour fade, stitching, delivery), 3–4★ mixes both.
4. **Reviews match products** — phrase banks are keyed by subcategory and product
   attributes, so a shirt review can never mention heels; size feedback only
   appears for sized apparel.
5. **Derived aggregates** — `rating_avg`, `rating_count`, and `rating_distribution`
   on each product are computed *from its generated reviews* after the fact
   (`Validators.finalize_product_rating`), so they are exactly consistent.
6. **Uniqueness** — titles embed brand + attribute combinations and are checked
   globally; review comments are assembled from many composable fragments and the
   comment-uniqueness ratio is reported.

All of the above are asserted in `validators.py`; violations fail the run with a
non-zero exit code and appear in the report.

## Reproducibility & testing

- Generation is seeded per-stage (`ProductGenerator`, `ReviewGenerator`, plan RNG),
  so `--seed 42` twice yields identical files. A unit test asserts this.
- Tests live in `tests/` and cover the generators, determinism, and the
  rating-derivation consistency:

```bash
cd data_generation && PYTHONPATH=src python -m pytest
```

## Troubleshooting

- **`ModuleNotFoundError: fashion_datagen`** — run from `data_generation/` with
  `PYTHONPATH=src`, or `pip install -e .`.
- **`--parquet` fails** — install the extra: `pip install "fashion-datagen[parquet]"`.
- **Validation FAIL** — open `data/reports/validation_report.md`; the first 20
  issues are listed with kind, id, and message. Usually caused by editing config
  YAMLs into an invalid state (e.g. a subcategory missing from `price_bands.yaml`).
