# data/ — Generated Datasets & Configuration

All generated artefacts live here, strictly separated from code. The generator that
fills this tree is [`../data_generation/`](../data_generation/README.md)
(`python -m fashion_datagen`, or `make data` / `make data-small` from the repo root).

```
data/
├── config/        # committed YAMLs that define the synthetic world (edit these, not code)
│   ├── taxonomy.yaml           # categories → subcategories → attributes, category weights
│   ├── brands.yaml             # brands per segment with tier: budget | mid | premium
│   ├── price_bands.yaml        # min/max selling price per subcategory × brand tier
│   └── review_templates.yaml   # sentiment/subcategory/attribute phrase banks for reviews
├── raw/           # GITIGNORED — full generated datasets (large)
│   ├── products.jsonl          # one product per line (schema: docs/DATA_SCHEMA.md)
│   ├── reviews.jsonl           # 3–15 reviews per product, linked by product_id
│   └── *.parquet               # optional copies when generated with --parquet
├── samples/       # COMMITTED — first 100 products + their reviews, used by unit tests
│   ├── products_sample.jsonl
│   └── reviews_sample.jsonl
└── reports/       # validation output (regenerated on every run)
    ├── validation_report.md    # distributions, price histograms, uniqueness, PASS/FAIL
    └── stats.json              # machine-readable counts/shares/ratios
```

## Rules of the road

- **Never commit `raw/`** — it is gitignored and can hold up to 500k products / ~2.5M reviews.
- **`samples/` is the test fixture** — regenerate it together with a dataset run; keep it
  deterministic (`--seed 42`) so CI is stable.
- **Everything is reproducible**: same seed + same `config/` ⇒ byte-identical output.
  Change the world by editing the YAMLs in `config/`, not the generator code.
- Full field-by-field documentation of the product/review schemas is in
  [`../docs/DATA_SCHEMA.md`](../docs/DATA_SCHEMA.md); design rationale lives in
  [`../docs/DECISIONS.md`](../docs/DECISIONS.md).

## Regenerating

```bash
cd ..            # repo root
make data-small  # 2,000 products (dev/CI default)
make data        # 100,000 products
```
