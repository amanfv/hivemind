"""Crash recovery: runs once when hivemind starts, BEFORE it accepts any request.

After a crash (e.g. kill -9) only the database survives. This goes through
everything left half-done and sorts each purchase into one of four cases:

  1. Money held, no photocopy           -> crashed before writing the cheque.
                                           Release the hold; the agent may retry.
  2. Photocopy, cashed on chain         -> it went through, we just never heard.
                                           Mark it spent and finish the job
                                           (show the shop the proof, collect the report).
  3. Photocopy, not cashed, number free -> mail the SAME copy again. That cheque
                                           number can only be cashed once, so this
                                           can't pay twice. Then it's case 2.
  4. Photocopy, but its number was      -> this cheque can never be cashed now.
     cashed by a different cheque          Release the hold.

Then any cheque number below our counter that nothing will ever cash is filled
(the Step 4 hole-filler), so later payments can't get stuck.
"""

import json

import httpx

import chain
import payqueue
import policy
import purchases
from db import conn, log

RECEIPT_WAIT = 90  # seconds to wait for a resent payment to be mined


async def recover():
    summary = {"released": 0, "confirmed": 0, "resent": 0, "replaced": 0, "delivered": 0, "filled": 0}
    pending = conn.execute(
        "SELECT * FROM payments WHERE status IN ('signed', 'sent') ORDER BY nonce, id").fetchall()

    # --- Pass 1: for each unfinished photocopy, ask the chain what happened ----------
    # (in cheque-number order, so resent cheques arrive in the order they must be cashed)
    to_wait = []
    for p in pending:
        receipt = await chain.get_receipt(p["tx_hash"])
        if receipt is not None:
            to_wait.append(p)                       # case 2: already cashed
            continue
        cashed = await chain.w3.eth.get_transaction_count(payqueue.WALLET.address, "latest")
        if cashed > p["nonce"] and await chain.get_receipt(p["tx_hash"]) is None:
            _replaced(p, summary)                   # case 4: number used by another cheque
            continue
        try:                                        # case 3: mail the same copy again
            await chain.broadcast(p["raw_tx"])
        except Exception as e:
            if "already known" not in str(e):
                log("recovery", f"resending cheque #{p['nonce']} failed: {e}", tx_hash=p["tx_hash"])
                continue
        payqueue.mark(p["tx_hash"], "sent")
        summary["resent"] += 1
        log("recovery", f"cheque #{p['nonce']} not cashed: resent the SAME signed copy",
            tx_hash=p["tx_hash"], explorer=chain.tx_link(p["tx_hash"]))
        to_wait.append(p)

    # --- Pass 2: wait for those to be cashed, then settle each one -------------------
    for p in to_wait:
        try:
            receipt = await chain.wait_for_receipt(p["tx_hash"], timeout=RECEIPT_WAIT)
        except Exception:
            log("recovery", f"cheque #{p['nonce']} still not cashed; will look again next start",
                tx_hash=p["tx_hash"])
            continue
        await _settle(p, receipt, summary)

    # --- Pass 3: purchases still marked 'working' that pass 1-2 didn't settle -------
    for pu in conn.execute("SELECT * FROM purchases WHERE status = 'working'").fetchall():
        hold = conn.execute("SELECT * FROM holds WHERE agent = ? AND purchase_id = ?"
                            " ORDER BY id DESC LIMIT 1", (pu["agent"], pu["purchase_id"])).fetchone()
        has_copy = hold is not None and conn.execute(
            "SELECT 1 FROM payments WHERE hold_id = ? AND kind = 'purchase'", (hold["id"],)).fetchone()
        if hold is None or (hold["status"] == "held" and not has_copy):
            # case 1: crashed before any cheque was written
            if hold is not None:
                policy.release(hold["id"])
            _fail(pu["agent"], pu["purchase_id"], "hivemind restarted before paying; nothing was paid")
            summary["released"] += 1
            log("recovery", "no cheque was written before the crash: hold released, agent may retry",
                agent=pu["agent"], purchase_id=pu["purchase_id"])
        elif hold["status"] == "spent":
            await _deliver(pu, hold, summary)     # paid, crashed before collecting the report
        elif hold["status"] == "released":
            _fail(pu["agent"], pu["purchase_id"], "payment did not go through; nothing was paid")

    # --- Pass 4: fill any hole below our counter ----------------------------------------
    summary["filled"] = await _fill_holes()

    log("recovery", "done: " + ", ".join(f"{k} {v}" for k, v in summary.items()))
    return summary


def _replaced(p, summary):
    """Case 4: this cheque's number was cashed by a different cheque."""
    payqueue.mark(p["tx_hash"], "rejected")
    summary["replaced"] += 1
    if p["kind"] == "purchase" and p["hold_id"] is not None:
        policy.release(p["hold_id"])
        hold = conn.execute("SELECT * FROM holds WHERE id = ?", (p["hold_id"],)).fetchone()
        _fail(hold["agent"], hold["purchase_id"], "payment was never cashed; nothing was paid")
    log("recovery", f"cheque #{p['nonce']} can never be cashed (number used): hold released",
        tx_hash=p["tx_hash"])


async def _settle(p, receipt, summary):
    """Case 2 (and 3 once mined): record the outcome and finish the purchase."""
    ok = receipt.status == 1
    payqueue.mark(p["tx_hash"], "confirmed" if ok else "failed", receipt)
    if p["kind"] != "purchase" or p["hold_id"] is None:
        return
    hold = conn.execute("SELECT * FROM holds WHERE id = ?", (p["hold_id"],)).fetchone()
    if not ok:
        policy.release(hold["id"], p["tx_hash"])
        _fail(hold["agent"], hold["purchase_id"], "payment failed on chain; hold released")
        return
    policy.mark_spent(hold["id"], p["tx_hash"])
    hold = conn.execute("SELECT * FROM holds WHERE id = ?", (p["hold_id"],)).fetchone()  # now has tx_hash
    summary["confirmed"] += 1
    log("recovery", f"cheque #{p['nonce']} was cashed: hold -> spent", agent=hold["agent"],
        purchase_id=hold["purchase_id"], tx_hash=p["tx_hash"], explorer=chain.tx_link(p["tx_hash"]))
    pu = conn.execute("SELECT * FROM purchases WHERE agent = ? AND purchase_id = ?",
                      (hold["agent"], hold["purchase_id"])).fetchone()
    if pu is not None and pu["status"] == "working":
        await _deliver(pu, hold, summary)


async def _deliver(pu, hold, summary):
    """Paid but the report was never collected: show the shop the proof now."""
    tx_hash = hold["tx_hash"]
    link = chain.tx_link(tx_hash)
    price = str(chain.units_to_usdc(hold["amount_units"]))
    proof = json.dumps({"orderId": pu["order_id"], "txHash": tx_hash})
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(pu["url"], headers={"X-PAYMENT": proof})
        if r.status_code == 200:
            body = {"status": "ok", "data": r.json(), "price": price, "txHash": tx_hash,
                    "explorer": link, "recovered": True}
            purchases.finish(pu["agent"], pu["purchase_id"], "done", 200, body)
            summary["delivered"] += 1
            log("recovery", "paid before the crash: collected the report from the shop",
                agent=pu["agent"], purchase_id=pu["purchase_id"], tx_hash=tx_hash, explorer=link)
            return
        error = f"shop refused the proof: {r.text}"
    except httpx.HTTPError as e:
        error = f"shop unreachable: {e!r}"
    # Money moved, so this is 'done' either way: a retry must never pay again.
    body = {"status": "error", "error": f"paid, but {error}", "txHash": tx_hash, "explorer": link}
    purchases.finish(pu["agent"], pu["purchase_id"], "done", 502, body)
    log("recovery", f"paid before the crash, but {error}", agent=pu["agent"],
        purchase_id=pu["purchase_id"], tx_hash=tx_hash, explorer=link)


def _fail(agent, purchase_id, error):
    """Record that no money moved, so the agent may retry with the same ID."""
    purchases.finish(agent, purchase_id, "failed", 503, {"status": "error", "error": error})


async def _fill_holes():
    """Any cheque number between 'cashed so far' and our highest copy that has no
    live cheque would block every later payment forever: fill it."""
    cashed = await chain.w3.eth.get_transaction_count(payqueue.WALLET.address, "latest")
    top = conn.execute("SELECT MAX(nonce) FROM payments").fetchone()[0]
    filled = 0
    if top is None:
        return 0
    for n in range(cashed, top + 1):
        live = conn.execute("SELECT 1 FROM payments WHERE nonce = ? AND status IN ('sent', 'confirmed')",
                            (n,)).fetchone()
        if not live:
            await payqueue.fill_gap(n)
            filled += 1
    return filled
