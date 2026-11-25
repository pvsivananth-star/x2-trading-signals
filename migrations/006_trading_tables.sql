BEGIN;

CREATE SCHEMA IF NOT EXISTS trading;

CREATE TABLE IF NOT EXISTS trading.orders (
  order_id text PRIMARY KEY,
  signal_id text NOT NULL,
  symbol text NOT NULL,
  side text NOT NULL CHECK (side IN ('buy','sell')),
  quantity numeric(38,18) NOT NULL CHECK (quantity > 0),
  order_type text NOT NULL,
  state text NOT NULL,
  submitted_at timestamptz NOT NULL,
  limit_price numeric(38,18),
  reason text,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS trading.fills (
  fill_id text PRIMARY KEY,
  order_id text NOT NULL REFERENCES trading.orders(order_id) ON DELETE CASCADE,
  signal_id text NOT NULL,
  symbol text NOT NULL,
  side text NOT NULL CHECK (side IN ('buy','sell')),
  quantity numeric(38,18) NOT NULL CHECK (quantity > 0),
  fill_price numeric(38,18) NOT NULL CHECK (fill_price > 0),
  fee numeric(38,18) NOT NULL CHECK (fee >= 0),
  fill_time timestamptz NOT NULL
);

CREATE TABLE IF NOT EXISTS trading.positions (
  symbol text PRIMARY KEY,
  quantity numeric(38,18) NOT NULL,
  average_cost numeric(38,18) NOT NULL CHECK (average_cost >= 0),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS trading.equity_snapshots (
  snapshot_id text PRIMARY KEY,
  snapshot_time timestamptz NOT NULL,
  cash numeric(38,18) NOT NULL,
  positions_value numeric(38,18) NOT NULL,
  unrealized_pnl numeric(38,18) NOT NULL,
  realized_pnl numeric(38,18) NOT NULL,
  equity numeric(38,18) NOT NULL
);

CREATE TABLE IF NOT EXISTS trading.audit_cards (
  card_id text PRIMARY KEY,
  timestamp timestamptz NOT NULL,
  signal_id text NOT NULL,
  order_id text NOT NULL,
  event text NOT NULL,
  payload jsonb NOT NULL DEFAULT '{}'::jsonb
);

COMMIT;
