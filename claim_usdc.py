"""Claim 20 free test USDC into the shared wallet from the official faucet.

Needs a little BTC in the shared wallet first (from the faucet) to pay the fee.
The faucet allows at most 2 claims per wallet (40 USDC in total).
"""

import asyncio

import chain


async def main():
    shared = chain.load_wallet("shared")
    print(f"Shared wallet: {shared.address}")
    print(f"  BTC (fees): {await chain.btc_balance(shared.address)}")
    print(f"  USDC:       {await chain.usdc_balance(shared.address)}")

    ok, reason = await chain.can_claim_usdc(shared.address)
    if not ok:
        print(f"\nFaucet says no: {reason}")
        return

    print("\nClaiming test USDC...")
    tx_hash = await chain.claim_usdc(shared)
    print(f"  sent: {chain.tx_link(tx_hash)}")

    receipt = await chain.wait_for_receipt(tx_hash)
    print(f"  status: {'success' if receipt.status == 1 else 'FAILED'} (block {receipt.blockNumber})")
    print(f"  USDC now: {await chain.usdc_balance(shared.address)}")


if __name__ == "__main__":
    asyncio.run(main())
