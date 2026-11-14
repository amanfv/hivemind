"""Step 0 check: pay the seller a tiny amount of USDC and show the proof.

    python send_test.py          # pays 0.01 USDC
    python send_test.py 0.05     # pays 0.05 USDC
"""

import asyncio
import sys

import chain


async def main(amount):
    shared = chain.load_wallet("shared")
    seller = chain.load_address("seller")

    print("Before:")
    print(f"  shared USDC: {await chain.usdc_balance(shared.address)}")
    print(f"  seller USDC: {await chain.usdc_balance(seller)}")

    print(f"\nPaying {amount} USDC  shared -> seller ...")
    tx_hash = await chain.pay_usdc(shared, seller, amount)
    print(f"  tx: {chain.tx_link(tx_hash)}")

    receipt = await chain.wait_for_receipt(tx_hash)
    fee_btc = chain.Decimal(receipt.gasUsed * receipt.effectiveGasPrice) / 10**18
    print(f"  status: {'success' if receipt.status == 1 else 'FAILED'} (block {receipt.blockNumber})")
    print(f"  fee paid: {fee_btc} BTC")

    print("\nThe USDC program's note in the receipt (what a shop would check):")
    for frm, to, amt in chain.usdc_transfers(receipt):
        print(f"  {amt} USDC  {frm} -> {to}")

    print("\nAfter:")
    print(f"  shared USDC: {await chain.usdc_balance(shared.address)}")
    print(f"  seller USDC: {await chain.usdc_balance(seller)}")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "0.01"))
