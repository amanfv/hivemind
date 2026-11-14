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

## Step 1: one full purchase through hivemind

**What we built:** a shop (`seller.py`), hivemind (`gateway.py` + its logbook in `db.py`), and an agent (`agent.py`). One command, `python agent.py`, buys a report and prints the proof link.

**The flow:** Intent → Policy → Quote → Settlement → Audit
1. The agent asks hivemind to buy a report, showing its badge (token), the URL, a reason and a purchase ID. The agent never has the wallet key.
2. Hivemind checks the badge.
3. Hivemind asks the shop, and the shop replies **402 Payment Required**: price, where to pay, order number.
4. Hivemind pays in USDC on GOAT, waits for the receipt, then goes back to the shop with the proof (order number + payment ID).
5. The shop looks the payment up **on the chain itself**. It doesn't take hivemind's word for it. Then it hands over the report.
6. Every stage is written in hivemind's logbook, which is only ever added to.

**Already protected:** a fake badge is refused, and the shop won't accept the same payment twice for two different orders.

**Not protected yet (on purpose, shown in Step 2):** hivemind pays whatever the shop asks, to whatever address it gives, with no budget. Steps 3–4 add those rules.

**First purchase:** https://explorer.testnet3.goat.network/tx/0x421ac51badccafbeda643220df97d23b69cef9d54c775747be2397ce219f262c

**In our own words: why the shop checks the chain instead of trusting hivemind:**
The shop trusts the chain because it's the source of truth. Hivemind is just a payment layer in between, and its word doesn't have to be true. To the shop, hivemind is a stranger, and its proof is only a claim, like a screenshot of a bank transfer. The chain is a public record nobody can fake, so the shop checks it itself, the way you'd open your own bank app before handing over the bike.

This splits the jobs: **the chain** proves a payment *really happened*; **hivemind** makes sure it was a payment we *meant to make*.

## Step 2: the naive version loses money

**The naive version:** no hivemind. Every agent holds the wallet key and pays on its own. It tries to stick to a team budget by reading "spent so far" from a shared file before paying.

**Five attacks, five losses (all on the explorer):**
| Attack | What happened |
|---|---|
| Overspending | Budget 0.05, 10 agents a split second apart. The first 9 saw "spent = 0" because nobody's payment had confirmed yet (the 10th saw 0.04, still "room left"). **Spent 0.10, 200% of the budget.** |
| Traffic jam | 10 agents at the same instant all got cheque #13. **1 payment landed, 9 of 10 purchases failed.** |
| Double paying | The slow shop took 12 s, the agent gave up at 10 s and bought again. **Paid twice for one report.** |
| Wrong shop | The quote's address was swapped to the attacker's (`0xBE82…` vs the real `0xBe8F…`: nearly identical at a glance). **Attacker got 0.01, agent got nothing.** |
| Stolen access | The thief copied one agent's credentials, which *are* the wallet key. **The whole wallet (0.83 USDC) went to the attacker in one payment.** |

**Something we discovered:** GOAT's public server doesn't count payments still waiting to go into a block when you ask "what's my next cheque number?". Agents even 0.4 s apart got the same number. So the overspend demo uses agents with the jam fixed (they count cheque numbers themselves), to show that **even with the jam fixed, the budget still breaks.**

**In our own words:** _(to fill in: why did the agents see "spent = 0" even though others had already paid?)_

## Step 3: hivemind's rules block three attacks

**What we built:** four protections, checked at two gates, plus reasons in the logbook.
- **Gate 1** (before contacting the shop): is the pass real and switched on? Is the shop on our approved list?
- **Gate 2** (on the shop's quote): official USDC? Paying the shop's *approved* address? Price under the cap? Quote not expired?
- **The hold:** in one locked database step (well under a millisecond, no network calls), check there's room in the team budget AND this agent's limit, and set the money aside right away. Then pay with no lock held. Payment confirmed → spent. Failed → released.

**Same attacks, now against hivemind:**
| Attack | Naive | Hivemind |
|---|---|---|
| Overspending | 10 paid, 0.10 on a 0.05 budget (200%) | 10 agents at the *same instant*: **exactly 5 paid, 5 refused**, spent 100% |
| Wrong shop | Attacker got 0.01, no report | **Refused before paying:** payTo isn't the shop's approved address. 0 lost. |
| Stolen access | Whole wallet to the attacker | Thief has agent3's *pass*, not the key: can't pay itself (refused), 3 of 6 paid then **over agent3's 0.03 limit**, **off switch** stops it, agent1 keeps working. **Attacker got 0.** |

**The key line for the pitch:** with hivemind, a stolen pass can only ever buy from approved shops, up to that one agent's limit. It can never send money to the thief.

**In our own words:** _(to fill in: how does the hold stop two agents from both seeing "room left"?)_
