"""Everything that talks to the GOAT blockchain lives here.

The rest of the project calls these functions and never touches web3 directly.

Two kinds of money:
  BTC  - built into the chain. Pays the small fee ("gas") on every transaction.
  USDC - a token: a program on the chain that keeps its own list of balances.
         Paying in USDC = asking that program to move numbers between rows.
"""

import json
from decimal import Decimal

from eth_account import Account
from web3 import AsyncHTTPProvider, AsyncWeb3
from web3.logs import DISCARD
from web3.middleware import ExtraDataToPOAMiddleware

# --- Network settings (GOAT Testnet3) ---------------------------------------

RPC_URL = "https://rpc.testnet3.goat.network"
CHAIN_ID = 48816
EXPLORER = "https://explorer.testnet3.goat.network"

# Test USDC with a public claimTokens() faucet: 10 USDC per wallet per 24 h.
# TODO: confirm with the organizers this is the token the rules expect.
USDC_ADDRESS = "0x5900e383062Cbe2d8f977e10AEF990cc9472C99c"
USDC_DECIMALS = 6  # 1 USDC is stored as 1_000_000 "units"

# The few token functions we use, described so web3 knows how to call them.
TOKEN_ABI = [
    {"name": "balanceOf", "type": "function", "stateMutability": "view",
     "inputs": [{"name": "owner", "type": "address"}],
     "outputs": [{"name": "", "type": "uint256"}]},
    {"name": "transfer", "type": "function", "stateMutability": "nonpayable",
     "inputs": [{"name": "to", "type": "address"}, {"name": "value", "type": "uint256"}],
     "outputs": [{"name": "", "type": "bool"}]},
    {"name": "claimTokens", "type": "function", "stateMutability": "nonpayable",
     "inputs": [], "outputs": []},
    {"name": "Transfer", "type": "event", "anonymous": False,
     "inputs": [{"name": "from", "type": "address", "indexed": True},
                {"name": "to", "type": "address", "indexed": True},
                {"name": "value", "type": "uint256", "indexed": False}]},
]

# --- Connection ---------------------------------------------------------------

w3 = AsyncWeb3(AsyncHTTPProvider(RPC_URL))
# GOAT blocks carry one extra byte of data that web3 rejects by default.
w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)

usdc = w3.eth.contract(address=USDC_ADDRESS, abi=TOKEN_ABI)


# --- Wallets ------------------------------------------------------------------

def load_wallet(role, path="wallets.json"):
    """Return the eth_account Account (address + secret key) for a role."""
    with open(path) as f:
        return Account.from_key(json.load(f)[role]["key"])


def load_address(role, path="wallets.json"):
    """Return just the public address for a role (no secret key)."""
    with open(path) as f:
        return json.load(f)[role]["address"]


# --- Amounts ------------------------------------------------------------------

def usdc_to_units(amount):
    """1.5 USDC -> 1_500_000 units. The chain only stores whole numbers."""
    return int(Decimal(str(amount)) * 10**USDC_DECIMALS)


def units_to_usdc(units):
    """1_500_000 units -> Decimal('1.5')."""
    return Decimal(units) / 10**USDC_DECIMALS


# --- Explorer links -------------------------------------------------------------

def tx_link(tx_hash):
    return f"{EXPLORER}/tx/{tx_hash}"


def address_link(address):
    return f"{EXPLORER}/address/{address}"


# --- Reading balances -------------------------------------------------------------

async def btc_balance(address):
    """BTC (for fees) held by an address, in BTC."""
    wei = await w3.eth.get_balance(address)
    return Decimal(wei) / 10**18


async def usdc_balance(address):
    """USDC held by an address, read from the token's balance list."""
    units = await usdc.functions.balanceOf(address).call()
    return units_to_usdc(units)


# --- Sending transactions -------------------------------------------------------

async def send(account, call):
    """Sign and broadcast one token call (e.g. a transfer). Returns the tx hash.

    Steps: pick the cheque number (nonce), set fees, sign locally, broadcast.
    The secret key never leaves this computer; only the signature is sent.

    NOTE: the nonce is asked from the chain each time. That is fine for one
    payment at a time but breaks when many are sent at once (the "jam").
    Step 4 replaces this with a payment queue that hands out nonces itself.
    """
    nonce = await w3.eth.get_transaction_count(account.address, "pending")
    priority_fee = await w3.eth.max_priority_fee
    base_fee = (await w3.eth.get_block("latest"))["baseFeePerGas"]

    tx = await call.build_transaction({
        "from": account.address,
        "nonce": nonce,
        "chainId": CHAIN_ID,
        "maxPriorityFeePerGas": priority_fee,
        "maxFeePerGas": 2 * base_fee + priority_fee,  # room if the base fee rises
    })
    signed = account.sign_transaction(tx)
    # The hash is known before broadcasting (Step 5 relies on this).
    tx_hash = "0x" + signed.hash.hex().removeprefix("0x")
    await w3.eth.send_raw_transaction(signed.raw_transaction)
    return tx_hash


async def pay_usdc(account, to, amount):
    """Ask the USDC program to move `amount` USDC from `account` to `to`."""
    return await send(account, usdc.functions.transfer(to, usdc_to_units(amount)))


async def claim_usdc(account):
    """Claim free test USDC from the token's faucet (10 USDC, once per 24 h)."""
    return await send(account, usdc.functions.claimTokens())


# --- Receipts --------------------------------------------------------------------

async def wait_for_receipt(tx_hash, timeout=60):
    """Wait until the tx is in a block (~3.4 s), then return its receipt."""
    return await w3.eth.wait_for_transaction_receipt(tx_hash, timeout=timeout)


def usdc_transfers(receipt):
    """Read the USDC program's notes in a receipt: [(from, to, amount), ...].

    Only notes written by the real USDC program count, so a fake token
    with the same name can't pass as a payment.
    """
    events = usdc.events.Transfer().process_receipt(receipt, errors=DISCARD)
    return [
        (e.args["from"], e.args["to"], units_to_usdc(e.args["value"]))
        for e in events
        if e.address.lower() == USDC_ADDRESS.lower()
    ]
