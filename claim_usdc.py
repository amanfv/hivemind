"""Claim 10 free test USDC into the shared wallet.

Needs a little BTC in the shared wallet first (from the faucet) to pay the fee.
The token allows one claim per wallet every 24 hours.
"""

import asyncio

import chain


async def main():
    shared = chain.load_wallet("shared")
    print(f"Shared wallet: {shared.address}")
    print(f"  BTC (fees): {await chain.btc_balance(shared.address)}")
    print(f"  USDC:       {await chain.usdc_balance(shared.address)}")

    print("\nClaiming test USDC...")
    tx_hash = await chain.claim_usdc(shared)
    print(f"  sent: {chain.tx_link(tx_hash)}")

    receipt = await chain.wait_for_receipt(tx_hash)
    print(f"  status: {'success' if receipt.status == 1 else 'FAILED'} (block {receipt.blockNumber})")
    print(f"  USDC now: {await chain.usdc_balance(shared.address)}")


if __name__ == "__main__":
    asyncio.run(main())
