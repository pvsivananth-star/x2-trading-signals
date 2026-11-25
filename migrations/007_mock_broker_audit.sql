BEGIN;

CREATE TABLE IF NOT EXISTS trading.mock_quotes (
  quote_id bigserial PRIMARY KEY,
  symbol text NOT NULL,
  bid numeric(38,18) NOT NULL,
  ask numeric(38,18) NOT NULL,
  last numeric(38,18) NOT NULL,
  timestamp timestamptz NOT NULL
);

CREATE TABLE IF NOT EXISTS trading.mock_trades (
  trade_id text PRIMARY KEY,
  order_id text NOT NULL,
  signal_id text,
  symbol text NOT NULL,
  side text NOT NULL CHECK (side IN ('buy','sell')),
  quantity numeric(38,18) NOT NULL,
  price numeric(38,18) NOT NULL,
  narrative text NOT NULL,
  reasons jsonb NOT NULL DEFAULT '{}'::jsonb,
  timestamp timestamptz NOT NULL
);

CREATE TABLE IF NOT EXISTS trading.mock_orders (
  order_id text PRIMARY KEY,
  client_order_id text,
  signal_id text,
  symbol text NOT NULL,
  side text NOT NULL CHECK (side IN ('buy','sell')),
  quantity numeric(38,18) NOT NULL,
  price numeric(38,18),
  state text NOT NULL,
  narrative text NOT NULL,
  reason text,
  submitted_at timestamptz NOT NULL
);

CREATE TABLE IF NOT EXISTS trading.mock_fills (
  fill_id text PRIMARY KEY,
  order_id text NOT NULL,
  trade_id text,
  symbol text NOT NULL,
  side text NOT NULL CHECK (side IN ('buy','sell')),
  quantity numeric(38,18) NOT NULL,
  price numeric(38,18) NOT NULL,
  timestamp timestamptz NOT NULL
);

CREATE TABLE IF NOT EXISTS trading.mock_positions (
  symbol text PRIMARY KEY,
  quantity numeric(38,18) NOT NULL,
  average_cost numeric(38,18) NOT NULL,
  current_value numeric(38,18) NOT NULL,
  unrealized_pnl numeric(38,18) NOT NULL,
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS trading.mock_broker_audit (
  event_id text PRIMARY KEY,
  order_id text,
  signal_id text,
  event text NOT NULL,
  narrative text NOT NULL,
  reasons jsonb NOT NULL DEFAULT '{}'::jsonb,
  timestamp timestamptz NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS mock_orders_client_order_id_uidx
  ON trading.mock_orders(client_order_id) WHERE client_order_id IS NOT NULL;

COMMIT;
