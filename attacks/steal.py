"""Attack 3: stolen access.

In the naive version, an agent's credentials ARE the wallet key. A thief who
copies one agent's config file can sign anything, so it sends the whole
balance to the attacker. No budget, no limit, nothing to switch off.

    .venv/bin/python -m attacks.steal
(Top the naive wallet back up afterwards with:  .venv/bin/python fund_naive.py)
"""

import asyncio

import chain
from attacks.common import balances, headline, short, target_from_args, verdict


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


if __name__ == "__main__":
    target_from_args(__doc__, hivemind_step=3)
    headline("Stolen access", "A thief copies one agent's credentials (in the naive version: the key)")
    asyncio.run(run_naive())
