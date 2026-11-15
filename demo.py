"""The attack lab: a split-screen demo page.

Click a scene and the SAME attack runs at the same time on both sides:
    left  = without hivemind (the naive version: every agent holds a wallet key)
    right = with hivemind (agents hold passes; hivemind holds the key)

The two sides use different wallets (naive vs shared), so they don't interfere.
This file only drives the demo. Hivemind itself (gateway.py) is unchanged.

Needs the shop (seller.py, port 8001) and hivemind (gateway.py, port 8000) running.

Run:  .venv/bin/python demo.py      then open http://127.0.0.1:8002
"""

import asyncio
import time
import uuid
from contextlib import asynccontextmanager
from decimal import Decimal

import httpx
import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

import admin
import chain
import fund_naive
import naive_agent
from attacks.common import GATEWAY, SHOP, hivemind_buy
from db import conn

PORT = 8002
ATTACKER = chain.load_address("attacker")



@asynccontextmanager
async def lifespan(app):
    task = asyncio.create_task(refresh())
    yield
    task.cancel()


app = FastAPI(title="hivemind attack lab", lifespan=lifespan)

# --- Live state the page polls ---------------------------------------------------------

balances = {}                 # role -> Decimal USDC (refreshed in the background)
health = {"gateway": False, "shop": False}
scene = None                  # the current (or last) Scene
resetting = False


class Side:
    """One half of the split screen."""

    def __init__(self, wallet_role, budget=None):
        self.wallet_role = wallet_role
        self.budget = budget
        self.rows = []            # each: {label, status, note, link}
        self.start_balance = None
        self.drop = Decimal(0)    # USDC that actually left this side's wallet
        self.to_attacker = Decimal(0)
        self.verdict = None
        self.good = None

    def row(self, label, status="waiting", note=""):
        r = {"label": label, "status": status, "note": note, "link": None}
        self.rows.append(r)
        return r

    def json(self):
        live = self.drop
        if self.verdict is None and self.start_balance is not None and self.wallet_role in balances:
            live = max(Decimal(0), self.start_balance - balances[self.wallet_role])
        return {"rows": self.rows, "budget": str(self.budget) if self.budget is not None else None,
                "spent": str(live), "toAttacker": str(self.to_attacker),
                "verdict": self.verdict, "good": self.good}


class Scene:
    def __init__(self, name, title, story, left_budget=None, right_budget=None):
        self.name, self.title, self.story = name, title, story
        self.left = Side("naive", left_budget)
        self.right = Side("shared", right_budget)
        self.started = time.monotonic()
        self.finished = None
        self.error = None
        self.can_switch_off = False

    def json(self):
        end = self.finished or time.monotonic()
        return {"name": self.name, "title": self.title, "story": self.story,
                "elapsed": round(end - self.started, 1), "done": self.finished is not None,
                "error": self.error, "canSwitchOff": self.can_switch_off,
                "left": self.left.json(), "right": self.right.json()}


# --- Helpers ----------------------------------------------------------------------------

def short_tx(tx):
    return f"{tx[:6]}…{tx[-4:]}" if tx else ""


def set_link(row, tx):
    row["link"] = {"href": chain.tx_link(tx), "text": short_tx(tx)}


class NaiveEvents(list):
    """naive_buy() reports each step here; we turn steps into live row updates."""

    def __init__(self, rows):
        super().__init__()
        self.rows = rows  # agent name -> row

    def append(self, e):
        super().append(e)
        r = self.rows.get(e["agent"])
        if r is None:
            return
        if e["step"] == "check":
            r.update(status="checking", note=f"read the budget file: {e['spent']} of {e['budget']} spent, room left")
        elif e["step"] == "paid":
            r.update(status="paying", note="payment sent, waiting for the chain")
            set_link(r, e["tx"])
        elif e["step"] == "confirmed":
            r.update(status="paying", note="payment confirmed, collecting the report")
        elif e["step"] == "rejected":
            r.update(status="failed", note=plain_error(e["error"]))


def plain_error(err):
    err = str(err or "")
    if "already known" in err:
        return "rejected: another agent already used this payment number"
    if "nonce too low" in err:
        return "rejected: payment number already used"
    if "shop refused" in err:
        return "the shop refused: it was never paid"
    return err[:140]


def finish_naive(row, result):
    if result.get("tx"):
        set_link(row, result["tx"])
    if result["ok"]:
        row.update(status="paid", note="got the report")
    elif row["status"] != "failed":
        row.update(status="failed", note=plain_error(result.get("error")))


def finish_hivemind(row, result, success_note="got the report"):
    if result.get("tx"):
        set_link(row, result["tx"])
    if result["ok"]:
        row.update(status="paid", note=success_note)
    elif result.get("code") in (401, 403):
        row.update(status="refused", note=result["error"])
    else:
        row.update(status="failed", note=result.get("error") or "failed")


def hivemind_budget(amount):
    """New budget period on hivemind, and agent3 back on."""
    try:
        admin.set_budget(amount)
    except SystemExit as e:
        raise RuntimeError(str(e)) from None
    conn.execute("UPDATE agents SET enabled = 1 WHERE name = 'agent3'")


def agent3_on():
    return bool(conn.execute("SELECT enabled FROM agents WHERE name = 'agent3'").fetchone()[0])


# --- Scenes -------------------------------------------------------------------------------
# Each scene has a left (naive) and a right (hivemind) coroutine, run at the same time.

async def overspend_left(s):
    naive_agent.reset_budget(s.left.budget)
    await naive_agent.start_local_nonces()  # jam fixed, to isolate the budget race
    rows = {f"agent{i}": s.left.row(f"agent{i}") for i in range(1, 11)}
    events = NaiveEvents(rows)

    async def one(i):
        await asyncio.sleep((i - 1) * 0.4)
        name = f"agent{i}"
        finish_naive(rows[name], await naive_agent.naive_buy(
            name, f"{SHOP}/report/weather", events, local_nonces=True))

    await asyncio.gather(*(one(i) for i in range(1, 11)))


async def overspend_right(s, client):
    hivemind_budget(s.right.budget)
    rows = {f"agent{i}": s.right.row(f"agent{i}", "asking", "asked hivemind") for i in range(1, 11)}

    async def one(name):
        finish_hivemind(rows[name], await hivemind_buy(client, name, f"{SHOP}/report/weather"))

    await asyncio.gather(*(one(n) for n in rows))


async def steal_left(s):
    r = s.left.row("thief, with agent3's key", "paying", "copied agent3's config: it IS the wallet key")
    key = chain.load_wallet("naive")
    amount = await chain.usdc_balance(key.address)
    if amount <= 0:
        r.update(status="failed", note="naive wallet is empty: press Reset first")
        return
    r["note"] = f"sending everything ({amount} USDC) to the attacker"
    tx = await chain.pay_usdc(key, ATTACKER, amount)
    set_link(r, tx)
    receipt = await chain.wait_for_receipt(tx)
    if receipt.status == 1:
        r.update(status="stolen", note=f"{amount} USDC sent to the attacker. Nothing could stop it.")
        s.left.to_attacker = amount


async def steal_right(s, client):
    hivemind_budget(1)
    token = admin.load_token("agent3")

    r = s.right.row("thief: pay me instead", "asking", "quote with the attacker's address")
    finish_hivemind(r, await hivemind_buy(client, "thief", f"{SHOP}/evil/report/weather", token=token))

    rows = [s.right.row(f"thief: buy #{i}", "asking", "burning the budget") for i in range(1, 7)]

    async def one(row):
        finish_hivemind(row, await hivemind_buy(client, "thief", f"{SHOP}/report/btc", token=token),
                        "paid (to the real shop, inside agent3's limit)")

    await asyncio.gather(*(one(row) for row in rows))

    wait = s.right.row("operator", "waiting", "click “Switch off agent3” to stop the thief")
    s.can_switch_off = True
    deadline = time.monotonic() + 25
    while agent3_on() and time.monotonic() < deadline:
        await asyncio.sleep(0.3)
    s.can_switch_off = False
    if agent3_on():  # nobody clicked: switch it off automatically
        await client.post(f"{GATEWAY}/dashboard/agents/agent3/off")
        wait.update(status="switched", note="switched agent3 off (automatically after 25 s)")
    else:
        wait.update(status="switched", note="switched agent3's pass off")

    r = s.right.row("thief: try again", "asking", "")
    finish_hivemind(r, await hivemind_buy(client, "thief", f"{SHOP}/report/btc", token=token))
    r = s.right.row("agent1 (unaffected)", "asking", "")
    finish_hivemind(r, await hivemind_buy(client, "agent1", f"{SHOP}/report/weather"),
                    "still works: only agent3's pass was switched off")


async def redirect_left(s):
    naive_agent.reset_budget(1)
    r = s.left.row("agent1", "asking", "buying via a tampered link")
    events = NaiveEvents({"agent1": r})
    result = await naive_agent.naive_buy("agent1", f"{SHOP}/evil/report/weather", events)
    finish_naive(r, result)
    if result.get("payTo", "").lower() == ATTACKER.lower() and result.get("paid"):
        s.left.to_attacker = result["paid"]
        r["note"] = f"paid the attacker {result['paid']} USDC, and the shop refused: no report"


async def redirect_right(s, client):
    hivemind_budget(1)
    r = s.right.row("agent1", "asking", "buying via a tampered link")
    finish_hivemind(r, await hivemind_buy(client, "agent1", f"{SHOP}/evil/report/weather"))


async def double_pay_left(s):
    naive_agent.reset_budget(1)
    url = f"{SHOP}/slow/report/coffee"
    rows = {"attempt 1": s.left.row("attempt 1", "asking", "waits up to 10 s"),
            "attempt 2": s.left.row("attempt 2", "waiting", "")}
    events = NaiveEvents(rows)
    try:
        finish_naive(rows["attempt 1"], await asyncio.wait_for(
            naive_agent.naive_buy("attempt 1", url, events), 10))
        return
    except asyncio.TimeoutError:
        paid = rows["attempt 1"]["link"] is not None
        rows["attempt 1"].update(status="gaveup", note="no answer after 10 s: assumed it failed"
                                 + (", but it had already paid" if paid else ""))
    rows["attempt 2"].update(status="asking", note="a brand-new purchase (waits up to 20 s)")
    try:
        finish_naive(rows["attempt 2"], await asyncio.wait_for(
            naive_agent.naive_buy("attempt 2", url, events), 20))
    except asyncio.TimeoutError:
        rows["attempt 2"].update(status="gaveup", note="gave up again")


async def double_pay_right(s, client):
    hivemind_budget(1)
    url = f"{SHOP}/slow/report/coffee"
    pid = str(uuid.uuid4())
    r1 = s.right.row("attempt 1", "asking", f"purchase ID {pid[:8]}…, waits up to 10 s")
    r2 = s.right.row("attempt 2", "waiting", "")
    try:
        finish_hivemind(r1, await asyncio.wait_for(hivemind_buy(client, "agent1", url, purchase_id=pid), 10))
        return
    except asyncio.TimeoutError:
        r1.update(status="gaveup", note="no answer after 10 s: assumed it failed")
    r2.update(status="asking", note=f"retry with the SAME purchase ID {pid[:8]}…")
    result = await asyncio.wait_for(hivemind_buy(client, "agent1", url, purchase_id=pid), 25)
    finish_hivemind(r2, result, "got the report from the first purchase: not paid again"
                    if result.get("replayed") else "got the report")


async def jam_left(s):
    naive_agent.reset_budget(1)
    rows = {f"agent{i}": s.left.row(f"agent{i}") for i in range(1, 11)}
    events = NaiveEvents(rows)

    async def one(name):
        finish_naive(rows[name], await naive_agent.naive_buy(name, f"{SHOP}/report/btc", events))

    await asyncio.gather(*(one(n) for n in rows))


async def jam_right(s, client):
    hivemind_budget(1)
    rows = {f"agent{i}": s.right.row(f"agent{i}", "asking", "asked hivemind") for i in range(1, 11)}

    async def one(name):
        finish_hivemind(rows[name], await hivemind_buy(client, name, f"{SHOP}/report/btc"))

    await asyncio.gather(*(one(n) for n in rows))


def count(side, status):
    return sum(1 for r in side.rows if r["status"] == status)


def verdicts(s):
    L, R = s.left, s.right
    if s.name == "overspend":
        L.good = L.drop <= L.budget
        L.verdict = (f"Overspent: {L.drop} USDC on a {L.budget} budget ({L.drop / L.budget:.0%})"
                     if not L.good else f"Within budget this time ({L.drop} of {L.budget})")
        R.good = R.drop <= R.budget
        R.verdict = (f"On budget: {count(R, 'paid')} paid, {count(R, 'refused')} refused instantly "
                     f"({R.drop / R.budget:.0%})")
    elif s.name == "steal":
        L.good = L.to_attacker == 0
        L.verdict = f"Wallet drained: {L.to_attacker} USDC to the attacker"
        R.good = True
        R.verdict = f"Attacker got 0. Thief capped at agent3's {admin.AGENT_LIMIT} USDC limit, then switched off"
    elif s.name == "redirect":
        L.good = L.to_attacker == 0
        L.verdict = f"Paid the attacker {L.to_attacker} USDC and got nothing"
        R.good = R.drop == 0
        R.verdict = "Refused before any money moved" if R.good else f"{R.drop} USDC left the wallet"
    elif s.name == "double_pay":
        L.good = L.drop <= Decimal("0.01")
        L.verdict = f"Paid {L.drop} USDC for one report" + (": paid twice" if not L.good else "")
        R.good = R.drop <= Decimal("0.01")
        R.verdict = f"Paid {R.drop} USDC for one report: the retry did not pay again"
    elif s.name == "jam":
        L.good = count(L, "failed") == 0
        L.verdict = f"Jammed: {count(L, 'failed')} of 10 purchases failed"
        R.good = count(R, "paid") == 10
        R.verdict = f"{count(R, 'paid')} of 10 paid, one payment number each"


SCENES = {
    "overspend": ("Overspend", "10 agents share a 0.05 USDC budget (5 reports) and all buy at once.",
                  overspend_left, overspend_right, Decimal("0.05")),
    "steal": ("Stolen pass", "A thief copies agent3's credentials and tries to take the money.",
              steal_left, steal_right, None),
    "redirect": ("Wrong address", "The shop's quote is tampered with: pay the attacker instead.",
                 redirect_left, redirect_right, None),
    "double_pay": ("Double pay", "A slow shop. The agent gives up after 10 s and tries again.",
                   double_pay_left, double_pay_right, None),
    "jam": ("Traffic jam", "10 agents pay from one wallet at the same instant.",
            jam_left, jam_right, None),
}


async def run_scene(s, left_fn, right_fn):
    # Starting balances from the background cache (asking the chain here held up
    # every scene by seconds). The final numbers are read fresh from the chain.
    s.left.start_balance = balances.get("naive") or await chain.usdc_balance(chain.load_address("naive"))
    s.right.start_balance = balances.get("shared") or await chain.usdc_balance(chain.load_address("shared"))
    try:
        async with httpx.AsyncClient() as client:
            results = await asyncio.gather(left_fn(s), right_fn(s, client), return_exceptions=True)
        errors = [r for r in results if isinstance(r, BaseException)]
        if errors:
            s.error = f"{type(errors[0]).__name__}: {errors[0]}"
        balances["naive"], balances["shared"] = await asyncio.gather(
            chain.usdc_balance(chain.load_address("naive")), chain.usdc_balance(chain.load_address("shared")))
        s.left.drop = s.left.start_balance - balances["naive"]
        s.right.drop = s.right.start_balance - balances["shared"]
        verdicts(s)
    except Exception as e:
        s.error = f"{type(e).__name__}: {e}"
    finally:
        s.can_switch_off = False
        s.finished = time.monotonic()


# --- Web routes --------------------------------------------------------------------------

@app.get("/")
async def page():
    with open("demo.html") as f:
        return HTMLResponse(f.read())


@app.get("/state")
async def state():
    return {"scene": scene.json() if scene else None,
            "addresses": {role: chain.load_address(role) for role in ("shared", "naive", "attacker", "seller")},
            "running": scene is not None and scene.finished is None,
            "resetting": resetting, "health": health,
            "balances": {k: str(v) for k, v in balances.items()}}


@app.post("/scene/{name}")
async def start(name: str):
    global scene
    if name not in SCENES:
        return JSONResponse(status_code=404, content={"error": f"no scene {name}"})
    if (scene and scene.finished is None) or resetting:
        return JSONResponse(status_code=409, content={"error": "wait for the current scene to finish"})
    if not (health["gateway"] and health["shop"]):
        return JSONResponse(status_code=503, content={"error": "start seller.py and gateway.py first"})
    if balances.get("naive", Decimal(0)) < Decimal("0.15") and name != "steal":
        return JSONResponse(status_code=409, content={"error": "the naive wallet is low: press Reset"})
    title, story, left_fn, right_fn, budget = SCENES[name]
    scene = Scene(name, title, story, left_budget=budget, right_budget=budget or Decimal(1))
    asyncio.create_task(run_scene(scene, left_fn, right_fn))
    return {"started": name}


@app.post("/switch-off-agent3")
async def switch_off():
    async with httpx.AsyncClient() as client:
        r = await client.post(f"{GATEWAY}/dashboard/agents/agent3/off")
    return JSONResponse(status_code=r.status_code, content=r.json())


@app.post("/reset")
async def reset():
    global resetting
    if (scene and scene.finished is None) or resetting:
        return JSONResponse(status_code=409, content={"error": "busy"})
    resetting = True
    try:
        hivemind_budget(1)
        naive_agent.reset_budget(1)
        await fund_naive.main()  # top the naive wallet back up from shared
        balances["naive"], balances["shared"] = await asyncio.gather(
            chain.usdc_balance(chain.load_address("naive")), chain.usdc_balance(chain.load_address("shared")))
        return {"reset": True}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})
    finally:
        resetting = False


async def refresh():
    """Balances and health, every 2 s (the page polls our cache, not the chain)."""
    async with httpx.AsyncClient(timeout=2) as client:
        while True:
            for role in ("naive", "shared", "attacker"):
                try:
                    balances[role] = await chain.usdc_balance(chain.load_address(role))
                except Exception:
                    pass
            for key, url in (("gateway", f"{GATEWAY}/openapi.json"), ("shop", f"{SHOP}/openapi.json")):
                try:
                    health[key] = (await client.get(url)).status_code == 200
                except httpx.HTTPError:
                    health[key] = False
            await asyncio.sleep(2)


if __name__ == "__main__":
    print(f"[demo] attack lab: http://127.0.0.1:{PORT}")
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
