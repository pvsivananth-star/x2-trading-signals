#!/usr/bin/env bash
set -euo pipefail

PYTHON="${PYTHON:-python}"
exec "$PYTHON" -m unittest discover -s tests -p 'test_backtest_category_coverage.py' -v
