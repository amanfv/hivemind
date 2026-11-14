"""Attack 4: wrong shop (redirection).

The agent asks for a report, but the quote it gets back has been tampered with:
payTo is the attacker's address instead of the shop's. The naive agent pays
whatever address the quote says. The real shop then refuses the proof, because
it was never paid. Money gone, and no report.

    .venv/bin/python -m attacks.redirect
"""

import asyncio

import httpx

import admin
import chain
import naive_agent
from attacks.common import SHOP, balances, headline, hivemind_buy, short, target_from_args, verdict

URL = f"{SHOP}/evil/report/weather"


async def run_naive():
    naive_agent.reset_budget(1)
    seller = chain.load_address("seller")
    attacker = chain.load_address("attacker")
    before = await balances("naive", "attacker")

    print(f"\nThe real shop's address:  {short(seller)}")
    print(f"[agent1] buying {URL}")
    r = await naive_agent.naive_buy("agent1", URL)
    print(f"[agent1] quote said pay:  {short(r.get('payTo', '?'))}   <- the attacker")
    print(f"[agent1] paid:            {chain.tx_link(r['tx']) if r.get('tx') else 'nothing'}")
    print(f"[agent1] result:          {'got report' if r['ok'] else r['error']}")

    after = await balances("naive", "attacker")
    verdict([
        f"Attacker received:  {after['attacker'] - before['attacker']} USDC",
        f"Report received:    {'yes' if r['ok'] else 'NO'}",
        f"LOST:               {before['naive'] - after['naive']} USDC, and nothing to show for it",
        "Why: the agent pays whatever address the quote says. Nothing checks it against the real shop.",
    ])


async def run_hivemind():
    admin.set_budget(1)
    before = await balances("shared", "attacker")

    print(f"\nThe real shop's approved address:  {short(chain.load_address('seller'))}")
    print(f"[agent1] asking hivemind to buy {URL}")
    async with httpx.AsyncClient() as client:
        r = await hivemind_buy(client, "agent1", URL)
    print(f"[agent1] result: {'got report' if r['ok'] else 'REFUSED: ' + r['error']}")

    after = await balances("shared", "attacker")
    verdict([
        f"Attacker received:  {after['attacker'] - before['attacker']} USDC",
        f"Team wallet lost:   {before['shared'] - after['shared']} USDC",
        "BLOCKED before any money moved." if not r["ok"] else "NOT BLOCKED",
        "Why: hivemind compares the quote's payTo with the shop's approved address (gate 2).",
    ])


if __name__ == "__main__":
    target = target_from_args(__doc__, hivemind_step=3, hivemind_ready=True)
    headline(f"Wrong shop vs {target}", "The quote's payTo is swapped to the attacker's address")
    asyncio.run(run_naive() if target == "naive" else run_hivemind())
