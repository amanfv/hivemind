"""Create the three wallets the project uses and save them to wallets.json.

A wallet is just a random secret key. The public address is worked out from it.
Run this once. It refuses to overwrite wallets.json so you can't lose funded keys.

    shared   - the team's card. Only hivemind will ever hold this key.
    seller   - the shop's account, where purchases are paid to.
    attacker - used later by the "wrong shop" attack.
"""

import json
import os
import sys

from eth_account import Account

WALLETS_FILE = "wallets.json"
ROLES = ["shared", "seller", "attacker"]


def main():
    if os.path.exists(WALLETS_FILE):
        sys.exit(f"{WALLETS_FILE} already exists. Not overwriting it (it may hold funded keys).")

    wallets = {}
    for role in ROLES:
        account = Account.create()  # a fresh random secret key
        wallets[role] = {"address": account.address, "key": account.key.hex()}

    with open(WALLETS_FILE, "w") as f:
        json.dump(wallets, f, indent=2)
    os.chmod(WALLETS_FILE, 0o600)  # only your user can read the file

    print(f"Saved {len(ROLES)} wallets to {WALLETS_FILE} (git ignores this file).\n")
    for role in ROLES:
        print(f"  {role:<9} {wallets[role]['address']}")
    print("\nNext: paste the SHARED address into the faucet to get BTC for fees.")


if __name__ == "__main__":
    main()
