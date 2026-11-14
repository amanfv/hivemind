# hivemind

Agentic Payments Hackathon Toronto. Submissions due on BuilderBase by **10:00 AM ET, Sunday Nov 15**. Built on GOAT Network (Testnet3). Tracks: **Control** and **Attack**.

## How to work with me

- I want to understand everything we build. Before writing code for a step, explain the design in plain language: the options, the tradeoffs, and which one you recommend. Wait for my ok before building.
- Build one step at a time (see "Build order"). Don't start the next step until the current step's "done when" passes.
- Keep it simple. No extra services or libraries unless we agree on them.
- We're new to blockchain. Use plain language and the shared-card analogy, and define any new term the first time it comes up.
- Every step follows the same loop: **explain** (then wait for ok) → **build** in small pieces → **see it** run → **explain it back** (we say what it does in our own words, and it gets saved in `NOTES.md`). `NOTES.md` becomes the submission write-ups.

## The idea

Multiple AI agents make payments in parallel from one shared wallet, all spending from one team budget. This breaks in four ways:

1. **Overspending:** two agents both see $10 of budget left and both spend it at the same instant.
2. **Double paying:** an agent retries a payment it wrongly believes failed.
3. **Credential theft (attack):** one agent's access is stolen and the thief drains the wallet.
4. **Redirection (attack):** a seller or attacker swaps in a different address, so the money goes to the wrong party.

Hivemind is the layer between the agents and the wallet. Agents never touch the wallet. They send hivemind a purchase request and a reason. Hivemind checks the rules, sets the money aside so no other agent can take it, pays in USDC on GOAT, and records it with the transaction link.

GOAT moves the money and gives a receipt: it makes sure a payment *happens*. Hivemind makes sure it's a payment we *meant to make*: inside the team budget, inside that agent's limit, not a duplicate, and to the right seller. (The chain already stops a wallet spending more than it holds. Hivemind enforces everything else.)

## Problems and fixes

| Problem | Fix |
|---|---|
| Overspending | **Hold:** in one atomic step, check there's enough in the team budget and the agent's limit, and subtract it right away. Then pay with no lock held. Payment works → hold becomes spending. Payment fails → give the money back. |
| Double paying | **Purchase IDs:** the agent creates an ID once per purchase and reuses it on every retry. Same ID → return the first result, never pay again. |
| Credential theft | **Passes:** each agent gets a token, never the wallet key. Each token has its own spending limit and can be switched off without stopping the other agents. |
| Redirection | **Approved addresses:** each seller has a known payout address. If the 402 asks to be paid anywhere else, or in any token other than the official USDC, refuse. |
| Jammed payments (GOAT-specific) | **Payment queue:** every tx from a wallet needs the next nonce. If agents send at once, two take the same nonce and one is dropped or stuck. Only the payment queue hands out nonces. |

## Why it's fast

- **Lazy lock (the comparison):** one request at a time. Lock, check, pay, wait about 3.4 s for the block, unlock. That's about 1 payment per 3.4 s ≈ 0.3/s, however many agents there are.
- **Hivemind:** the lock covers only the hold (check + subtract), well under a millisecond. The 3.4 s wait happens with no lock held. With 20 payments in flight, that's about 20 / 3.4 s ≈ 6/s.
- The public RPC rate limit will probably be the real ceiling. Measure it; don't promise a number.

Holds are the same trick a card uses at a hotel. Don't claim we invented it. What's new is applying it to a fleet of agents sharing a wallet, and proving it live.

## Architecture

1. **Agents:** scripted workers (no LLM needed for the attack demos). They call `POST /buy {url, reason, purchaseId}` with a bearer token. They hold no key.
2. **Seller:** our own small x402-style server.
   - First request: `402 {price, payTo, orderId, expiry}`.
   - Retry with proof `{orderId, txHash}`: fetch the receipt and verify that its status is success, that it contains a `Transfer` note written by the official USDC contract with `to == payTo` and `amount >= price`, and that this txHash hasn't been used for another order. Then return the data. (The tx's own `to` is the USDC contract, not the seller, so don't check that. Use `chain.usdc_transfers(receipt)`.)
   - Needs demo modes: normal, slow (delays past the agent's timeout), and redirect (returns an attacker address).
3. **Hivemind gateway**, one process:
   - a. **Front door (policy gate 1, before contacting the seller):** token valid and not disabled, seller on the allowlist, rate limit.
   - b. **Seller client:** gets the 402 quote. After payment, sends the proof and fetches the data.
   - c. **Rules + hold (policy gate 2):** price under the cap, `payTo` matches the seller's approved address, then the hold on the team budget and the agent's limit.
   - d. **Payment queue:** the only holder of the private key. Assigns nonces, signs, saves, broadcasts. Many in flight at once.
   - e. **Watcher:** polls receipts. Success → hold becomes spending, and the actual gas (BTC) is recorded. Failure → release the hold.
   - f. **Logbook:** append-only. Records agent, reason, seller, amount, payTo, the policy decision and why, txHash, and explorer link for every state change.
   - g. **Database:** SQLite holding budgets, holds, orders, purchase IDs, and the log.
4. **GOAT Testnet3:** where the money moves.
5. **Naive version:** the same agents, but each one holds the private key and pays on its own. Used to show money being lost.
6. **Attack scripts:** trigger each problem against the naive version and against hivemind.
7. **Dashboard (nice to have):** live budget, holds, and log. If time is short, use terminal output plus explorer links.

## One payment, start to finish

Intent → Policy → Quote → Settlement → Audit

1. **Intent:** the agent calls `/buy` with its token, the URL, a reason, and a purchaseId.
2. **Policy (gate 1):** check the token, the seller, and the rate limit.
3. **Quote:** call the seller and get the 402 with price, payTo, and orderId.
4. **Policy (gate 2):** check price and address, then place the hold.
5. **Settlement:** the payment queue sends a USDC `transfer(payTo, price)` to the USDC contract. The watcher waits for the receipt. The seller client sends the proof, and the seller verifies on-chain and returns the data.
6. **Audit:** the logbook records every step with the explorer link.

Who pays whom:
- The operator funds the shared wallet: BTC for gas and USDC to spend, both from the official faucet.
- Hivemind signs.
- The seller receives USDC at payTo.
- Gas is paid in BTC, also from the shared wallet.
- Two currencies, kept separate: budgets and limits are in **USDC**; gas is in **BTC** and is tracked on its own.
- An agent's token is delegated spending authority, capped at its limit.

## Correctness rules (don't break these)

1. **The hold is one SQLite transaction**, opened with `BEGIN IMMEDIATE` (which locks the database for writing immediately), with no network calls and no `await` inside it.
   - It must keep both of these true: `spent + held <= teamBudget`, and for every agent, `spent_i + held_i <= limit_i`.
   - Hold amount = the price, in USDC. Gas is paid in BTC, so it isn't part of the hold. Before paying, check that the shared wallet has enough BTC for gas, and record the actual gas after confirmation.
2. **Only the payment queue touches the key and nonces.**
   - Read the starting nonce once at boot (`pending`), then increment locally.
   - If a tx is rejected while later nonces are already out, fill that nonce: resend it with a higher fee, or send a 0-value transfer to self. Release the failed order's hold.
3. **Save the signed tx before broadcasting.** Store the raw bytes, txHash, and nonce in SQLite first. The txHash is known before broadcast.
4. **Idempotency has three layers:**
   - unique `(agentId, purchaseId)`, so a retry returns the first result
   - unique `orderId`, so no order is ever paid twice
   - the seller accepts each txHash once
5. **Crash recovery at startup:**
   - held but never signed → release the hold
   - signed or broadcast, and the txHash is mined → confirm
   - signed or broadcast, not mined → rebroadcast the same raw bytes (the same nonce can only be mined once, so this can't double-pay)
   - the nonce was used by a different tx → release the hold
   - Then reconcile: the drop in the wallet's on-chain USDC balance should equal the confirmed prices, and the drop in its BTC balance should equal the recorded actual gas.
6. **Confirmed payments can't be reversed.** The hold is the undo before paying; after paying, prevention is the protection. Say this in the write-ups.
7. **A "global lock" mode flag** runs the same code but holds one lock from the check through confirmation. Use it only for the benchmark, so the chart compares identical code.

## GOAT Testnet3

- Chain ID 48816 (0xBEB0)
- RPC: https://rpc.testnet3.goat.network (public, rate-limited)
- Explorer: https://explorer.testnet3.goat.network
- EVM-compatible; gas is paid in BTC; web3.py works. One block about every 3.4 s (measured). Fees are tiny: a USDC transfer costs about 0.000000007 BTC (~52k gas).
- **web3 quirk:** GOAT blocks have 33 bytes of extraData, so web3 needs `ExtraDataToPOAMiddleware` (already set up in `chain.py`).
- **Payments are in stablecoins, as the hackathon rules require.** Official tokens:
  - USDC: `0x29D1Ee93e9ecf6E50F309f498e40a6b42D352Fa1` (6 decimals). This is the one we pay in.
  - USDT: `0xdcE0Af57E8F2CE957b3838Cd2A2F3F3677965Dd3` (6 decimals). Not used yet.
  - Many look-alike "USDC" tokens exist on the explorer. Only trust these addresses.
- **Faucets:**
  - BTC for gas: https://bridge.testnet3.goat.network/faucet, 0.00002 BTC once per wallet (enough for thousands of payments).
  - Token faucet contract: `0x89e7dfd01a86e5393ce6d8A78c9aa6653Ee113A6` (a proxy in front of `GoatTestnetFaucet`). Call `claimToken(token)`: 20 USDC per claim, **at most 2 claims per wallet** (40 USDC total). `canClaim(token, user)` says whether a claim is allowed and why not. Claiming costs a little BTC gas.
  - So demo prices should be tiny (around 0.01 USDC).
- Paying in USDC = calling `transfer(to, amount)` on the USDC contract. The tx's `to` is the USDC contract and its `value` is 0. The real payment is recorded as a `Transfer(from, to, value)` event (a note) in the receipt.
- The official USDC also supports `transferWithAuthorization` (EIP-3009, which x402 uses). We don't use it yet; it's an option if judges care about exact x402 compliance.
- GOAT's own SDK, `@goatnetwork/agentkit`, is TypeScript-only, and we're not using it. (`@goat-sdk/*`, by Crossmint, is a different, unrelated project.) Everything already passes through hivemind, and our own records power crash recovery.

## Tech

- **Python 3.11+ with asyncio.** Hivemind is I/O-bound: each payment is under 1 ms of CPU and about 3.4 s of waiting on the chain. asyncio runs many waits at once on one thread, so the GIL doesn't matter here.
- **web3.py** (`AsyncWeb3`) + **eth-account:** set nonces manually, sign locally, compute the tx hash before broadcasting, and send the raw tx with `send_raw_transaction`. Never let the library pick nonces.
- **FastAPI + uvicorn** for the gateway and the seller. Use `async def` endpoints.
- **httpx** (async) for agents → gateway and gateway → seller.
- **sqlite3** (standard library) for the database.
- **matplotlib** for the benchmark chart.
- Public repo with an MIT licence.

## Build order

| Step | Build | Done when |
|---|---|---|
| 0 ✅ | Repo + MIT licence, RPC client, wallets funded from the faucet, explorer link helper | One test USDC transfer shows on the explorer |
| 1 | Seller (normal mode), simple gateway, one agent | One command runs Intent → Audit and prints the tx link |
| 2 | Naive version + attack scripts (can run in parallel with 3–4) | Each attack visibly loses money or jams on the explorer |
| 3 | Holds, tokens with limits and a kill switch, approved addresses, price cap | Overspending, credential theft, and redirection attacks are blocked |
| 4 | Purchase IDs + payment queue | Double pay and nonce collisions are blocked; 20 agents run clean |
| 5 | Crash recovery + reconciliation | `kill -9` mid-payment, then restart: no double pay, and the ledger matches the chain |
| 6 | Global-lock flag, benchmark chart, dashboard | The chart shows lazy lock vs hivemind, both correct |
| 7 | Write-ups, demo script, backup video | Submitted before 10 AM Sunday |

Steps 0–5 are must-haves.

### Code so far
- `chain.py`: the only file that talks to the chain. Balances, `send()` (sign + broadcast), `pay_usdc()`, `claim_usdc()` / `can_claim_usdc()`, `wait_for_receipt()`, `usdc_transfers()` (reads the USDC notes in a receipt), explorer links. `send()` still asks the chain for the nonce each time; Step 4 replaces that with the payment queue.
- `make_wallets.py`: creates the shared, seller and attacker wallets in `wallets.json` (gitignored, never commit it).
- `claim_usdc.py`: claims USDC into the shared wallet from the token faucet.
- `send_test.py`: the Step 0 check; pays the seller 0.01 USDC and prints the proof.
- Run everything with `.venv/bin/python` (Python 3.14, web3 8.0.0).

## Demo

1. Naive version: run each attack and show the money lost on the explorer.
2. Hivemind: run the same attacks; they're blocked, and the log says why.
3. The chart: both are safe, and hivemind is faster.

The first minute must make the problem obvious.

## Submission

1. Public repo
2. Working prototype
3. Pitch (≤150 words; stages: Policy and Settlement)
4. Payment / value-flow explanation (200–300 words)
5. Security and failure-mode analysis (200–300 words, at least 2 vectors)
6. Dependencies and licences
7. Live demo

## Open questions

- **Qatom prizes.** The general prizes may require building on Qatom. Ask the organizers. If yes, put Settlement behind an interface (`settle(quote) -> receipt`) and add a Qatom adapter; the holds, rules, and idempotency don't change. Note that Qatom treats retries as new charges.
- **Rule checks:** teams must be 2–4 people; the repo must be public with its own licence.
