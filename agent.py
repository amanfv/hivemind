"""A helper agent: asks hivemind to buy a report. Holds no wallet key.

    .venv/bin/python agent.py            # buys the weather report
    .venv/bin/python agent.py coffee     # buys the coffee report
"""

import asyncio
import sys
import uuid

import httpx

GATEWAY = "http://127.0.0.1:8000"
SHOP = "http://127.0.0.1:8001"
TOKEN = "tok-agent1-demo"  # this agent's badge (not the wallet key)


async def main(topic):
    purchase_id = str(uuid.uuid4())  # made once per purchase; reused on retries (Step 4)
    request = {
        "url": f"{SHOP}/report/{topic}",
        "reason": f"need the {topic} report for my task",
        "purchaseId": purchase_id,
    }
    print(f"[agent] asking hivemind to buy {request['url']}")

    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(f"{GATEWAY}/buy", json=request,
                              headers={"Authorization": f"Bearer {TOKEN}"})
    result = r.json()

    if result.get("status") != "ok":
        print(f"[agent] purchase failed ({r.status_code}): {result.get('error')}")
        if result.get("explorer"):
            print(f"[agent] payment: {result['explorer']}")
        sys.exit(1)

    print(f"[agent] got it for {result['price']} USDC:")
    print(f"        \"{result['data']['report']}\"")
    print(f"[agent] proof of payment: {result['explorer']}")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "weather"))
