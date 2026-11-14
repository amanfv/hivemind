"""Test: kill -9 hivemind mid-payment, restart it, and check nothing went wrong.

Runs its own hivemind (so it can kill it). Stop the usual hivemind tab first.
The shop (seller.py) must be running.

  Scenario A - random crash: 5 agents buy at once. The moment a payment has been
               sent but not yet confirmed, kill -9. Restart. Agents retry with
               the SAME purchase IDs.
  Scenario B - crash between photocopy and sending: hivemind kills itself right
               after saving the signed payment, before sending it. Restart.

Checks: every purchase paid exactly once, nothing stuck, and the ledger matches
the chain to the last unit (USDC and BTC fees).

    .venv/bin/python -m tests.crash
"""

import asyncio
import os
import signal
import socket
import subprocess
import sys
import uuid

import httpx

import admin
import chain
from attacks.common import GATEWAY, SHOP, hivemind_buy, verdict
from db import conn

LOG_FILE = "tests/crash_gateway.log"


def port_in_use(port):
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


async def start_gateway(extra_env=None):
    """Start hivemind in the background (recovery runs at startup) and wait until it answers."""
    log = open(LOG_FILE, "a")
    proc = subprocess.Popen([sys.executable, "gateway.py"], stdout=log, stderr=subprocess.STDOUT,
                            env={**os.environ, **(extra_env or {})})
    async with httpx.AsyncClient() as client:
        for _ in range(240):  # up to 2 minutes: recovery may wait for receipts
            if proc.poll() is not None:
                return proc
            try:
                await client.get(f"{GATEWAY}/openapi.json", timeout=1)
                return proc
            except httpx.HTTPError:
                await asyncio.sleep(0.5)
    raise RuntimeError("hivemind did not start")


def kill9(proc):
    os.kill(proc.pid, signal.SIGKILL)  # pull the power cord: no cleanup at all
    proc.wait()


def recovery_lines(since_id):
    return conn.execute("SELECT message FROM log WHERE id > ? AND stage = 'recovery' ORDER BY id",
                        (since_id,)).fetchall()


def last_log_id():
    return conn.execute("SELECT COALESCE(MAX(id), 0) FROM log").fetchone()[0]


def payments_for(agent, purchase_id):
    """Confirmed payments for one purchase (via its holds)."""
    return conn.execute(
        "SELECT p.* FROM payments p JOIN holds h ON p.hold_id = h.id"
        " WHERE h.agent = ? AND h.purchase_id = ? AND p.status = 'confirmed'",
        (agent, purchase_id)).fetchall()


async def safe_buy(client, agent, purchase_id):
    try:
        return await hivemind_buy(client, agent, f"{SHOP}/report/weather", purchase_id=purchase_id)
    except httpx.HTTPError as e:
        return {"agent": agent, "ok": False, "error": f"connection lost ({type(e).__name__})"}


async def scenario_a():
    print("\n### Scenario A: kill -9 while payments are in flight")
    agents = [f"agent{i}" for i in range(11, 16)]
    ids = {a: str(uuid.uuid4()) for a in agents}
    start_id = conn.execute("SELECT COALESCE(MAX(id), 0) FROM payments").fetchone()[0]

    proc = await start_gateway()
    async with httpx.AsyncClient() as client:
        tasks = [asyncio.create_task(safe_buy(client, a, ids[a])) for a in agents]
        for _ in range(600):  # wait for a payment that's sent but not confirmed yet
            if conn.execute("SELECT 1 FROM payments WHERE id > ? AND status = 'sent'",
                            (start_id,)).fetchone():
                break
            await asyncio.sleep(0.05)
        kill9(proc)
        rows = conn.execute("SELECT status, COUNT(*) n FROM payments WHERE id > ? GROUP BY status",
                            (start_id,)).fetchall()
        held = conn.execute("SELECT COUNT(*) FROM holds WHERE status = 'held'").fetchone()[0]
        print(f"KILLED. At that instant: holds held={held}, photocopies "
              + ", ".join(f"{r['status']}={r['n']}" for r in rows))
        first = await asyncio.gather(*tasks)
        print("Agents saw: " + ", ".join(f"{r['agent']}: {r['error'] or 'ok'}" for r in first))

        log_id = last_log_id()
        print("\nRestarting hivemind (recovery runs first)...")
        proc = await start_gateway()
        for (msg,) in recovery_lines(log_id):
            print(f"  [recovery] {msg}")

        print("\nAgents retry with the SAME purchase IDs:")
        second = await asyncio.gather(*(safe_buy(client, a, ids[a]) for a in agents))
    for r in second:
        print(f"  {r['agent']:<8} {'got report' if r['ok'] else 'FAILED: ' + str(r['error'])}"
              f"{' (replayed)' if r.get('replayed') else ''}  {r.get('explorer') or ''}")
    counts = {a: len(payments_for(a, ids[a])) for a in agents}
    return proc, all(r["ok"] for r in second), counts


async def scenario_b(proc):
    print("\n### Scenario B: crash right after the photocopy, before sending")
    proc.terminate()
    proc.wait()
    agent, pid = "agent16", str(uuid.uuid4())
    start_id = conn.execute("SELECT COALESCE(MAX(id), 0) FROM payments").fetchone()[0]

    proc = await start_gateway({"HIVEMIND_CRASH_AFTER_SAVE": "1"})
    async with httpx.AsyncClient() as client:
        r = await safe_buy(client, agent, pid)
        proc.wait()
        print(f"Agent saw: {r['error'] or 'ok'}  (hivemind killed itself)")
        copy = conn.execute("SELECT * FROM payments WHERE id > ? AND kind = 'purchase'",
                            (start_id,)).fetchone()
        on_chain = await chain.get_receipt(copy["tx_hash"]) if copy else None
        print(f"Photocopy: cheque #{copy['nonce']} status={copy['status']}, "
              f"on chain: {'yes' if on_chain else 'NO (never sent)'}")

        log_id = last_log_id()
        print("\nRestarting hivemind (recovery runs first)...")
        proc = await start_gateway()
        for (msg,) in recovery_lines(log_id):
            print(f"  [recovery] {msg}")
        r = await safe_buy(client, agent, pid)
    print(f"\nAgent retries with the SAME purchase ID: {'got report' if r['ok'] else 'FAILED: ' + str(r['error'])}"
          f"{' (replayed)' if r.get('replayed') else ''}  {r.get('explorer') or ''}")
    return proc, r["ok"], len(payments_for(agent, pid)), copy["tx_hash"] == r.get("tx")


async def main():
    if port_in_use(8000):
        sys.exit("Port 8000 is busy: stop the usual hivemind (gateway.py) first.")
    open(LOG_FILE, "w").close()

    # Clean start: let recovery settle any leftovers, then take the checkpoint.
    proc = await start_gateway()
    proc.terminate()
    proc.wait()
    admin.set_budget(1)
    await admin.checkpoint()

    proc, a_ok, a_counts = await scenario_a()
    proc, b_ok, b_count, b_same = await scenario_b(proc)
    proc.terminate()
    proc.wait()

    print()
    matched = await admin.reconcile()
    stuck = sum(admin._unsettled())
    all_once = all(n == 1 for n in a_counts.values()) and b_count == 1
    verdict([
        f"A: all 5 agents got their report after the crash:  {'YES' if a_ok else 'NO'}",
        f"A: payments per purchase: {list(a_counts.values())}  (each must be exactly 1)",
        f"B: never-sent photocopy was resent and the agent got its report: {'YES' if b_ok else 'NO'}",
        f"B: payments for that purchase: {b_count}, and it was the SAME saved cheque: "
        f"{'YES' if b_same else 'NO'}",
        f"Stuck holds or unsent payments: {stuck}",
        f"Ledger matches chain (USDC and BTC): {'YES' if matched else 'NO'}",
        "PASS" if a_ok and b_ok and all_once and b_same and stuck == 0 and matched else "FAIL",
    ])


if __name__ == "__main__":
    asyncio.run(main())
