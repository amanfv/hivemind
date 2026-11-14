"""Top up the naive wallet from the shared wallet, so the attacks have money to lose.

Tops up to the targets below (sends only what's missing), so it's safe to re-run,
e.g. after the theft attack empties the naive wallet.
"""

import asyncio
from decimal import Decimal

import chain

TARGET_USDC = Decimal("1")         # enough for every attack, small enough to lose
TARGET_BTC = Decimal("0.000002")   # fees for about 400 payments


async def main():
    shared = chain.load_wallet("shared")
    naive = chain.load_address("naive")

    usdc_now = await chain.usdc_balance(naive)
    btc_now = await chain.btc_balance(naive)
    print(f"naive wallet {naive}: {usdc_now} USDC, {btc_now} BTC")

    # BTC first: the naive wallet can't pay any fee without it.
    if btc_now < TARGET_BTC:
        tx = await chain.send_btc(shared, naive, TARGET_BTC - btc_now)
        await chain.wait_for_receipt(tx)
        print(f"  sent {TARGET_BTC - btc_now} BTC  {chain.tx_link(tx)}")

    if usdc_now < TARGET_USDC:
        tx = await chain.pay_usdc(shared, naive, TARGET_USDC - usdc_now)
        await chain.wait_for_receipt(tx)
        print(f"  sent {TARGET_USDC - usdc_now} USDC  {chain.tx_link(tx)}")

    print(f"naive wallet now: {await chain.usdc_balance(naive)} USDC, "
          f"{await chain.btc_balance(naive)} BTC")


if __name__ == "__main__":
    asyncio.run(main())
