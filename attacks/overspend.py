"""Attack 1: overspending.

Team budget 0.05 USDC (5 reports). 10 agents each buy one, started a split
second apart. Each checks "spent so far" before paying, but nobody adds to
"spent" until their payment confirms (~3.4 s later), so they all see room.

These agents have the jam (attack 5) fixed: they share a local cheque-number
counter. That isolates the budget problem: even with the jam fixed, it overspends.

    .venv/bin/python -m attacks.overspend
"""

import asyncio
import time
from decimal import Decimal

import naive_agent
from attacks.common import SHOP, balances, headline, target_from_args, verdict

AGENTS = 10
BUDGET = Decimal("0.05")
STAGGER = 0.4  # seconds between agents starting


async def run_naive():
    naive_agent.reset_budget(BUDGET)
    await naive_agent.start_local_nonces()
    before = await balances("naive")
    events = []
    start = time.monotonic()

    async def agent(i):
        await asyncio.sleep(i * STAGGER)
        return await naive_agent.naive_buy(f"agent{i + 1}", f"{SHOP}/report/weather", events,
                                           local_nonces=True)

    results = await asyncio.gather(*(agent(i) for i in range(AGENTS)))

    print("\nWhat each agent saw when it checked the budget:")
    for e in sorted(events, key=lambda e: e["t"]):
        if e["step"] == "check":
            print(f"  {e['t'] - start:5.1f}s  {e['agent']:<8} spent so far {e['spent']} of {e['budget']}"
                  f"  -> room left, pays")

    print("\nResults:")
    for r in results:
        status = "got report" if r["ok"] else f"FAILED: {r['error']}"
        print(f"  {r['agent']:<8} {status}  {naive_agent.chain.tx_link(r['tx']) if r.get('tx') else ''}")

    after = await balances("naive")
    spent = before["naive"] - after["naive"]
    paid = sum(1 for r in results if r.get("tx"))
    over = spent - BUDGET
    verdict([
        f"Team budget:        {BUDGET} USDC",
        f"Actually spent:     {spent} USDC  ({paid} payments on chain)",
        (f"OVERSPENT BY:       {over} USDC  ({spent / BUDGET:.0%} of budget)" if over > 0
         else f"Within budget this run (over by {over}). Check the failures above."),
        "Why: every agent checked 'spent' before anyone's payment confirmed.",
    ])


if __name__ == "__main__":
    target_from_args(__doc__, hivemind_step=3)
    headline("Overspending", f"{AGENTS} agents, budget {BUDGET} USDC, each buys a 0.01 report")
    asyncio.run(run_naive())
