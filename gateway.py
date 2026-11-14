"""Hivemind: the layer between the agents and the wallet.

Agents never touch the wallet key. They call one door:

    POST /buy  {url, reason, purchaseId}   with header  Authorization: Bearer <pass>

and hivemind runs the purchase:

    Intent -> Gate 1 -> Purchase ID -> Quote -> Gate 2 -> Hold
           -> Pay (payment queue) -> Confirm -> Proof -> Audit

Rules: policy.py. Purchase IDs: purchases.py. Key and cheque numbers: payqueue.py.
Every allow/deny is written to the logbook with the reason.

Run:  .venv/bin/python gateway.py      (listens on http://127.0.0.1:8000)
"""

import asyncio
import json
from contextlib import asynccontextmanager
from decimal import Decimal

import httpx
import uvicorn
from fastapi import FastAPI, Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import chain
import payqueue
import policy
import purchases
import recovery
from db import log

PORT = 8000
http = httpx.AsyncClient(timeout=30)

# Purchases running right now: (agent, purchaseId) -> the task doing the work.
# A retry with the same ID waits on this task instead of starting a new purchase.
in_flight = {}


@asynccontextmanager
async def lifespan(app):
    # Before opening the door: sort out anything a crash left half-done.
    await recovery.recover()
    nonce = await payqueue.start()
    print(f"[hivemind] shared wallet {payqueue.WALLET.address}, next cheque number {nonce}")
    yield


app = FastAPI(title="hivemind", lifespan=lifespan)


class BuyRequest(BaseModel):
    url: str         # what to buy
    reason: str      # why the agent wants it (goes in the logbook)
    purchaseId: str  # made once per purchase by the agent, reused on every retry


def fail(error, **extra):
    return {"status": "error", "error": error, **extra}


@app.post("/buy")
async def buy(req: BuyRequest, authorization: str | None = Header(default=None)):
    pid = req.purchaseId
    token = (authorization or "").removeprefix("Bearer ").strip()

    # --- Gate 1a: is the pass real and switched on? ----------------------------------
    try:
        agent = policy.check_pass(token)["name"]
    except policy.Denied as e:
        log("policy", f"DENY: {e}", agent="unknown", purchase_id=pid, url=req.url)
        return JSONResponse(status_code=401, content=fail(str(e)))

    # --- Purchase ID: have we seen this purchase before? -----------------------------
    state, saved = purchases.claim(agent, pid, req.url)
    key = (agent, pid)
    if state == "replay":
        log("dedupe", f"purchase {pid} already finished: returning the first result, NOT paying again",
            agent=agent, purchase_id=pid, tx_hash=saved["body"].get("txHash"))
        return JSONResponse(status_code=saved["code"], content={**saved["body"], "replayed": True})
    if state == "working":
        task = in_flight.get(key)
        if task is None:  # e.g. hivemind restarted mid-purchase; Step 5 resolves these
            return JSONResponse(status_code=202, content=fail("purchase still in progress, try later"))
        log("dedupe", f"purchase {pid} already in progress: waiting for it, NOT paying again",
            agent=agent, purchase_id=pid)
        code, body = await asyncio.shield(task)
        return JSONResponse(status_code=code, content={**body, "replayed": True})

    # New purchase. Run it as its own task, so it finishes even if the agent
    # hangs up (times out) halfway: the money may already be on its way.
    task = asyncio.create_task(run_purchase(agent, req))
    in_flight[key] = task
    task.add_done_callback(lambda _: in_flight.pop(key, None))
    code, body = await asyncio.shield(task)
    return JSONResponse(status_code=code, content=body)


async def run_purchase(agent, req):
    """Run one purchase and record how it ended. Returns (http_code, body)."""
    try:
        code, body, outcome = await _purchase(agent, req)
    except Exception as e:  # anything unexpected: no money moved unless a tx exists
        log("error", f"unexpected error: {e!r}", agent=agent, purchase_id=req.purchaseId)
        code, body, outcome = 500, fail("internal error"), "failed"
    purchases.finish(agent, req.purchaseId, outcome, code, body)
    return code, body


async def _purchase(agent, req):
    """The purchase itself. Returns (http_code, body, outcome) where outcome is
    'done' (money moved), 'failed' (no money moved) or 'working' (unknown yet)."""
    pid = req.purchaseId
    log("intent", f"wants {req.url} because: {req.reason}", agent=agent,
        purchase_id=pid, url=req.url, reason=req.reason)

    # --- Gate 1b: approved shop? -----------------------------------------------------
    try:
        seller = policy.check_seller(req.url)
    except policy.Denied as e:
        log("policy", f"DENY: {e}", agent=agent, purchase_id=pid)
        return 403, fail(str(e)), "failed"
    log("policy", "ALLOW gate 1: valid pass, approved shop", agent=agent, purchase_id=pid)

    # --- Quote --------------------------------------------------------------------------
    try:
        r = await http.get(req.url)
    except httpx.HTTPError as e:
        log("error", f"shop unreachable: {e!r}", agent=agent, purchase_id=pid)
        return 502, fail("shop unreachable"), "failed"
    if r.status_code != 402:
        log("error", f"expected 402 from shop, got {r.status_code}", agent=agent, purchase_id=pid)
        return 502, fail(f"shop replied {r.status_code}, not a 402 quote"), "failed"
    quote = r.json()
    log("quote", f"shop wants {quote['price']} USDC to {quote['payTo']} (order {quote['orderId']})",
        agent=agent, purchase_id=pid, **quote)

    # --- Gate 2 + order check + Hold -----------------------------------------------------
    try:
        price_units = policy.check_quote(quote, seller)
        purchases.set_order(agent, pid, quote["orderId"])
        hold_id = policy.place_hold(agent, pid, price_units)
    except policy.Denied as e:
        log("policy", f"DENY: {e}", agent=agent, purchase_id=pid)
        return 403, fail(str(e)), "failed"
    price = chain.units_to_usdc(price_units)
    log("hold", f"ALLOW gate 2: {price} USDC set aside (hold {hold_id})", agent=agent, purchase_id=pid)

    # --- Settlement: the payment queue pays (no lock held) -------------------------------
    try:
        tx_hash = await payqueue.pay(hold_id, quote["payTo"], price, agent, pid)
    except Exception as e:
        policy.release(hold_id)
        log("error", f"payment rejected, hold released: {e}", agent=agent, purchase_id=pid)
        return 502, fail("payment rejected by the chain"), "failed"
    link = chain.tx_link(tx_hash)
    log("paid", f"sent {price} USDC to {quote['payTo']}", agent=agent, purchase_id=pid,
        tx_hash=tx_hash, explorer=link)

    try:
        receipt = await chain.wait_for_receipt(tx_hash)
    except Exception as e:
        # Unknown outcome: keep the hold (the money may still leave). Step 5 recovers these.
        log("error", f"no receipt yet, hold kept: {e!r}", agent=agent, purchase_id=pid,
            tx_hash=tx_hash, explorer=link)
        return 504, fail("payment sent but not confirmed yet", txHash=tx_hash, explorer=link), "working"
    if receipt.status != 1:
        payqueue.mark(tx_hash, "failed", receipt)
        policy.release(hold_id, tx_hash)
        log("error", "payment failed on chain, hold released", agent=agent, purchase_id=pid,
            tx_hash=tx_hash, explorer=link)
        return 502, fail("payment failed on chain", txHash=tx_hash, explorer=link), "failed"
    payqueue.mark(tx_hash, "confirmed", receipt)
    policy.mark_spent(hold_id, tx_hash)
    gas_btc = Decimal(receipt.gasUsed * receipt.effectiveGasPrice) / 10**18
    log("confirmed", f"in block {receipt.blockNumber}, hold -> spent, gas {gas_btc} BTC",
        agent=agent, purchase_id=pid, tx_hash=tx_hash, explorer=link, gas_btc=gas_btc)

    # --- Proof: show the shop, collect what we bought ------------------------------------
    proof = json.dumps({"orderId": quote["orderId"], "txHash": tx_hash})
    r = await http.get(req.url, headers={"X-PAYMENT": proof})
    if r.status_code != 200:
        log("error", f"paid, but the shop refused the proof: {r.text}", agent=agent,
            purchase_id=pid, tx_hash=tx_hash, explorer=link)
        return 502, fail("paid, but the shop refused the proof", txHash=tx_hash, explorer=link), "done"

    # --- Audit ----------------------------------------------------------------------------
    log("delivered", f"got {req.url}", agent=agent, purchase_id=pid, tx_hash=tx_hash, explorer=link)
    return 200, {"status": "ok", "data": r.json(), "price": str(price),
                 "txHash": tx_hash, "explorer": link}, "done"


# --- Test switch (for tests/gap_fill.py only) ------------------------------------------
@app.post("/test/reject-next-payment")
async def reject_next_payment():
    """Make the chain 'reject' the next cheque number, to test hole-filling."""
    payqueue.fail_nonce = payqueue.peek_nonce()
    return {"willReject": payqueue.fail_nonce}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
