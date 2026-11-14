"""Test: a rejected payment leaves a hole, and the payment queue fills it.

1. Tell hivemind to "reject" the next cheque number N (a test switch).
2. Three agents buy at the same instant: they get cheques N, N+1, N+2.
3. Cheque N is rejected. Without hole-filling, N+1 and N+2 would wait forever
   (cheques are cashed in order). The queue fills N with a 0 BTC payment to
   ourselves, so N+1 and N+2 go through, and N's hold is released.

    .venv/bin/python -m tests.gap_fill
"""

import asyncio

import httpx

import admin
import chain
from attacks.common import GATEWAY, SHOP, hivemind_buy, verdict
from db import conn


async def main():
    admin.set_budget(1)
    async with httpx.AsyncClient() as client:
        n = (await client.post(f"{GATEWAY}/test/reject-next-payment")).json()["willReject"]
        print(f"Test switch: the chain will 'reject' cheque #{n}\n")
        results = await asyncio.gather(*(
            hivemind_buy(client, f"agent{i}", f"{SHOP}/report/weather") for i in (11, 12, 13)
        ))

    for r in results:
        print(f"  {r['agent']:<8} {'got report  ' + r['explorer'] if r['ok'] else 'FAILED: ' + r['error']}")

    rows = conn.execute("SELECT nonce, kind, status, tx_hash FROM payments WHERE nonce BETWEEN ? AND ?"
                        " ORDER BY nonce, id", (n, n + 2)).fetchall()
    print("\nThe payment queue's photocopies for these cheque numbers:")
    for row in rows:
        print(f"  #{row['nonce']:<4} {row['kind']:<9} {row['status']:<9} {chain.tx_link(row['tx_hash'])}")

    filler = next((r for r in rows if r["kind"] == "gap_fill"), None)
    filler_ok = False
    if filler:
        receipt = await chain.wait_for_receipt(filler["tx_hash"])
        filler_ok = receipt.status == 1
    held = conn.execute("SELECT COUNT(*) FROM holds WHERE status = 'held'").fetchone()[0]
    got = sum(r["ok"] for r in results)
    verdict([
        f"Cheque #{n} rejected, hole filled on chain: {'YES' if filler_ok else 'NO'}",
        f"Purchases behind it that still went through: {got} of 2",
        f"Holds left stuck as 'held': {held}  (the rejected purchase's hold was released)",
        "PASS" if filler_ok and got == 2 and held == 0 else "FAIL",
    ])


if __name__ == "__main__":
    asyncio.run(main())
