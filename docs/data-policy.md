# Market data policy

## Rule: licensed market data is never committed to this repository

This repository is public. Exchange data (for example NASDAQ TotalView, or Coinbase exports
under a vendor licence) must not be committed, pushed, or attached to pull requests, even if it
is "chopped" for database tables. Each source's `license_reference` in `ingestion_sources` defines
what may be stored and where.

## How backtests read local data

1. Keep the bar files outside the repository checkout, for example `~/x2-data/2026-11-19_to_25/`.
2. Set `X2_REAL_DATA_DIR` to that directory.
3. `app/real_data.py` reads `bars_1.csv` from that directory. It refuses any directory inside the
   repository.
4. Run the tests. The real-data checks in `tests/unit/test_real_data.py` run only when the
   variable is set, so CI stays green without the licensed files.

```
export X2_REAL_DATA_DIR="$HOME/x2-data/2026-11-19_to_25"
python -m unittest tests.unit.test_real_data -v
```

## Before any file is trusted for results

- Record the source name, licence reference, and the raw object checksum (SHA-256) from
  `raw_objects`. The checksum must match the file actually loaded. Verification is not yet
  automated; check it by hand.
- Check that `bars_1.csv` has no rows outside expected sessions, and that the aggregate volume
  agrees with any other feed used for the same day. The BTC 21 Nov volume difference noted in
  the data review is still open.
- Reject files whose trades or quotes show generated patterns (constant spreads, fixed sequence
  steps). Flag them as synthetic in the run metadata instead.

## Synthetic fixtures

Files under `data/fixtures/` are deterministic synthetic test data and are labelled
`deterministic_synthetic_test_fixture` in their manifests. They may be committed. They do not
support performance claims.
