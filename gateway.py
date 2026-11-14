"""Hivemind: the layer between the agents and the wallet.

Agents never touch the wallet key. They call one door:

    POST /buy  {url, reason, purchaseId}   with header  Authorization: Bearer <pass>

and hivemind runs the purchase:

    Intent -> Gate 1 -> Quote -> Gate 2 -> Hold -> Pay -> Confirm -> Proof -> Audit

Rules live in policy.py. Every allow/deny is written to the logbook with the reason.
Still to come (Step 4): purchase-ID dedupe and the full payment queue.

Run:  .venv/bin/python gateway.py      (listens on http://127.0.0.1:8000)
"""

import json
from contextlib import asynccontextmanager
from decimal import Decimal

import httpx
import uvicorn
from fastapi import FastAPI, Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import chain
import policy
from db import log

PORT = 8000

SHARED = chain.load_wallet("shared")  # the team card: only hivemind holds this key
http = httpx.AsyncClient(timeout=30)


# --- Cheque numbers (nonces) -----------------------------------------------------------
# GOAT's RPC gives every caller the same "next nonce" until a block lands, so we
# read it ONCE at startup and count up ourselves. take_nonce() has no `await`,
# so two purchases can never get the same number. (Step 4 turns this into the
# full payment queue: save before sending, refill gaps, recover after a crash.)
_next_nonce = None


async def sync_nonce():
    global _next_nonce
    _next_nonce = await chain.w3.eth.get_transaction_count(SHARED.address, "pending")


def take_nonce():
    global _next_nonce
    n = _next_nonce
    _next_nonce += 1
    return n


async def pay(to, price):
    """Pay with our own cheque number. If something else used the wallet meanwhile
    (e.g. fund_naive.py) and our number is stale, re-read it once and retry."""
    try:
        return await chain.pay_usdc(SHARED, to, price, take_nonce())
    except Exception as e:
        if "nonce too low" not in str(e):
            raise
        await sync_nonce()
        return await chain.pay_usdc(SHARED, to, price, take_nonce())


@asynccontextmanager
async def lifespan(app):
    await sync_nonce()
    print(f"[hivemind] shared wallet {SHARED.address}, next cheque number {_next_nonce}")
    yield


app = FastAPI(title="hivemind", lifespan=lifespan)


class BuyRequest(BaseModel):
    url: str         # what to buy
    reason: str      # why the agent wants it (goes in the logbook)
    purchaseId: str  # the agent's own ID for this purchase (dedupe comes in Step 4)


def fail(status, error, **extra):
    return JSONResponse(status_code=status, content={"status": "error", "error": error, **extra})


@app.post("/buy")
async def buy(req: BuyRequest, authorization: str | None = Header(default=None)):
    pid = req.purchaseId
    token = (authorization or "").removeprefix("Bearer ").strip()

    # --- Intent + Gate 1: valid pass? approved shop? (before contacting the shop) ---
    try:
        agent = policy.check_pass(token)["name"]
    except policy.Denied as e:
        log("policy", f"DENY: {e}", agent="unknown", purchase_id=pid, url=req.url)
        return fail(401, str(e))
    log("intent", f"wants {req.url} because: {req.reason}", agent=agent,
        purchase_id=pid, url=req.url, reason=req.reason)
    try:
        seller = policy.check_seller(req.url)
    except policy.Denied as e:
        log("policy", f"DENY: {e}", agent=agent, purchase_id=pid)
        return fail(403, str(e))
    log("policy", "ALLOW gate 1: valid pass, approved shop", agent=agent, purchase_id=pid)

    # --- Quote: ask the shop what it costs ---------------------------------------
    try:
        r = await http.get(req.url)
    except httpx.HTTPError as e:
        log("error", f"shop unreachable: {e!r}", agent=agent, purchase_id=pid)
        return fail(502, "shop unreachable")
    if r.status_code != 402:
        log("error", f"expected 402 from shop, got {r.status_code}", agent=agent, purchase_id=pid)
        return fail(502, f"shop replied {r.status_code}, not a 402 quote")
    quote = r.json()
    log("quote", f"shop wants {quote['price']} USDC to {quote['payTo']} (order {quote['orderId']})",
        agent=agent, purchase_id=pid, **quote)

    # --- Gate 2 + Hold: is the quote OK? is there room? set the money aside ------
    try:
        price_units = policy.check_quote(quote, seller)
        hold_id = policy.place_hold(agent, pid, price_units)
    except policy.Denied as e:
        log("policy", f"DENY: {e}", agent=agent, purchase_id=pid)
        return fail(403, str(e))
    price = chain.units_to_usdc(price_units)
    log("hold", f"ALLOW gate 2: {price} USDC set aside (hold {hold_id})", agent=agent, purchase_id=pid)

    # --- Settlement: pay (no lock held), wait for the receipt --------------------
    try:
        tx_hash = await pay(quote["payTo"], price)
    except Exception as e:
        policy.release(hold_id)
        log("error", f"payment rejected, hold released: {e}", agent=agent, purchase_id=pid)
        return fail(502, "payment rejected by the chain")
    link = chain.tx_link(tx_hash)
    log("paid", f"sent {price} USDC to {quote['payTo']}", agent=agent, purchase_id=pid,
        tx_hash=tx_hash, explorer=link)

    try:
        receipt = await chain.wait_for_receipt(tx_hash)
    except Exception as e:
        # Unknown outcome: keep the hold (the money may still leave). Step 5 recovers these.
        log("error", f"no receipt yet, hold kept: {e!r}", agent=agent, purchase_id=pid,
            tx_hash=tx_hash, explorer=link)
        return fail(504, "payment sent but not confirmed yet", txHash=tx_hash, explorer=link)
    if receipt.status != 1:
        policy.release(hold_id, tx_hash)
        log("error", "payment failed on chain, hold released", agent=agent, purchase_id=pid,
            tx_hash=tx_hash, explorer=link)
        return fail(502, "payment failed on chain", txHash=tx_hash, explorer=link)
    policy.mark_spent(hold_id, tx_hash)
    gas_btc = Decimal(receipt.gasUsed * receipt.effectiveGasPrice) / 10**18
    log("confirmed", f"in block {receipt.blockNumber}, hold -> spent, gas {gas_btc} BTC",
        agent=agent, purchase_id=pid, tx_hash=tx_hash, explorer=link, gas_btc=gas_btc)

    # --- Proof: show the shop, collect what we bought -----------------------------
    proof = json.dumps({"orderId": quote["orderId"], "txHash": tx_hash})
    r = await http.get(req.url, headers={"X-PAYMENT": proof})
    if r.status_code != 200:
        log("error", f"paid, but the shop refused the proof: {r.text}", agent=agent,
            purchase_id=pid, tx_hash=tx_hash, explorer=link)
        return fail(502, "paid, but the shop refused the proof", txHash=tx_hash, explorer=link)

    # --- Audit ----------------------------------------------------------------------
    log("delivered", f"got {req.url}", agent=agent, purchase_id=pid, tx_hash=tx_hash, explorer=link)
    return {"status": "ok", "data": r.json(), "price": str(price), "txHash": tx_hash, "explorer": link}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
