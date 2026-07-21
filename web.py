"""Local read-only JSON API + static frontend for the money aggregator.

The API (/api/*) is the reusable contract a future phone/PWA client will call;
the browser UI is a separate static app in static/. The DB is opened READ-ONLY
for all reads; the single write path (manual category override) uses an explicit
read-write connection.

All /api/stats/* endpoints share one filter (date range, accounts, categories,
flow, search, amount range) so every tile/chart/table stays consistent. Amounts
are single-currency (EUR in production; mock data is DKK).

Bind to 127.0.0.1 only. From a laptop: ssh -L 8000:localhost:8000 <host>.
Run: ./.venv/bin/python -m uvicorn web:app --host 127.0.0.1 --port 8000
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi import Body, Depends, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

import categorize
import db

STATIC = Path(__file__).parent / "static"
BALANCE_PREFERENCE = db.BALANCE_PREFERENCE

app = FastAPI(title="Money Aggregator", version="0.2")

# Transactions joined to their category — the base of every stats query.
BASE = """
FROM transactions t
LEFT JOIN tx_category tc ON tc.dedup_id = t.dedup_id
LEFT JOIN categories  c  ON c.id = tc.category_id
"""
DATE_EXPR = "COALESCE(t.booking_date, t.value_date)"


def rows(sql: str, params: tuple = ()) -> list[dict]:
    conn = sqlite3.connect(f"file:{db.DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def _filter(date_from, date_to, account, category, flow, q, amount_min, amount_max):
    """Build a WHERE clause + params shared by all stats endpoints.

    flow: income|expense|transfer. Income/expense exclude transfer-kind
    categories so internal moves don't inflate spending.
    """
    where, params = [], []
    if date_from:
        where.append(f"{DATE_EXPR} >= ?"); params.append(date_from)
    if date_to:
        where.append(f"{DATE_EXPR} <= ?"); params.append(date_to)
    if account:
        where.append(f"t.account_key IN ({','.join('?' * len(account))})")
        params += account
    if category:
        where.append(f"c.name IN ({','.join('?' * len(category))})")
        params += category
    if flow == "income":
        where.append("t.credit_debit_indicator='CRDT' AND COALESCE(c.kind,'expense')!='transfer'")
    elif flow == "expense":
        where.append("t.credit_debit_indicator='DBIT' AND COALESCE(c.kind,'expense')!='transfer'")
    elif flow == "transfer":
        where.append("c.kind='transfer'")
    if q:
        where.append("(t.remittance_information LIKE ? OR t.creditor_name LIKE ? "
                     "OR t.debtor_name LIKE ?)")
        params += [f"%{q}%"] * 3
    if amount_min is not None:
        where.append("ABS(t.signed_amount) >= ?"); params.append(amount_min)
    if amount_max is not None:
        where.append("ABS(t.signed_amount) <= ?"); params.append(amount_max)
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    return clause, params


# Shared query params -> keeps every endpoint signature identical.
def filters(
    date_from: str | None = None,
    date_to: str | None = None,
    account: list[str] | None = Query(None),
    category: list[str] | None = Query(None),
    flow: str | None = None,
    q: str | None = None,
    amount_min: float | None = None,
    amount_max: float | None = None,
):
    return _filter(date_from, date_to, account, category, flow, q, amount_min, amount_max)


# --- reference data -------------------------------------------------------

@app.get("/api/filters")
def filter_options() -> dict:
    accounts = rows("SELECT account_key, name, aspsp_name, currency FROM accounts "
                    "ORDER BY aspsp_name, name")
    cats = rows("SELECT name, group_name, kind, color FROM categories "
                "ORDER BY group_name, name")
    bounds = rows(f"SELECT MIN({DATE_EXPR}) lo, MAX({DATE_EXPR}) hi FROM transactions t")[0]
    return {"accounts": accounts, "categories": cats, "date_min": bounds["lo"],
            "date_max": bounds["hi"]}


@app.get("/api/categories")
def categories() -> list[dict]:
    return rows("SELECT id, name, group_name, kind, color FROM categories "
                "ORDER BY group_name, name")


# --- stats ----------------------------------------------------------------

@app.get("/api/stats/summary")
def stats_summary(f=Depends(filters)) -> dict:
    where, params = f
    r = rows(f"""
        SELECT
          COALESCE(SUM(CASE WHEN t.credit_debit_indicator='CRDT'
                            AND COALESCE(c.kind,'expense')!='transfer'
                       THEN t.signed_amount END), 0) AS income,
          COALESCE(SUM(CASE WHEN t.credit_debit_indicator='DBIT'
                            AND COALESCE(c.kind,'expense')!='transfer'
                       THEN -t.signed_amount END), 0) AS expense,
          COALESCE(SUM(CASE WHEN c.kind='transfer'
                       THEN ABS(t.signed_amount) END), 0) AS transfers,
          COALESCE(SUM(t.signed_amount), 0) AS net_cash,
          COUNT(*) AS count
        {BASE} {where}""", tuple(params))[0]
    # net  = income - expense, both excluding transfer-kind categories. Moving
    #        money between your own accounts must not read as earning/spending.
    # net_cash = every selected transaction summed as-is, transfers included.
    #        This is the figure that reconciles against account balances; it
    #        differs from `net` by exactly the transfer legs in the selection.
    r["net"] = round(r["income"] - r["expense"], 2)
    for k in ("income", "expense", "transfers", "net_cash"):
        r[k] = round(r[k], 2)
    return r


@app.get("/api/stats/by-category")
def stats_by_category(f=Depends(filters)) -> list[dict]:
    """Per-category money in and out, kept directional.

    `expense` and `income` are separate because summing ABS() mixes the two:
    a category holding both a refund and a purchase would report their sum as
    "spending". The `expense` column here adds up to exactly the Expenses tile.
    Grouped by NAME, not c.id: transactions with no tx_category row at all join
    to NULL and would otherwise form a second, separate "Uncategorized" slice.
    """
    where, params = f
    return rows(f"""
        SELECT COALESCE(c.name,'Uncategorized') AS category,
               MAX(c.group_name) AS group_name, MAX(c.color) AS color,
               COALESCE(MAX(c.kind),'expense') AS kind,
               ROUND(COALESCE(SUM(CASE WHEN t.credit_debit_indicator='DBIT'
                                  THEN -t.signed_amount END),0),2) AS expense,
               ROUND(COALESCE(SUM(CASE WHEN t.credit_debit_indicator='CRDT'
                                  THEN t.signed_amount END),0),2) AS income,
               ROUND(SUM(t.signed_amount),2) AS net,
               ROUND(SUM(ABS(t.signed_amount)),2) AS total,
               COUNT(*) AS count
        {BASE} {where}
        GROUP BY COALESCE(c.name,'Uncategorized')
        ORDER BY expense DESC""", tuple(params))


@app.get("/api/stats/by-month")
def stats_by_month(f=Depends(filters)) -> list[dict]:
    where, params = f
    return rows(f"""
        SELECT substr({DATE_EXPR},1,7) AS month,
          ROUND(SUM(CASE WHEN t.credit_debit_indicator='CRDT'
                         AND COALESCE(c.kind,'expense')!='transfer'
                    THEN t.signed_amount END),2) AS income,
          ROUND(SUM(CASE WHEN t.credit_debit_indicator='DBIT'
                         AND COALESCE(c.kind,'expense')!='transfer'
                    THEN -t.signed_amount END),2) AS expense
        {BASE} {where}
        GROUP BY month ORDER BY month""", tuple(params))


@app.get("/api/stats/by-merchant")
def stats_by_merchant(f=Depends(filters), limit: int = Query(20, ge=1, le=200)) -> list[dict]:
    where, params = f
    return rows(f"""
        SELECT COALESCE(t.creditor_name, t.debtor_name, t.remittance_information,
                        '(unknown)') AS merchant,
               ROUND(SUM(ABS(t.signed_amount)),2) AS total, COUNT(*) AS count
        {BASE} {where}
        GROUP BY merchant ORDER BY total DESC LIMIT ?""", tuple(params) + (limit,))


@app.get("/api/stats/by-account")
def stats_by_account(f=Depends(filters)) -> list[dict]:
    where, params = f
    return rows(f"""
        SELECT t.account_key, a.name, a.aspsp_name,
          ROUND(COALESCE(SUM(CASE WHEN t.credit_debit_indicator='CRDT'
                            AND COALESCE(c.kind,'expense')!='transfer'
                       THEN t.signed_amount END),0),2) AS income,
          ROUND(COALESCE(SUM(CASE WHEN t.credit_debit_indicator='DBIT'
                            AND COALESCE(c.kind,'expense')!='transfer'
                       THEN -t.signed_amount END),0),2) AS expense,
          ROUND(COALESCE(SUM(t.signed_amount),0),2) AS net_cash,
          MIN({DATE_EXPR}) AS first_date, MAX({DATE_EXPR}) AS last_date,
          COUNT(*) AS count
        {BASE} LEFT JOIN accounts a ON a.account_key = t.account_key {where}
        GROUP BY t.account_key ORDER BY expense DESC""", tuple(params))


# --- transactions (list, filter-aware, with category) ---------------------

@app.get("/api/transactions")
def transactions(f=Depends(filters), group_by: str | None = None,
                 limit: int = Query(100, ge=1, le=1000),
                 offset: int = Query(0, ge=0)) -> dict:
    where, params = f
    total = rows(f"SELECT COUNT(*) n {BASE} {where}", tuple(params))[0]["n"]
    items = rows(f"""
        SELECT t.dedup_id, t.account_key, t.booking_date, t.value_date,
               t.signed_amount, t.currency, t.credit_debit_indicator, t.status,
               t.creditor_name, t.debtor_name, t.remittance_information,
               c.name AS category, c.color AS category_color,
               c.group_name AS category_group, tc.source AS category_source
        {BASE} {where}
        ORDER BY {DATE_EXPR} DESC, t.dedup_id
        LIMIT ? OFFSET ?""", tuple(params) + (limit, offset))
    return {"total": total, "limit": limit, "offset": offset, "items": items}


# --- accounts + summary tiles --------------------------------------------

def _chosen_balance(account_key: str) -> dict | None:
    bals = rows("SELECT balance_type, amount, currency, reference_date "
                "FROM balances WHERE account_key=?", (account_key,))
    if not bals:
        return None
    bals.sort(key=lambda b: (
        BALANCE_PREFERENCE.index(b["balance_type"])
        if b["balance_type"] in BALANCE_PREFERENCE else 99,
        "".join(chr(255 - ord(ch)) for ch in (b["reference_date"] or "")),
    ))
    return bals[0]


@app.get("/api/accounts")
def accounts() -> list[dict]:
    out = []
    for a in rows("SELECT account_key, name, iban, currency, aspsp_name, "
                  "aspsp_country FROM accounts ORDER BY aspsp_name, name"):
        a["balance"] = _chosen_balance(a["account_key"])
        out.append(a)
    return out


# --- write path: manual category override ---------------------------------

@app.patch("/api/transactions/{dedup_id}/category")
def set_category(dedup_id: str, category_id: int = Body(..., embed=True),
                 learn: bool = Body(True, embed=True)) -> dict:
    """Pin one transaction to a category (sticky, source='manual').

    When learn=True (default) also teach a merchant rule from it and propagate
    that category to non-manual look-alikes; `applied` reports how many.
    """
    conn = sqlite3.connect(db.DB_PATH)
    try:
        if not conn.execute("SELECT 1 FROM transactions WHERE dedup_id=?",
                            (dedup_id,)).fetchone():
            raise HTTPException(404, "transaction not found")
        if not conn.execute("SELECT 1 FROM categories WHERE id=?",
                            (category_id,)).fetchone():
            raise HTTPException(400, "unknown category")
        conn.execute("""
            INSERT INTO tx_category (dedup_id, category_id, source, updated_at)
            VALUES (?,?, 'manual', ?)
            ON CONFLICT(dedup_id) DO UPDATE SET
              category_id=excluded.category_id, source='manual',
              updated_at=excluded.updated_at""",
            (dedup_id, category_id, db.now_iso()))
        conn.commit()
        learned = (categorize.learn_and_apply(conn, dedup_id, category_id)
                   if learn else {"key": None, "applied": 0})
    finally:
        conn.close()
    return {"ok": True, "dedup_id": dedup_id, "category_id": category_id,
            "learned_key": learned["key"], "applied": learned["applied"]}


# --- static frontend ------------------------------------------------------

@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
