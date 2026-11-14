"""Purchase IDs: the same purchase can never pay twice.

The agent makes one ID per thing it wants to buy and reuses it on every retry.
The `purchases` table's PRIMARY KEY (agent, purchase_id) makes the database
itself refuse a second row, so even two requests at the same instant can't
both start the purchase.

    new ID                    -> start the purchase
    seen, still working       -> wait for the first one; return its result
    seen, done                -> return the first result again (no payment)
    seen, failed before any money moved -> allowed to try again
"""

import json
import sqlite3

from db import conn, now
from policy import Denied


def claim(agent, purchase_id, url=None):
    """Returns ("new", None), ("working", None) or ("replay", saved_result)."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute("SELECT * FROM purchases WHERE agent = ? AND purchase_id = ?",
                           (agent, purchase_id)).fetchone()
        if row is None:
            conn.execute("INSERT INTO purchases (agent, purchase_id, status, url, created, updated)"
                         " VALUES (?, ?, 'working', ?, ?, ?)", (agent, purchase_id, url, now(), now()))
            state = ("new", None)
        elif row["status"] == "failed":
            # Failed before any money moved: the agent may try again with the same ID.
            conn.execute("UPDATE purchases SET status = 'working', order_id = NULL, url = ?, updated = ?"
                         " WHERE agent = ? AND purchase_id = ?", (url, now(), agent, purchase_id))
            state = ("new", None)
        elif row["status"] == "done":
            state = ("replay", json.loads(row["result"]))
        else:
            state = ("working", None)
        conn.execute("COMMIT")
        return state
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def set_order(agent, purchase_id, order_id):
    """Remember the shop's order number. Each order can belong to only one purchase."""
    try:
        conn.execute("UPDATE purchases SET order_id = ?, updated = ? WHERE agent = ? AND purchase_id = ?",
                     (order_id, now(), agent, purchase_id))
    except sqlite3.IntegrityError:
        raise Denied(f"shop order {order_id} was already paid by another purchase") from None


def finish(agent, purchase_id, status, code, body):
    """Record how the purchase ended, and the reply, so retries get the same answer.

    status: "done"    - money moved (or was delivered): never pay again
            "failed"  - refused or rejected before any money moved: may retry
            "working" - payment sent but not confirmed: leave for crash recovery
    """
    conn.execute(
        "UPDATE purchases SET status = ?, tx_hash = ?, result = ?, updated = ?"
        " WHERE agent = ? AND purchase_id = ?",
        (status, body.get("txHash"), json.dumps({"code": code, "body": body}), now(),
         agent, purchase_id),
    )
