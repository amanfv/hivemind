# Hivemind notes

Our own explanations of each step, in plain language. These become the submission write-ups.

## Step 0: one USDC payment on GOAT

**What we built:** three wallets (shared, seller, attacker), a small `chain.py` that talks to the GOAT blockchain, and a test that pays the seller 0.01 USDC.

**Two kinds of money:**
- **BTC** is the postage. Every transaction costs a tiny fee in BTC (about 0.000000007 BTC per payment).
- **USDC** is the money in the envelope, what agents actually spend. The hackathon rules require stablecoins.

**Why the explorer shows "To: USDC" and not the seller:**
Each transaction only changes the state of the stablecoin program; it doesn't send money directly to the seller's address. USDC is a program on the chain that keeps its own list of balances. Paying = sending the program a message: "move 0.01 from my row to the seller's row." The program edits its list and writes a note in the receipt ("0.01 USDC moved from shared to seller"). The seller's address is only a row in that list, and it shows up in the note.

**The proof:** that note in the receipt is what a shop checks before handing over what was bought. Only notes written by the official USDC program count, so a fake token called "USDC" can't pass as payment.

**First payment:** https://explorer.testnet3.goat.network/tx/0xab0efe589c605f739bdea6962f046138fae024e80f71430d5b4400f8076c9a8a
(Nonce 1, because nonce 0 was the faucet claim. The nonce is the wallet's cheque number.)
