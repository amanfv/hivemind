"""Attack 3: stolen access.

In the naive version, an agent's credentials ARE the wallet key. A thief who
copies one agent's config file can sign anything, so it sends the whole
balance to the attacker. No budget, no limit, nothing to switch off.

    .venv/bin/python -m attacks.steal
(Top the naive wallet back up afterwards with:  .venv/bin/python fund_naive.py)
"""

import asyncio

import httpx

import admin
import chain
from attacks.common import SHOP, balances, headline, hivemind_buy, short, target_from_args, verdict


async def run_naive():
    # What the thief copied from agent3's config: the shared naive key.
    stolen_key = chain.load_wallet("naive")
    attacker = chain.load_address("attacker")

    before = await balances("naive", "attacker")
    print(f"\nnaive wallet {short(stolen_key.address)} holds {before['naive']} USDC")
    print(f"Thief uses agent3's stolen key to send ALL of it to {short(attacker)} ...")

    tx = await chain.pay_usdc(stolen_key, attacker, before["naive"])
    receipt = await chain.wait_for_receipt(tx)
    print(f"  {'success' if receipt.status == 1 else 'FAILED'}: {chain.tx_link(tx)}")

    after = await balances("naive", "attacker")
    verdict([
        f"Team wallet:   {before['naive']} -> {after['naive']} USDC",
        f"Attacker:      {before['attacker']} -> {after['attacker']} USDC",
        f"LOST:          {before['naive'] - after['naive']} USDC (everything)",
        "Why: every agent holds the key, so stealing ONE agent = stealing the whole wallet.",
    ])


async def run_hivemind():
    """With hivemind, agent3's credentials are a PASS, not the key."""
    admin.set_budget(1)
    admin.set_enabled("agent3", True)
    stolen_pass = admin.load_token("agent3")  # what the thief copied from agent3's config
    before = await balances("shared", "attacker")

    async with httpx.AsyncClient() as client:
        print("\n1) Thief tries to get money sent to itself (a quote with the attacker's address):")
        r = await hivemind_buy(client, "thief", f"{SHOP}/evil/report/weather", token=stolen_pass)
        print(f"   {'PAID' if r['ok'] else 'REFUSED: ' + r['error']}")

        print("\n2) Thief tries to burn the budget: 6 purchases at once from the real shop:")
        results = await asyncio.gather(*(
            hivemind_buy(client, "thief", f"{SHOP}/report/btc", token=stolen_pass) for _ in range(6)
        ))
        for r in results:
            print(f"   {'paid  ' + r['explorer'] if r['ok'] else 'REFUSED: ' + r['error']}")

        print("\n3) Operator notices and switches agent3's pass off:")
        admin.set_enabled("agent3", False)
        r = await hivemind_buy(client, "thief", f"{SHOP}/report/btc", token=stolen_pass)
        print(f"   thief tries again -> {'PAID' if r['ok'] else 'REFUSED: ' + r['error']}")

        print("\n4) Meanwhile, the other agents keep working:")
        r = await hivemind_buy(client, "agent1", f"{SHOP}/report/weather")
        print(f"   agent1 -> {'got report  ' + r['explorer'] if r['ok'] else 'REFUSED: ' + r['error']}")

    admin.set_enabled("agent3", True)  # clean up for the next demo run
    after = await balances("shared", "attacker")
    verdict([
        f"Attacker received:  {after['attacker'] - before['attacker']} USDC  (hivemind only pays approved shops)",
        f"Thief's damage:     at most agent3's limit ({admin.AGENT_LIMIT} USDC), spent on real reports",
        "Thief stopped:      by switching off ONE pass; agent1 kept working",
        "Why: agents hold passes, not the key. Each pass has its own limit and its own off switch.",
    ])


if __name__ == "__main__":
    target = target_from_args(__doc__, hivemind_step=3, hivemind_ready=True)
    headline(f"Stolen access vs {target}", "A thief copies one agent's credentials")
    asyncio.run(run_naive() if target == "naive" else run_hivemind())
