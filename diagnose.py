"""Read-only health check: history coverage, balance reconciliation, rule quality.

Answers the three questions the dashboard can't: does each account actually
have the history you think it has, does the transaction history explain the
bank's balance, and which rules are firing on things they shouldn't.

Usage: ./.venv/bin/python diagnose.py
"""
from __future__ import annotations

import sqlite3
from datetime import date

import db

BOOKED_PREFERENCE = db.BALANCE_PREFERENCE


def conn_ro() -> sqlite3.Connection:
    c = sqlite3.connect(f"file:{db.DB_PATH}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    return c


def coverage(c: sqlite3.Connection) -> None:
    print("=== history coverage per account ===")
    print("An account whose earliest transaction is ~90 days old was fetched")
    print("without an explicit date_from, or the bank caps unattended access.\n")
    rows = c.execute("""
        SELECT a.aspsp_name, a.name, a.currency, t.account_key,
               COUNT(*) n,
               MIN(COALESCE(t.booking_date,t.value_date)) lo,
               MAX(COALESCE(t.booking_date,t.value_date)) hi
        FROM transactions t LEFT JOIN accounts a ON a.account_key=t.account_key
        GROUP BY t.account_key ORDER BY lo""").fetchall()
    today = date.today()
    for r in rows:
        span = "?"
        if r["lo"]:
            span = f"{(today - date.fromisoformat(r['lo'])).days}d back"
        flag = ""
        if r["lo"] and (today - date.fromisoformat(r["lo"])).days < 120:
            flag = "  <-- SHORT history, likely truncated"
        print(f"  {r['aspsp_name'] or '?':22} {(r['name'] or '')[:18]:18} "
              f"{r['n']:6} txns  {r['lo']} -> {r['hi']}  ({span}){flag}")
    # accounts we know about but never got transactions for
    for r in c.execute("""
        SELECT aspsp_name, name FROM accounts WHERE account_key NOT IN
        (SELECT DISTINCT account_key FROM transactions)"""):
        print(f"  {r['aspsp_name'] or '?':22} {(r['name'] or '')[:18]:18} "
              f"  NO TRANSACTIONS AT ALL")


def reconcile(c: sqlite3.Connection) -> None:
    print("\n=== balance reconciliation ===")
    print("gap = bank balance - sum(all stored transactions). A non-zero gap is")
    print("the opening balance of the period you actually hold, i.e. how much")
    print("history is missing. It should be stable between polls, not growing.\n")
    for a in c.execute("SELECT account_key, name, aspsp_name, currency FROM accounts"):
        bals = c.execute("""SELECT balance_type, amount, reference_date FROM balances
                            WHERE account_key=?""", (a["account_key"],)).fetchall()
        if not bals:
            print(f"  {a['aspsp_name']:22} {(a['name'] or '')[:18]:18}  no balance stored")
            continue
        bals.sort(key=lambda b: (
            BOOKED_PREFERENCE.index(b["balance_type"])
            if b["balance_type"] in BOOKED_PREFERENCE else 99,
            b["reference_date"] or "", ), reverse=False)
        # newest row of the most-preferred type
        best_type = bals[0]["balance_type"]
        same = [b for b in bals if b["balance_type"] == best_type]
        b = max(same, key=lambda x: x["reference_date"] or "")
        total = c.execute("SELECT COALESCE(SUM(signed_amount),0) FROM transactions "
                          "WHERE account_key=?", (a["account_key"],)).fetchone()[0]
        gap = float(b["amount"]) - total
        types = ",".join(sorted({x["balance_type"] for x in bals}))
        print(f"  {a['aspsp_name']:22} {(a['name'] or '')[:18]:18} "
              f"balance {float(b['amount']):12,.2f} ({b['balance_type']} @ {b['reference_date']}) "
              f"- txns {total:12,.2f} = gap {gap:12,.2f}   [types: {types}]")


def rule_quality(c: sqlite3.Connection) -> None:
    print("\n=== auto-categorization by rule ===")
    print("Look for a rule with a suspiciously large count, or one whose")
    print("category holds a lot of income -- that is a false-positive pattern.\n")
    rows = c.execute("""
        SELECT cat.name category, cr.match_type, cr.pattern, cr.note,
               COUNT(*) n,
               SUM(CASE WHEN t.credit_debit_indicator='CRDT' THEN 1 ELSE 0 END) credits
        FROM tx_category tc
        JOIN transactions t ON t.dedup_id = tc.dedup_id
        JOIN categories cat ON cat.id = tc.category_id
        LEFT JOIN category_rules cr ON cr.id = tc.matched_rule_id
        WHERE tc.source='auto'
        GROUP BY tc.matched_rule_id ORDER BY n DESC LIMIT 25""").fetchall()
    for r in rows:
        pat = f"{r['match_type']}:{r['pattern']}" if r["pattern"] else "(no rule -> fallback)"
        warn = "  <-- mostly income?" if r["n"] and r["credits"] > r["n"] * .5 else ""
        print(f"  {r['n']:6}  {r['credits']:5} cr  {r['category']:18} {pat}{warn}")

    print("\n=== categorization progress ===")
    tot, un = c.execute("""
        SELECT COUNT(*), SUM(CASE WHEN cat.name IS NULL OR cat.name='Uncategorized'
                                  THEN 1 ELSE 0 END)
        FROM transactions t
        LEFT JOIN tx_category tc ON tc.dedup_id=t.dedup_id
        LEFT JOIN categories cat ON cat.id=tc.category_id""").fetchone()
    print(f"  {tot - (un or 0)}/{tot} categorized ({100*(tot-(un or 0))/tot:.1f}%)")
    print("\n  biggest uncategorized payees (each one you fix teaches a rule):")
    for r in c.execute("""
        SELECT COALESCE(t.creditor_name, t.debtor_name, t.remittance_information,
                        '(unknown)') payee,
               COUNT(*) n, ROUND(SUM(ABS(t.signed_amount)),2) vol
        FROM transactions t
        LEFT JOIN tx_category tc ON tc.dedup_id=t.dedup_id
        LEFT JOIN categories cat ON cat.id=tc.category_id
        WHERE cat.name IS NULL OR cat.name='Uncategorized'
        GROUP BY payee ORDER BY n DESC LIMIT 15"""):
        print(f"    {r['n']:5}  {r['vol']:12,.2f}  {(r['payee'] or '')[:60]}")


def main() -> None:
    c = conn_ro()
    try:
        print(f"DB: {db.DB_PATH}\n")
        coverage(c)
        reconcile(c)
        rule_quality(c)
    finally:
        c.close()


if __name__ == "__main__":
    main()
