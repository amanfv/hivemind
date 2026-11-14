"""Hivemind's database (SQLite, one file: hivemind.db).

Tables:
    log      - the logbook: append-only record of every stage of every purchase
    team     - the team budget (one row)
    agents   - each agent's pass: a fingerprint (hash) of its token, its limit, on/off
    sellers  - approved shops: web address -> the address they must be paid at, price cap
    holds    - one row per purchase: money set aside -> spent or released

Money is stored in USDC *units* (1 USDC = 1_000_000), the same whole numbers
the chain uses, so there's no rounding.

    .venv/bin/python db.py    # print the logbook
"""

import json
import sqlite3
from datetime import datetime, timezone

DB_PATH = "hivemind.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL,     -- when (UTC)
    agent       TEXT,              -- which agent asked
    purchase_id TEXT,              -- the agent's ID for this purchase
    stage       TEXT NOT NULL,     -- intent / policy / quote / hold / paid / confirmed / delivered / error
    message     TEXT NOT NULL,     -- what happened, in words (for denials: why)
    tx_hash     TEXT,              -- payment ID, once there is one
    explorer    TEXT,              -- link to see the payment
    detail      TEXT               -- anything else, as JSON
);

CREATE TABLE IF NOT EXISTS team (
    id           INTEGER PRIMARY KEY CHECK (id = 1),  -- only ever one row
    budget_units INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS agents (
    name        TEXT PRIMARY KEY,
    token_hash  TEXT NOT NULL UNIQUE,   -- sha256 of the token, never the token itself
    limit_units INTEGER NOT NULL,       -- most this agent may ever spend
    enabled     INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS sellers (
    origin          TEXT PRIMARY KEY,   -- e.g. http://127.0.0.1:8001
    pay_to          TEXT NOT NULL,      -- the ONLY address this shop may be paid at
    max_price_units INTEGER NOT NULL    -- price cap per purchase
);

CREATE TABLE IF NOT EXISTS holds (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    agent        TEXT NOT NULL,
    purchase_id  TEXT,
    amount_units INTEGER NOT NULL,
    status       TEXT NOT NULL CHECK (status IN ('held', 'spent', 'released')),
    tx_hash      TEXT,
    created      TEXT NOT NULL,
    updated      TEXT NOT NULL
);
"""

# isolation_level=None: we open transactions ourselves (BEGIN IMMEDIATE in policy.py).
conn = sqlite3.connect(DB_PATH, isolation_level=None)
conn.row_factory = sqlite3.Row
conn.execute("PRAGMA journal_mode=WAL")   # lets admin.py read/write while the gateway runs
conn.execute("PRAGMA busy_timeout=5000")  # wait (don't fail) if another process holds the lock
conn.executescript(SCHEMA)


def now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def log(stage, message, agent=None, purchase_id=None, tx_hash=None, explorer=None, **detail):
    """Add one row to the logbook (and print it, so it shows in the terminal)."""
    conn.execute(
        "INSERT INTO log (ts, agent, purchase_id, stage, message, tx_hash, explorer, detail)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (now(), agent, purchase_id, stage, message, tx_hash, explorer,
         json.dumps(detail, default=str) if detail else None),
    )
    print(f"[hivemind] {stage:<9} {agent or '-'}  {message}" + (f"  {explorer}" if explorer else ""))


def read_log(limit=50):
    """The most recent logbook rows, oldest first."""
    rows = conn.execute("SELECT * FROM log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return list(reversed(rows))


if __name__ == "__main__":
    for r in read_log():
        line = f"{r['ts']}  {r['stage']:<9} {r['agent'] or '-':<8} {r['message']}"
        if r["explorer"]:
            line += f"\n{'':>36}{r['explorer']}"
        print(line)
