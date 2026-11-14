"""The payment queue: the ONLY code that touches the wallet key and cheque numbers.

Think of one clerk who owns the team's cheque book. For every payment the clerk:
    1. takes the next cheque number (nonce) from its own counter
    2. signs the payment
    3. saves a "photocopy" (signed bytes, payment ID, number) in the database
    4. only then sends it to the chain

If a payment is rejected, the clerk fills the hole: a 0 BTC payment to
ourselves with the same number, so the payments numbered after it aren't
stuck forever waiting for it.
"""

import chain
from db import conn, log, now

WALLET = chain.load_wallet("shared")  # the team card: nobody else loads this key
_next_nonce = None

# Test switch (used by tests/gap_fill.py through the gateway): pretend the chain
# rejects the payment that gets this cheque number.
fail_nonce = None


class PaymentRejected(Exception):
    """The chain refused the payment. Its hole has been filled; release the hold."""


async def start():
    """Read the next cheque number once at startup.

    Never go below a number we've already saved: the chain doesn't count
    payments that are sent but not yet in a block.
    """
    global _next_nonce
    from_chain = await chain.w3.eth.get_transaction_count(WALLET.address, "pending")
    saved = conn.execute("SELECT MAX(nonce) FROM payments").fetchone()[0]
    _next_nonce = max(from_chain, saved + 1 if saved is not None else 0)
    return _next_nonce


def peek_nonce():
    return _next_nonce


def _take_nonce():
    """Hand out the next number. No `await` here, so no two payments share one."""
    global _next_nonce
    n = _next_nonce
    _next_nonce += 1
    return n


def _save(nonce, tx_hash, raw_tx, kind, hold_id):
    conn.execute(
        "INSERT INTO payments (nonce, tx_hash, raw_tx, kind, hold_id, status, created, updated)"
        " VALUES (?, ?, ?, ?, ?, 'signed', ?, ?)",
        (nonce, tx_hash, raw_tx, kind, hold_id, now(), now()),
    )


def mark(tx_hash, status):
    """Record what happened to a saved payment: sent / confirmed / failed / rejected."""
    conn.execute("UPDATE payments SET status = ?, updated = ? WHERE tx_hash = ?",
                 (status, now(), tx_hash))


async def _send_saved(tx_hash, raw_tx, nonce):
    """Broadcast a saved payment. 'already known' means it's already out: fine."""
    global fail_nonce
    if fail_nonce is not None and nonce == fail_nonce:
        fail_nonce = None
        raise RuntimeError("TEST: simulated rejection by the chain")
    try:
        await chain.broadcast(raw_tx)
    except Exception as e:
        if "already known" not in str(e):
            raise
    mark(tx_hash, "sent")


async def pay(hold_id, to, amount, agent=None, purchase_id=None, _retried=False):
    """Pay `amount` USDC to `to` for the hold `hold_id`. Returns the tx hash.

    Raises PaymentRejected if the chain refuses it (after filling the hole).
    """
    nonce = _take_nonce()
    tx_hash, raw_tx = await chain.sign_usdc_transfer(WALLET, to, amount, nonce)
    _save(nonce, tx_hash, raw_tx, "purchase", hold_id)  # photocopy BEFORE sending
    try:
        await _send_saved(tx_hash, raw_tx, nonce)
        return tx_hash
    except Exception as e:
        mark(tx_hash, "rejected")
        if "nonce too low" in str(e) and not _retried:
            # Something outside hivemind used this number (e.g. fund_naive.py).
            # No hole to fill: catch up with the chain and try once more.
            await start()
            return await pay(hold_id, to, amount, agent, purchase_id, _retried=True)
        log("error", f"payment with cheque #{nonce} rejected: {e}", agent=agent,
            purchase_id=purchase_id, tx_hash=tx_hash)
        await fill_gap(nonce)
        raise PaymentRejected(str(e)) from e


async def fill_gap(nonce):
    """Fill the hole left by rejected cheque `nonce` with a 0 BTC payment to ourselves."""
    tx_hash, raw_tx = await chain.sign_gap_filler(WALLET, nonce)
    _save(nonce, tx_hash, raw_tx, "gap_fill", None)
    try:
        await _send_saved(tx_hash, raw_tx, nonce)
    except Exception as e:
        mark(tx_hash, "rejected")
        log("error", f"could not fill hole at cheque #{nonce}: {e}", tx_hash=tx_hash)
        return None
    log("gap_fill", f"filled hole at cheque #{nonce} with a 0 BTC payment to ourselves",
        tx_hash=tx_hash, explorer=chain.tx_link(tx_hash))
    return tx_hash
