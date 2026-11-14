"""Small helpers shared by the attack scripts."""

import argparse
import sys
import uuid

import httpx

import chain
from admin import load_token

SHOP = "http://127.0.0.1:8001"
GATEWAY = "http://127.0.0.1:8000"


def target_from_args(description, hivemind_step, hivemind_ready=False):
    """Read --target naive|hivemind. Some hivemind versions arrive in later steps."""
    p = argparse.ArgumentParser(description=description)
    p.add_argument("--target", choices=["naive", "hivemind"], default="naive")
    target = p.parse_args().target
    if target == "hivemind" and not hivemind_ready:
        sys.exit(f"The hivemind version of this attack arrives in Step {hivemind_step}.")
    return target


async def hivemind_buy(client, agent, url, reason="needed for my task", token=None):
    """Ask hivemind to buy `url` as `agent` (or with an explicit `token`).

    Returns {"agent", "ok", "error", "tx", "explorer", ...} like naive_buy does.
    """
    r = await client.post(
        f"{GATEWAY}/buy",
        json={"url": url, "reason": reason, "purchaseId": str(uuid.uuid4())},
        headers={"Authorization": f"Bearer {token or load_token(agent)}"},
        timeout=60,
    )
    body = r.json()
    return {"agent": agent, "ok": body.get("status") == "ok", "error": body.get("error"),
            "tx": body.get("txHash"), "explorer": body.get("explorer")}


def headline(title, story):
    print("=" * 72)
    print(f"ATTACK: {title}")
    print(story)
    print("=" * 72)


def verdict(lines):
    print("-" * 72)
    for line in lines:
        print(line)
    print("-" * 72)


def short(addr):
    return f"{addr[:6]}…{addr[-4:]}"


async def balances(*roles):
    """USDC balance for each wallet role, e.g. balances('naive', 'attacker')."""
    return {r: await chain.usdc_balance(chain.load_address(r)) for r in roles}
