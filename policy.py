"""Hivemind's rules: the two gates and the hold.

    Gate 1 (before contacting the shop):  check_pass(), check_seller()
    Gate 2 (on the shop's quote):         check_quote()
    The hold (sets the money aside):      place_hold()  -> mark_spent() / release()

Every rule that says no raises Denied(reason). The gateway logs the reason
and tells the agent.

Nothing in this file talks to the network, and nothing here uses `await`.
That matters most for place_hold(): see its docstring.
"""

import hashlib
import time
from urllib.parse import urlsplit

import chain
from db import conn, now


class Denied(Exception):
    """A rule said no. str(e) is the reason, in words."""


def token_hash(token):
    """The fingerprint we store instead of the token itself."""
    return hashlib.sha256(token.encode()).hexdigest()


def usdc(units):
    """Units -> a readable USDC string, for messages."""
    return f"{chain.units_to_usdc(units)} USDC"


# --- Gate 1: before we even contact the shop ----------------------------------------

def check_pass(token):
    """Is this a real pass, and is it switched on? Returns the agent's row."""
    agent = conn.execute("SELECT * FROM agents WHERE token_hash = ?",
                         (token_hash(token),)).fetchone()
    if agent is None:
        raise Denied("unknown pass")
    if not agent["enabled"]:
        raise Denied(f"pass for {agent['name']} is switched off")
    return agent


def origin_of(url):
    """http://127.0.0.1:8001/evil/report/x -> http://127.0.0.1:8001"""
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def check_seller(url):
    """Is this shop on our approved list? Returns the seller's row."""
    seller = conn.execute("SELECT * FROM sellers WHERE origin = ?", (origin_of(url),)).fetchone()
    if seller is None:
        raise Denied(f"shop {origin_of(url)} is not on the approved list")
    return seller


# --- Gate 2: the shop's quote ---------------------------------------------------------

def check_quote(quote, seller):
    """Is this a quote we'd be willing to pay? Returns the price in units."""
    if quote.get("token", "").lower() != chain.USDC_ADDRESS.lower():
        raise Denied(f"quote asks for token {quote.get('token')}, not the official USDC")
    if quote["payTo"].lower() != seller["pay_to"].lower():
        raise Denied(f"payTo {quote['payTo']} is not this shop's approved address {seller['pay_to']}")
    price = chain.usdc_to_units(quote["price"])
    if price <= 0:
        raise Denied("price must be positive")
    if price > seller["max_price_units"]:
        raise Denied(f"price {usdc(price)} is over this shop's cap of {usdc(seller['max_price_units'])}")
    if quote.get("expiry", 0) <= time.time():
        raise Denied("quote has expired")
    return price


# --- The hold ---------------------------------------------------------------------------

def place_hold(agent_name, purchase_id, amount):
    """Set `amount` units aside for this agent, if the budget and its limit allow.

    This is ONE database transaction, opened with BEGIN IMMEDIATE, which takes
    the write lock straight away. Until COMMIT, no other request (or process)
    can place a hold, so two agents can never both see the same "room left".

    It contains no network calls and no `await`, so it can't be paused halfway
    and it finishes in well under a millisecond. The slow payment happens
    afterwards, with the lock already released.

    Keeps both of these true:
        team:  spent + held + amount <= budget
        agent: spent + held + amount <= that agent's limit
    Returns the hold's id.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        # Re-check the off switch inside the lock: it may have just been flipped.
        agent = conn.execute("SELECT * FROM agents WHERE name = ?", (agent_name,)).fetchone()
        if agent is None or not agent["enabled"]:
            raise Denied(f"pass for {agent_name} is switched off")

        budget = conn.execute("SELECT budget_units FROM team WHERE id = 1").fetchone()[0]
        team_used = conn.execute(
            "SELECT COALESCE(SUM(amount_units), 0) FROM holds WHERE status IN ('held', 'spent')"
        ).fetchone()[0]
        agent_used = conn.execute(
            "SELECT COALESCE(SUM(amount_units), 0) FROM holds"
            " WHERE status IN ('held', 'spent') AND agent = ?", (agent_name,)
        ).fetchone()[0]

        if team_used + amount > budget:
            raise Denied(f"over team budget: {usdc(team_used)} of {usdc(budget)} "
                         f"already spent or held")
        if agent_used + amount > agent["limit_units"]:
            raise Denied(f"over {agent_name}'s limit: {usdc(agent_used)} of "
                         f"{usdc(agent['limit_units'])} already spent or held")

        cur = conn.execute(
            "INSERT INTO holds (agent, purchase_id, amount_units, status, created, updated)"
            " VALUES (?, ?, ?, 'held', ?, ?)",
            (agent_name, purchase_id, amount, now(), now()),
        )
        conn.execute("COMMIT")
        return cur.lastrowid
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def mark_spent(hold_id, tx_hash):
    """The payment confirmed: the held money is now spent."""
    conn.execute("UPDATE holds SET status = 'spent', tx_hash = ?, updated = ? WHERE id = ?",
                 (tx_hash, now(), hold_id))


def release(hold_id, tx_hash=None):
    """The payment failed or never happened: give the money back to the budget."""
    conn.execute("UPDATE holds SET status = 'released', tx_hash = COALESCE(?, tx_hash),"
                 " updated = ? WHERE id = ? AND status = 'held'", (tx_hash, now(), hold_id))
