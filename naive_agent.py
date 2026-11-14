"""The naive version: no hivemind. Every agent holds the wallet key and pays by itself.

It even tries to respect a team budget the obvious way:
    1. read "how much have we spent?" from a shared file
    2. if there's room, pay
    3. wait for the receipt (~3.4 s)
    4. add the price to "spent"

That looks reasonable. The attack scripts in attacks/ show why it isn't.
"""

import json
import time
from decimal import Decimal

import httpx

import chain

WALLET = chain.load_wallet("naive")  # every naive agent has this key
BUDGET_FILE = "naive_budget.json"    # the team budget, shared by all naive agents
SHOP = "http://127.0.0.1:8001"

http = httpx.AsyncClient(timeout=60)

# Optional jam fix for the overspend demo: hand out cheque numbers locally
# instead of asking the chain each time (which gives everyone the same number).
_next_nonce = None


async def start_local_nonces():
    """Read the wallet's next cheque number once; take_nonce() counts up from it."""
    global _next_nonce
    _next_nonce = await chain.w3.eth.get_transaction_count(WALLET.address, "pending")


def take_nonce():
    global _next_nonce
    n = _next_nonce
    _next_nonce += 1
    return n


# --- The shared budget file -------------------------------------------------------

def reset_budget(total):
    """Start a fresh team budget: `total` USDC, nothing spent."""
    with open(BUDGET_FILE, "w") as f:
        json.dump({"budget": str(total), "spent": "0"}, f)


def read_budget():
    """Return (budget, spent) as Decimals."""
    with open(BUDGET_FILE) as f:
        b = json.load(f)
    return Decimal(b["budget"]), Decimal(b["spent"])


def add_spent(amount):
    """Record a confirmed payment against the budget."""
    budget, spent = read_budget()
    with open(BUDGET_FILE, "w") as f:
        json.dump({"budget": str(budget), "spent": str(spent + amount)}, f)


# --- One purchase -----------------------------------------------------------------

async def naive_buy(name, url, events=None, local_nonces=False):
    """Buy `url` the naive way. Returns a dict describing what happened.

    `events` (optional list) collects timestamped steps so attacks can show timing.
    `local_nonces=True` uses take_nonce() (call start_local_nonces() first), so
    agents don't jam. The budget check is still naive.
    """
    def note(step, **info):
        if events is not None:
            events.append({"t": time.monotonic(), "agent": name, "step": step, **info})

    # Quote: the shop says what to pay and where.
    r = await http.get(url)
    if r.status_code != 402:
        return {"agent": name, "ok": False, "error": f"expected 402, got {r.status_code}"}
    quote = r.json()
    price = Decimal(quote["price"])

    # Budget check: read the shared file. (Nothing stops others reading it too.)
    budget, spent = read_budget()
    note("check", spent=spent, budget=budget)
    if spent + price > budget:
        return {"agent": name, "ok": False, "error": f"over budget ({spent}/{budget})"}

    # Pay: straight to whatever address the quote says.
    try:
        nonce = take_nonce() if local_nonces else None
        tx_hash = await chain.pay_usdc(WALLET, quote["payTo"], price, nonce)
    except Exception as e:  # e.g. the chain rejected it (nonce clash)
        note("rejected", error=str(e))
        return {"agent": name, "ok": False, "error": f"payment rejected: {e}"}
    note("paid", tx=tx_hash)

    receipt = await chain.wait_for_receipt(tx_hash)
    if receipt.status != 1:
        return {"agent": name, "ok": False, "tx": tx_hash, "error": "payment failed on chain"}
    add_spent(price)
    note("confirmed", tx=tx_hash)

    # Proof: show the shop the payment, collect the report.
    proof = json.dumps({"orderId": quote["orderId"], "txHash": tx_hash})
    r = await http.get(url, headers={"X-PAYMENT": proof})
    if r.status_code != 200:
        return {"agent": name, "ok": False, "tx": tx_hash, "paid": price, "payTo": quote["payTo"],
                "error": f"shop refused: {r.json().get('error')}"}
    return {"agent": name, "ok": True, "tx": tx_hash, "paid": price, "payTo": quote["payTo"],
            "report": r.json()["report"]}
