BEGIN;

CREATE SCHEMA IF NOT EXISTS backtest;

CREATE TABLE IF NOT EXISTS backtest.runs (
    run_id uuid PRIMARY KEY,
    status text NOT NULL CHECK (status IN ('created','running','completed','failed','cancelled')),
    started_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    config_hash text NOT NULL,
    code_hash text NOT NULL,
    data_provenance jsonb NOT NULL DEFAULT '{}'::jsonb,
    split_definition jsonb NOT NULL DEFAULT '{}'::jsonb,
    quality_summary jsonb NOT NULL DEFAULT '{}'::jsonb,
    configuration jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS backtest.fills (
    run_id uuid NOT NULL REFERENCES backtest.runs(run_id) ON DELETE CASCADE,
    fill_id uuid PRIMARY KEY,
    fill_time timestamptz NOT NULL,
    symbol text NOT NULL,
    side text NOT NULL CHECK (side IN ('buy','sell')),
    quantity numeric(38,18) NOT NULL CHECK (quantity >= 0),
    fill_price numeric(38,18) NOT NULL CHECK (fill_price >= 0),
    fee numeric(38,18) NOT NULL DEFAULT 0 CHECK (fee >= 0),
    status text NOT NULL CHECK (status IN ('filled','partial','rejected'))
);

CREATE TABLE IF NOT EXISTS backtest.equity_points (
    run_id uuid NOT NULL REFERENCES backtest.runs(run_id) ON DELETE CASCADE,
    point_time timestamptz NOT NULL,
    equity numeric(38,18) NOT NULL,
    cash numeric(38,18) NOT NULL,
    quantity numeric(38,18) NOT NULL,
    PRIMARY KEY (run_id, point_time)
);

CREATE TABLE IF NOT EXISTS backtest.metrics (
    run_id uuid PRIMARY KEY REFERENCES backtest.runs(run_id) ON DELETE CASCADE,
    profit_factor numeric(38,18),
    max_drawdown numeric(38,18),
    win_rate numeric(38,18),
    expectancy numeric(38,18),
    max_win_streak integer NOT NULL DEFAULT 0,
    max_loss_streak integer NOT NULL DEFAULT 0,
    exposure numeric(38,18),
    turnover numeric(38,18),
    recorded_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS backtest.quality_summary (
    run_id uuid PRIMARY KEY REFERENCES backtest.runs(run_id) ON DELETE CASCADE,
    finding_count integer NOT NULL DEFAULT 0,
    gap_count integer NOT NULL DEFAULT 0,
    threshold_count integer NOT NULL DEFAULT 0,
    policy text NOT NULL,
    details jsonb NOT NULL DEFAULT '{}'::jsonb
);

COMMIT;
