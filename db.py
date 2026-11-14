"""Hivemind's database (SQLite, one file: hivemind.db).

For now it holds only the logbook: an append-only record of every stage of
every purchase. Rows are only ever added, never edited or deleted.
Later steps add budgets, holds, orders and purchase IDs to the same file.

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
    stage       TEXT NOT NULL,     -- intent / policy / quote / paid / confirmed / delivered / error
    message     TEXT NOT NULL,     -- what happened, in words
    tx_hash     TEXT,              -- payment ID, once there is one
    explorer    TEXT,              -- link to see the payment
    detail      TEXT               -- anything else, as JSON
);
"""

conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row
conn.executescript(SCHEMA)


def log(stage, message, agent=None, purchase_id=None, tx_hash=None, explorer=None, **detail):
    """Add one row to the logbook (and print it, so it shows in the terminal)."""
    ts = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
    with conn:  # commits the insert
        conn.execute(
            "INSERT INTO log (ts, agent, purchase_id, stage, message, tx_hash, explorer, detail)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (ts, agent, purchase_id, stage, message, tx_hash, explorer,
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
