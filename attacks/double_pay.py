"""Attack 2: double paying.

The shop is slow: it delivers ~12 s after the request starts. The agent only
waits 10 s, decides the purchase failed, and buys again. But the first payment
already went through, so it pays twice for one report.

    .venv/bin/python -m attacks.double_pay
"""

import asyncio
import time
import uuid

import httpx

import admin
import chain
import naive_agent
from attacks.common import SHOP, balances, headline, hivemind_buy, target_from_args, verdict

FIRST_PATIENCE = 10  # seconds the agent waits before giving up; doubles each retry
URL = f"{SHOP}/slow/report/coffee"


async def run_naive():
    naive_agent.reset_budget(1)  # plenty: this attack isn't about the budget
    before = await balances("naive")
    events = []
    patience = FIRST_PATIENCE
    start = time.monotonic()

    for attempt in (1, 2):
        print(f"\n[agent1] attempt {attempt}: buying {URL} (will wait {patience}s)")
        try:
            r = await asyncio.wait_for(naive_agent.naive_buy("agent1", URL, events), patience)
        except asyncio.TimeoutError:
            print(f"[agent1] no answer after {patience}s -> assumes it FAILED, retrying")
            patience *= 2
            continue
        print(f"[agent1] got the report after {time.monotonic() - start:.1f}s total: \"{r.get('report')}\"")
        break

    payments = [e["tx"] for e in events if e["step"] == "paid"]
    print("\nPayments the agent made for this ONE report:")
    for tx in payments:
        print(f"  {chain.tx_link(tx)}")

    after = await balances("naive")
    spent = before["naive"] - after["naive"]
    verdict([
        "Wanted:        1 report for 0.01 USDC",
        f"Paid:          {len(payments)} times, {spent} USDC",
        f"LOST:          {spent - naive_agent.Decimal('0.01')} USDC",
        "Why: the agent can't tell 'slow' from 'failed', and its retry starts a brand-new purchase.",
    ])


async def run_hivemind():
    """Same slow shop, same impatient agent, but it reuses its purchase ID on retry."""
    admin.set_budget(1)
    before = await balances("shared")
    purchase_id = str(uuid.uuid4())  # made ONCE for this purchase
    patience = FIRST_PATIENCE
    start = time.monotonic()

    async with httpx.AsyncClient() as client:
        for attempt in (1, 2):
            print(f"\n[agent1] attempt {attempt}: buying {URL} (purchase ID {purchase_id[:8]}…, "
                  f"will wait {patience}s)")
            try:
                r = await asyncio.wait_for(
                    hivemind_buy(client, "agent1", URL, purchase_id=purchase_id), patience)
            except asyncio.TimeoutError:
                print(f"[agent1] no answer after {patience}s -> assumes it FAILED, retrying "
                      f"with the SAME purchase ID")
                patience *= 2
                continue
            print(f"[agent1] got the report after {time.monotonic() - start:.1f}s total "
                  f"(replayed={r['replayed']}): \"{r['report']}\"")
            print(f"[agent1] payment: {r['explorer']}")
            break

    after = await balances("shared")
    spent = before["shared"] - after["shared"]
    verdict([
        "Wanted:        1 report for 0.01 USDC",
        f"Paid:          {spent} USDC  ({'ONE payment' if spent == naive_agent.Decimal('0.01') else 'CHECK'})",
        "BLOCKED: the retry carried the same purchase ID, so hivemind waited for the",
        "         first purchase and returned its result instead of paying again.",
    ])


if __name__ == "__main__":
    target = target_from_args(__doc__, hivemind_step=4, hivemind_ready=True)
    headline(f"Double paying vs {target}", "A slow shop + an impatient agent that retries")
    asyncio.run(run_naive() if target == "naive" else run_hivemind())
