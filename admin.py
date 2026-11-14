"""The operator's controls for hivemind.

    .venv/bin/python admin.py setup            # 20 agents + the shop; writes tokens.json
    .venv/bin/python admin.py budget 0.05      # new budget period: set budget, clear holds
    .venv/bin/python admin.py disable agent3   # the off switch for one pass
    .venv/bin/python admin.py enable agent3
    .venv/bin/python admin.py status           # budget, spent, held, per agent

Works while the gateway is running (they share hivemind.db).
"""

import json
import os
import secrets
import sys

import chain
import policy
from db import conn

TOKENS_FILE = "tokens.json"  # each agent's pass (gitignored). The database only stores hashes.
AGENTS = [f"agent{i}" for i in range(1, 21)]
AGENT_LIMIT = "0.03"         # USDC each agent may spend in a budget period
TEAM_BUDGET = "1"            # USDC, default budget
SHOP_ORIGIN = "http://127.0.0.1:8001"
SHOP_PRICE_CAP = "0.05"      # USDC per purchase


def setup():
    """Create the team budget, 20 agent passes and the approved shop. Replaces old passes."""
    tokens = {name: f"tok-{name}-{secrets.token_hex(12)}" for name in AGENTS}
    conn.execute("BEGIN IMMEDIATE")
    conn.execute("DELETE FROM agents")
    for name, token in tokens.items():
        conn.execute("INSERT INTO agents (name, token_hash, limit_units, enabled) VALUES (?, ?, ?, 1)",
                     (name, policy.token_hash(token), chain.usdc_to_units(AGENT_LIMIT)))
    conn.execute("INSERT OR REPLACE INTO sellers (origin, pay_to, max_price_units) VALUES (?, ?, ?)",
                 (SHOP_ORIGIN, chain.load_address("seller"), chain.usdc_to_units(SHOP_PRICE_CAP)))
    conn.execute("COMMIT")
    set_budget(TEAM_BUDGET)

    with open(TOKENS_FILE, "w") as f:
        json.dump(tokens, f, indent=2)
    os.chmod(TOKENS_FILE, 0o600)
    print(f"Created {len(AGENTS)} passes (limit {AGENT_LIMIT} USDC each) -> {TOKENS_FILE}")
    print(f"Approved shop {SHOP_ORIGIN}: pay to {chain.load_address('seller')}, cap {SHOP_PRICE_CAP} USDC")


def set_budget(amount):
    """Start a new budget period: set the team budget and clear old holds.

    Refuses while any purchase is still in flight (a 'held' row), so we never
    forget money that's about to leave the wallet.
    """
    conn.execute("BEGIN IMMEDIATE")
    in_flight = conn.execute("SELECT COUNT(*) FROM holds WHERE status = 'held'").fetchone()[0]
    if in_flight:
        conn.execute("ROLLBACK")
        sys.exit(f"{in_flight} purchase(s) still in flight; try again in a few seconds.")
    conn.execute("DELETE FROM holds")
    conn.execute("INSERT OR REPLACE INTO team (id, budget_units) VALUES (1, ?)",
                 (chain.usdc_to_units(amount),))
    conn.execute("COMMIT")
    print(f"Team budget set to {amount} USDC (holds cleared)")


def set_enabled(name, on):
    cur = conn.execute("UPDATE agents SET enabled = ? WHERE name = ?", (1 if on else 0, name))
    if cur.rowcount == 0:
        sys.exit(f"No agent called {name}")
    print(f"{name}: pass switched {'ON' if on else 'OFF'}")


def status():
    budget = conn.execute("SELECT budget_units FROM team WHERE id = 1").fetchone()[0]

    def total(where, *args):
        return conn.execute(f"SELECT COALESCE(SUM(amount_units), 0) FROM holds WHERE {where}",
                            args).fetchone()[0]

    spent, held = total("status = 'spent'"), total("status = 'held'")
    u = chain.units_to_usdc
    print(f"Team budget {u(budget)}  spent {u(spent)}  held {u(held)}  left {u(budget - spent - held)}")
    print(f"{'agent':<9}{'on':<5}{'limit':>7}{'spent':>8}{'held':>8}")
    for a in conn.execute("SELECT * FROM agents ORDER BY length(name), name"):
        print(f"{a['name']:<9}{'yes' if a['enabled'] else 'OFF':<5}{u(a['limit_units']):>7}"
              f"{u(total('status = ? AND agent = ?', 'spent', a['name'])):>8}"
              f"{u(total('status = ? AND agent = ?', 'held', a['name'])):>8}")


def load_token(name):
    """An agent's pass, from tokens.json (used by agent.py and the attack scripts)."""
    with open(TOKENS_FILE) as f:
        return json.load(f)[name]


if __name__ == "__main__":
    cmd, args = (sys.argv[1] if len(sys.argv) > 1 else "status"), sys.argv[2:]
    if cmd == "setup":
        setup()
    elif cmd == "budget" and args:
        set_budget(args[0])
    elif cmd in ("disable", "enable") and args:
        set_enabled(args[0], cmd == "enable")
    elif cmd == "status":
        status()
    else:
        sys.exit(__doc__)
