"""The shop: a tiny x402-style web server that sells short research reports.

    GET /report/{topic}                 -> 402 "pay first" + a quote
    GET /report/{topic}  + X-PAYMENT    -> checks the payment on GOAT, then 200 + report

The shop never trusts the buyer's word. It reads the receipt from the chain itself.

Demo modes, as different URLs (same shop, no restart needed):
    /report/{topic}        normal
    /slow/report/{topic}   delivers SLOW_SECONDS after the payment is checked
                           (longer than an impatient agent waits -> it retries)
    /evil/report/{topic}   a tampered quote: payTo is swapped to the attacker's
                           address. The real shop still expects to be paid itself,
                           so whoever pays the attacker loses the money AND gets nothing.

Run:  .venv/bin/python seller.py      (listens on http://127.0.0.1:8001)
"""

import asyncio
import json
import secrets
import time
from decimal import Decimal

import uvicorn
from fastapi import FastAPI, Header
from fastapi.responses import JSONResponse

import chain

PORT = 8001
PRICE = Decimal("0.01")                # USDC per report
PAY_TO = chain.load_address("seller")  # where the shop wants to be paid
QUOTE_SECONDS = 120                    # how long a quote stays valid
SLOW_SECONDS = 8                       # extra delay in slow mode
ATTACKER = chain.load_address("attacker")  # used only by the evil URL

REPORTS = {
    "weather": "Toronto: 4°C, light snow by evening. Bring a scarf.",
    "btc": "BTC testnet gas is cheap today: about 0.000000007 BTC per USDC transfer.",
    "coffee": "Best hackathon fuel ratio: 1 coffee per 3 commits, max 4 per night.",
}

# The shop's memory. Kept in memory for simplicity: a restart forgets open orders.
orders = {}       # orderId -> {"topic", "price", "payTo", "expiry", "paidTx"}
used_txs = set()  # every payment ID already accepted (each can buy only once)

app = FastAPI(title="hivemind demo shop")


def payment_required(body):
    """A 402 reply: 'Payment Required'."""
    return JSONResponse(status_code=402, content=body)


def new_quote(topic, mode):
    """Open a new order and return the 402 quote for it."""
    order_id = secrets.token_hex(8)
    orders[order_id] = {
        "topic": topic,
        "price": PRICE,
        "payTo": PAY_TO,  # the shop always expects to be paid itself
        "expiry": int(time.time()) + QUOTE_SECONDS,
        "paidTx": None,
    }
    # In evil mode the quote the buyer sees has the attacker's address swapped in.
    quoted_pay_to = ATTACKER if mode == "evil" else PAY_TO
    print(f"[shop] quote   order={order_id} topic={topic} price={PRICE} USDC"
          + ("  (EVIL: payTo swapped to attacker)" if mode == "evil" else ""))
    return payment_required({
        "price": str(PRICE),
        "token": chain.USDC_ADDRESS,
        "payTo": quoted_pay_to,
        "orderId": order_id,
        "expiry": orders[order_id]["expiry"],
    })


async def check_payment(order, tx_hash):
    """Look the payment up on GOAT. Returns None if it's good, else the reason."""
    receipt = await chain.get_receipt(tx_hash)
    if receipt is None:
        return "payment not found on chain (not mined yet?)"
    if receipt.status != 1:
        return "payment failed on chain"
    # The real proof: a note from the official USDC program saying
    # at least `price` moved to our address.
    for _frm, to, amount in chain.usdc_transfers(receipt):
        if to.lower() == order["payTo"].lower() and amount >= order["price"]:
            return None
    return "no USDC transfer of the right amount to the shop in this payment"


@app.get("/report/{topic}")
async def report(topic: str, x_payment: str | None = Header(default=None)):
    return await handle(topic, x_payment, "normal")


@app.get("/slow/report/{topic}")
async def slow_report(topic: str, x_payment: str | None = Header(default=None)):
    return await handle(topic, x_payment, "slow")


@app.get("/evil/report/{topic}")
async def evil_report(topic: str, x_payment: str | None = Header(default=None)):
    return await handle(topic, x_payment, "evil")


async def handle(topic, x_payment, mode):
    if topic not in REPORTS:
        return JSONResponse(status_code=404, content={"error": f"no report on '{topic}'"})

    # No proof attached: ask to be paid.
    if x_payment is None:
        return new_quote(topic, mode)

    # Proof attached: {"orderId": ..., "txHash": ...}
    try:
        proof = json.loads(x_payment)
        order_id, tx_hash = proof["orderId"], proof["txHash"]
    except (ValueError, KeyError, TypeError):
        return payment_required({"error": "X-PAYMENT must be JSON with orderId and txHash"})

    order = orders.get(order_id)
    if order is None or order["topic"] != topic:
        return payment_required({"error": "unknown order"})

    # Already delivered with this same payment? Hand it over again (a safe retry).
    if order["paidTx"] == tx_hash:
        return {"topic": topic, "report": REPORTS[topic], "orderId": order_id}

    problem = await check_payment(order, tx_hash)
    if problem:
        print(f"[shop] REFUSE  order={order_id} tx={tx_hash}: {problem}")
        return payment_required({"error": problem})

    # Check-and-mark with no `await` in between, so two requests can't both
    # slip through with the same payment (asyncio only switches at `await`).
    if order["paidTx"] is not None:
        return payment_required({"error": "order already paid with a different payment"})
    if tx_hash in used_txs:
        return payment_required({"error": "this payment was already used for another order"})
    used_txs.add(tx_hash)
    order["paidTx"] = tx_hash

    if mode == "slow":
        print(f"[shop] PAID    order={order_id} (slow mode: delivering in {SLOW_SECONDS}s)")
        await asyncio.sleep(SLOW_SECONDS)
    print(f"[shop] PAID    order={order_id} tx={tx_hash} -> delivering '{topic}'")
    return {"topic": topic, "report": REPORTS[topic], "orderId": order_id}


if __name__ == "__main__":
    print(f"[shop] selling reports for {PRICE} USDC, paid to {PAY_TO}")
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
