"""SQLite schema + ingest for the money aggregator.


- accounts are keyed on `identification_hash` (stable across re-authorization),
  while `uid` (the API handle) is refreshed on every authorize.
- transactions dedup on `dedup_id`: transaction_id when the bank provides one,
  else a content hash (value_date + amount + indicator + remittance +
  counterparty + codes) plus an occurrence index for byte-identical records in
  the same poll. entry_reference is NOT used as a key: banks reuse it across
  recurring/standing-order payments (proven data loss on the mock ASPSP, where
  one reference covered 44 distinct transactions). We UPSERT so a pending (PDNG)
  txn that later books updates in place instead of duplicating.
- every transaction keeps its full `raw_json` so no bank field is ever lost.
- amounts are stored as the exact API string plus a signed REAL for querying.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).parent / "money.sqlite"

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
  account_key       TEXT PRIMARY KEY,   -- identification_hash (stable)
  uid               TEXT,               -- current session handle for API calls
  iban              TEXT,
  bban              TEXT,
  name              TEXT,
  currency          TEXT,
  cash_account_type TEXT,
  usage             TEXT,
  product           TEXT,
  details           TEXT,
  aspsp_name        TEXT,
  aspsp_country     TEXT,
  first_seen        TEXT,
  last_updated      TEXT
);

CREATE TABLE IF NOT EXISTS balances (
  account_key    TEXT NOT NULL REFERENCES accounts(account_key),
  balance_type   TEXT,
  amount         TEXT,
  currency       TEXT,
  reference_date TEXT,
  fetched_at     TEXT,
  PRIMARY KEY (account_key, balance_type, reference_date, amount)
);

CREATE TABLE IF NOT EXISTS transactions (
  dedup_id               TEXT PRIMARY KEY,
  account_key            TEXT NOT NULL REFERENCES accounts(account_key),
  transaction_id         TEXT,
  entry_reference        TEXT,
  booking_date           TEXT,
  value_date             TEXT,
  transaction_date       TEXT,
  amount                 TEXT,     -- exact string from API
  signed_amount          REAL,     -- DBIT negative, CRDT positive
  currency               TEXT,
  credit_debit_indicator TEXT,
  status                 TEXT,
  creditor_name          TEXT,
  debtor_name            TEXT,
  remittance_information TEXT,
  bank_transaction_code  TEXT,
  merchant_category_code TEXT,
  raw_json               TEXT,
  first_seen             TEXT,
  last_seen              TEXT
);
CREATE INDEX IF NOT EXISTS idx_txn_account_date
  ON transactions(account_key, booking_date);

-- --- categorization layer (decoupled from ingest) ---------------------
CREATE TABLE IF NOT EXISTS categories (
  id         INTEGER PRIMARY KEY,
  name       TEXT NOT NULL UNIQUE,
  group_name TEXT,
  kind       TEXT NOT NULL DEFAULT 'expense',  -- expense|income|transfer
  color      TEXT
);

CREATE TABLE IF NOT EXISTS category_rules (
  id          INTEGER PRIMARY KEY,
  priority    INTEGER NOT NULL DEFAULT 100,     -- lower is checked first
  match_type  TEXT NOT NULL,                    -- merchant|remittance|text|mcc|btc
  pattern     TEXT NOT NULL,                    -- substring, or MCC code / 'lo-hi' range
  category_id INTEGER NOT NULL REFERENCES categories(id),
  note        TEXT
);

CREATE TABLE IF NOT EXISTS tx_category (
  dedup_id        TEXT PRIMARY KEY REFERENCES transactions(dedup_id),
  category_id     INTEGER REFERENCES categories(id),
  source          TEXT NOT NULL DEFAULT 'auto', -- auto|manual (manual is sticky)
  matched_rule_id INTEGER,
  updated_at      TEXT
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def connect(path: Path = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    return conn


# --- account ingest -------------------------------------------------------

def account_key(acct: dict) -> str:
    return acct.get("identification_hash") or acct["uid"]


def _bban(acct: dict) -> str | None:
    for aid in acct.get("all_account_ids", []):
        if aid.get("scheme_name") == "BBAN":
            return aid.get("identification")
    other = (acct.get("account_id") or {}).get("other") or {}
    return other.get("identification")


def upsert_account(conn: sqlite3.Connection, aspsp: dict, acct: dict) -> str:
    ak = account_key(acct)
    iban = (acct.get("account_id") or {}).get("iban")
    conn.execute(
        """
        INSERT INTO accounts (account_key, uid, iban, bban, name, currency,
            cash_account_type, usage, product, details, aspsp_name,
            aspsp_country, first_seen, last_updated)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(account_key) DO UPDATE SET
            uid=excluded.uid,
            iban=excluded.iban,
            name=excluded.name,
            currency=excluded.currency,
            last_updated=excluded.last_updated
        """,
        (ak, acct.get("uid"), iban, _bban(acct), acct.get("name"),
         acct.get("currency"), acct.get("cash_account_type"),
         acct.get("usage"), acct.get("product"), acct.get("details"),
         aspsp.get("name"), aspsp.get("country"), now_iso(), now_iso()),
    )
    return ak


# --- balance ingest -------------------------------------------------------

def store_balances(conn: sqlite3.Connection, ak: str, balances: list[dict]) -> int:
    fetched = now_iso()
    n = 0
    for b in balances:
        amt = b.get("balance_amount") or {}
        cur = conn.execute(
            """INSERT OR IGNORE INTO balances
               (account_key, balance_type, amount, currency, reference_date, fetched_at)
               VALUES (?,?,?,?,?,?)""",
            (ak, b.get("balance_type"), amt.get("amount"), amt.get("currency"),
             b.get("reference_date"), fetched),
        )
        n += cur.rowcount
    return n


# --- transaction ingest ---------------------------------------------------

def _content_hash(ak: str, t: dict) -> str:
    """Hash the fields that identify a transaction's economic content.

    Uses value_date (not booking_date) and omits status, so a pending txn and
    its later booked form collapse to the same hash. entry_reference is
    deliberately excluded - banks reuse it across distinct payments.
    """
    amt = t.get("transaction_amount") or {}
    basis = json.dumps(
        {
            "date": t.get("value_date") or t.get("booking_date")
                    or t.get("transaction_date"),
            "amount": amt.get("amount"),
            "currency": amt.get("currency"),
            "ind": t.get("credit_debit_indicator"),
            "remit": t.get("remittance_information"),
            "creditor": _name(t.get("creditor")),
            "debtor": _name(t.get("debtor")),
            "btc": _btc(t),
            "mcc": t.get("merchant_category_code"),
        },
        sort_keys=True, ensure_ascii=False,
    )
    return hashlib.sha256(f"{ak}:{basis}".encode()).hexdigest()


def _dedup_ids(ak: str, txns: list[dict]) -> list[str]:
    """Compute a stable dedup_id per transaction across the WHOLE batch.

    transaction_id wins when present. Otherwise a content hash, suffixed with an
    occurrence index so genuinely-identical records in the same account (e.g.
    two identical same-day payments) each keep a distinct, reproducible id.
    """
    ids: list[str] = []
    counts: dict[str, int] = {}
    for t in txns:
        if t.get("transaction_id"):
            ids.append(f"txid:{ak}:{t['transaction_id']}")
            continue
        h = _content_hash(ak, t)
        idx = counts.get(h, 0)
        counts[h] = idx + 1
        ids.append(f"hash:{h}:{idx}")
    return ids


def _signed_amount(t: dict) -> float | None:
    amt = (t.get("transaction_amount") or {}).get("amount")
    if amt is None:
        return None
    val = float(amt)
    return -val if t.get("credit_debit_indicator") == "DBIT" else val


def _name(party: dict | None) -> str | None:
    return party.get("name") if party else None


def _btc(t: dict) -> str | None:
    b = t.get("bank_transaction_code") or {}
    parts = [b.get("code"), b.get("sub_code")]
    joined = "/".join(p for p in parts if p)
    return joined or None


def store_transactions(conn: sqlite3.Connection, ak: str,
                       txns: list[dict]) -> tuple[int, int]:
    """Returns (new, updated) so callers can prove dedup works.

    Pass the FULL per-account transaction list (all pages) so the occurrence
    index in _dedup_ids is stable across page boundaries.
    """
    seen = now_iso()
    dedup_ids = _dedup_ids(ak, txns)
    new = updated = 0
    for did, t in zip(dedup_ids, txns):
        existed = conn.execute(
            "SELECT 1 FROM transactions WHERE dedup_id=?", (did,)).fetchone()
        amt = t.get("transaction_amount") or {}
        remittance = t.get("remittance_information") or []
        conn.execute(
            """
            INSERT INTO transactions (
                dedup_id, account_key, transaction_id, entry_reference,
                booking_date, value_date, transaction_date, amount,
                signed_amount, currency, credit_debit_indicator, status,
                creditor_name, debtor_name, remittance_information,
                bank_transaction_code, merchant_category_code, raw_json,
                first_seen, last_seen)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(dedup_id) DO UPDATE SET
                status=excluded.status,
                booking_date=excluded.booking_date,
                value_date=excluded.value_date,
                transaction_id=excluded.transaction_id,
                raw_json=excluded.raw_json,
                last_seen=excluded.last_seen
            """,
            (did, ak, t.get("transaction_id"), t.get("entry_reference"),
             t.get("booking_date"), t.get("value_date"), t.get("transaction_date"),
             amt.get("amount"), _signed_amount(t), amt.get("currency"),
             t.get("credit_debit_indicator"), t.get("status"),
             _name(t.get("creditor")), _name(t.get("debtor")),
             " | ".join(remittance), _btc(t), t.get("merchant_category_code"),
             json.dumps(t, ensure_ascii=False), seen, seen),
        )
        if existed:
            updated += 1
        else:
            new += 1
    return new, updated
