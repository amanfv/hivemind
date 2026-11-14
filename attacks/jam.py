"""Attack 5: traffic jam (nonce collisions).

Every payment from a wallet needs the next cheque number (nonce): 0, 1, 2, ...
and each number can be used only once. 10 agents pay at the SAME instant. Each
asks the chain "what's my next number?", they all get the same answer, and
they all write that same cheque. Only one can be cashed.

    .venv/bin/python -m attacks.jam
"""

import asyncio

import chain
import naive_agent
from attacks.common import SHOP, balances, headline, target_from_args, verdict

AGENTS = 10


async def run_naive():
    naive_agent.reset_budget(1)
    naive_addr = chain.load_address("naive")
    nonce_before = await chain.w3.eth.get_transaction_count(naive_addr)
    before = await balances("naive")

    # Everyone at once: no stagger.
    results = await asyncio.gather(*(
        naive_agent.naive_buy(f"agent{i + 1}", f"{SHOP}/report/btc") for i in range(AGENTS)
    ))

    print("\nResults:")
    for r in results:
        status = "got report" if r["ok"] else f"FAILED: {r['error'][:90]}"
        print(f"  {r['agent']:<8} {status}")

    nonce_after = await chain.w3.eth.get_transaction_count(naive_addr)
    after = await balances("naive")
    landed = nonce_after - nonce_before
    got = sum(r["ok"] for r in results)
    claimed = {r["tx"] for r in results if r.get("tx")}
    verdict([
        f"Agents that tried to pay:       {AGENTS}",
        f"Payments that reached the chain: {landed}  (cheque numbers {nonce_before}..{nonce_after - 1})",
        f"Different payment IDs agents got: {len(claimed)}",
        f"Agents that got their report:   {got}",
        f"JAMMED:                         {AGENTS - got} of {AGENTS} purchases failed",
        f"(USDC spent: {before['naive'] - after['naive']})",
        "Why: they all asked the chain for the next cheque number at once and got the same one.",
    ])


if __name__ == "__main__":
    target_from_args(__doc__, hivemind_step=4)
    headline("Traffic jam", f"{AGENTS} agents pay at the same instant from one wallet")
    asyncio.run(run_naive())
