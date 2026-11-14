"""Hivemind: the layer between the agents and the wallet.

Agents never touch the wallet key. They call one door:

    POST /buy  {url, reason, purchaseId}   with header  Authorization: Bearer <token>

and hivemind runs the purchase:  Intent -> Policy -> Quote -> Settlement -> Audit.

Step 1 version: the only rule is "is the badge (token) valid?". Budgets, limits,
approved addresses, purchase-ID dedupe and the payment queue come in Steps 3-4.

Run:  .venv/bin/python gateway.py      (listens on http://127.0.0.1:8000)
"""

import json
from decimal import Decimal

import httpx
import uvicorn
from fastapi import FastAPI, Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import chain
from db import log

PORT = 8000

# Badges: token -> agent name. Step 3 moves these into the database with limits.
TOKENS = {"tok-agent1-demo": "agent1"}

SHARED = chain.load_wallet("shared")  # the team card: only hivemind holds this key
http = httpx.AsyncClient(timeout=30)

app = FastAPI(title="hivemind")


class BuyRequest(BaseModel):
    url: str         # what to buy
    reason: str      # why the agent wants it (goes in the logbook)
    purchaseId: str  # the agent's own ID for this purchase (dedupe comes in Step 4)


def fail(status, error, **extra):
    return JSONResponse(status_code=status, content={"status": "error", "error": error, **extra})


@app.post("/buy")
async def buy(req: BuyRequest, authorization: str | None = Header(default=None)):
    pid = req.purchaseId

    # --- 1. Intent: who is asking, for what, and why --------------------------
    token = (authorization or "").removeprefix("Bearer ").strip()
    agent = TOKENS.get(token)
    log("intent", f"wants {req.url} because: {req.reason}", agent=agent or "unknown",
        purchase_id=pid, url=req.url, reason=req.reason)

    # --- 2. Policy (gate 1): is the badge valid? --------------------------------
    if agent is None:
        log("policy", "DENY: invalid token", agent="unknown", purchase_id=pid)
        return fail(401, "invalid token")
    log("policy", "ALLOW: valid token", agent=agent, purchase_id=pid)

    # --- 3. Quote: ask the shop what it costs ------------------------------------
    try:
        r = await http.get(req.url)
    except httpx.HTTPError as e:
        log("error", f"shop unreachable: {e!r}", agent=agent, purchase_id=pid)
        return fail(502, "shop unreachable")
    if r.status_code != 402:
        log("error", f"expected 402 from shop, got {r.status_code}", agent=agent, purchase_id=pid)
        return fail(502, f"shop replied {r.status_code}, not a 402 quote")
    quote = r.json()
    price = Decimal(quote["price"])
    log("quote", f"shop wants {price} USDC to {quote['payTo']} (order {quote['orderId']})",
        agent=agent, purchase_id=pid, **quote)

    # --- 4. Settlement: pay on GOAT, wait for the receipt, show the shop ---------
    # Step 1 pays whatever the quote says. Step 3 adds the checks
    # (price cap, approved payTo, official token, budget hold) right here.
    tx_hash = await chain.pay_usdc(SHARED, quote["payTo"], price)
    link = chain.tx_link(tx_hash)
    log("paid", f"sent {price} USDC to {quote['payTo']}", agent=agent, purchase_id=pid,
        tx_hash=tx_hash, explorer=link)

    receipt = await chain.wait_for_receipt(tx_hash)
    if receipt.status != 1:
        log("error", "payment failed on chain", agent=agent, purchase_id=pid,
            tx_hash=tx_hash, explorer=link)
        return fail(502, "payment failed on chain", txHash=tx_hash, explorer=link)
    gas_btc = Decimal(receipt.gasUsed * receipt.effectiveGasPrice) / 10**18
    log("confirmed", f"in block {receipt.blockNumber}, gas {gas_btc} BTC", agent=agent,
        purchase_id=pid, tx_hash=tx_hash, explorer=link, gas_btc=gas_btc)

    proof = json.dumps({"orderId": quote["orderId"], "txHash": tx_hash})
    r = await http.get(req.url, headers={"X-PAYMENT": proof})
    if r.status_code != 200:
        log("error", f"shop refused the proof: {r.text}", agent=agent, purchase_id=pid,
            tx_hash=tx_hash, explorer=link)
        return fail(502, "paid, but the shop refused the proof", txHash=tx_hash, explorer=link)

    # --- 5. Audit: record the delivery and hand the result back ------------------
    log("delivered", f"got {req.url}", agent=agent, purchase_id=pid,
        tx_hash=tx_hash, explorer=link)
    return {"status": "ok", "data": r.json(), "price": str(price),
            "txHash": tx_hash, "explorer": link}


if __name__ == "__main__":
    print(f"[hivemind] shared wallet {SHARED.address}")
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
