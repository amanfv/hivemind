"""Small helpers shared by the attack scripts."""

import argparse
import sys

import chain

SHOP = "http://127.0.0.1:8001"


def target_from_args(description, hivemind_step):
    """Read --target naive|hivemind. Hivemind versions arrive in later steps."""
    p = argparse.ArgumentParser(description=description)
    p.add_argument("--target", choices=["naive", "hivemind"], default="naive")
    target = p.parse_args().target
    if target == "hivemind":
        sys.exit(f"The hivemind version of this attack arrives in Step {hivemind_step}.")
    return target


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
