"""Create the three wallets the project uses and save them to wallets.json.

A wallet is just a random secret key. The public address is worked out from it.
Safe to re-run: it only adds wallets that are missing and never replaces an
existing one, so you can't lose funded keys.

    shared   - the team's card. Only hivemind will ever hold this key.
    seller   - the shop's account, where purchases are paid to.
    attacker - receives the money in the attack demos.
    naive    - the naive version's wallet (every naive agent holds this key).
               Separate from shared so the attacks never touch hivemind's money.
"""

import json
import os
import sys

from eth_account import Account

WALLETS_FILE = "wallets.json"
ROLES = ["shared", "seller", "attacker", "naive"]


def main():
    wallets = {}
    if os.path.exists(WALLETS_FILE):
        with open(WALLETS_FILE) as f:
            wallets = json.load(f)

    added = []
    for role in ROLES:
        if role in wallets:
            continue  # never replace an existing (maybe funded) wallet
        account = Account.create()  # a fresh random secret key
        wallets[role] = {"address": account.address, "key": account.key.hex()}
        added.append(role)

    if not added:
        sys.exit("All wallets already exist. Nothing to do.")

    with open(WALLETS_FILE, "w") as f:
        json.dump(wallets, f, indent=2)
    os.chmod(WALLETS_FILE, 0o600)  # only your user can read the file

    print(f"Added {', '.join(added)} to {WALLETS_FILE} (git ignores this file).\n")
    for role in ROLES:
        print(f"  {role:<9} {wallets[role]['address']}" + ("   (new)" if role in added else ""))


if __name__ == "__main__":
    main()
