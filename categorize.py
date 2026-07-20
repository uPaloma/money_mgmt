"""Layered, deterministic transaction categorization.

Priority ladder (first match wins), highest priority first:
  1. manual overrides in tx_category (source='manual') - never recomputed here
  2. merchant/remittance keyword rules
  3. MCC (ISO 18245) rules
  4. bank_transaction_code rules
  5. fallback -> Uncategorized

Rules live in the DB (category_rules) so they're user-extensible; the seeds
below populate them once on an empty DB. Re-running is idempotent and never
touches manual assignments.
"""
from __future__ import annotations

import re
import sqlite3

import db

# Minimum length of the alphanumeric core of a learned merchant key. Guards
# against a one-click manual classification teaching an over-broad rule (e.g.
# a 2-3 letter token that matches unrelated transactions).
MIN_KEY_LEN = 4

# --- seed data ------------------------------------------------------------
# (name, group, kind, color)
CATEGORIES = [
    ("Salary",            "Income",     "income",   "#2e7d32"),
    ("Refunds",           "Income",     "income",   "#66bb6a"),
    ("Interest",          "Income",     "income",   "#43a047"),
    ("Other Income",      "Income",     "income",   "#81c784"),

    ("Rent",              "Housing",    "expense",  "#5e35b1"),
    ("Utilities",         "Housing",    "expense",  "#7e57c2"),
    ("Internet & Phone",  "Housing",    "expense",  "#9575cd"),
    ("Insurance",         "Housing",    "expense",  "#b39ddb"),

    ("Groceries",         "Food",       "expense",  "#00897b"),
    ("Restaurants",       "Food",       "expense",  "#26a69a"),

    ("Public Transport",  "Transport",  "expense",  "#1e88e5"),
    ("Fuel",              "Transport",  "expense",  "#42a5f5"),
    ("Taxi & Rideshare",  "Transport",  "expense",  "#64b5f6"),

    ("Clothing",          "Shopping",   "expense",  "#d81b60"),
    ("Electronics",       "Shopping",   "expense",  "#ec407a"),
    ("General Shopping",  "Shopping",   "expense",  "#f06292"),

    ("Pharmacy",          "Health",     "expense",  "#e53935"),
    ("Medical",           "Health",     "expense",  "#ef5350"),
    ("Fitness",           "Health",     "expense",  "#e57373"),

    ("Subscriptions",     "Leisure",    "expense",  "#fb8c00"),
    ("Entertainment",     "Leisure",    "expense",  "#ffa726"),
    ("Travel",            "Leisure",    "expense",  "#ffb74d"),

    ("Bank Fees",         "Fees",       "expense",  "#8d6e63"),

    ("P2P Transfer",      "Transfers",  "transfer", "#607d8b"),
    ("Shared Expenses",   "Transfers",  "transfer", "#78909c"),
    ("Savings",           "Transfers",  "transfer", "#90a4ae"),

    ("Uncategorized",     "Other",      "expense",  "#9e9e9e"),
]

# (priority, match_type, pattern, category_name)
RULES = [
    # --- income (keyword) ---
    (10, "remittance", "gehalt",        "Salary"),
    (10, "remittance", "lohn",          "Salary"),
    (10, "remittance", "salary",        "Salary"),
    (10, "remittance", "lønn",          "Salary"),
    (10, "remittance", "payroll",       "Salary"),
    (12, "remittance", "refund",        "Refunds"),
    (12, "remittance", "erstattung",    "Refunds"),
    (12, "remittance", "zinsen",        "Interest"),
    (12, "remittance", "interest",      "Interest"),

    # --- housing / recurring (keyword; covers mock DKK + DE) ---
    (15, "text", "miete",       "Rent"),
    (15, "text", "husleje",     "Rent"),        # mock
    (15, "text", "rent",        "Rent"),
    (16, "text", "strom",       "Utilities"),
    (16, "text", "electric",    "Utilities"),
    (16, "remittance", "el",    "Utilities"),    # mock 'El'
    (16, "text", "wasser",      "Utilities"),
    (16, "text", "gas",         "Utilities"),
    (17, "text", "internet",    "Internet & Phone"),
    (17, "text", "telekom",     "Internet & Phone"),
    (17, "text", "vodafone",    "Internet & Phone"),
    (17, "remittance", "tv",     "Internet & Phone"),  # mock 'TV'
    (18, "text", "versicherung","Insurance"),
    (18, "text", "insurance",   "Insurance"),
    (18, "text", "forsikring",  "Insurance"),      # mock
    (18, "text", "a-kasse",     "Insurance"),       # mock (unemployment fund)
    (18, "text", "sygeforsikr", "Insurance"),       # mock

    # --- food / shops (keyword) ---
    (20, "text", "kvickly",   "Groceries"),          # mock
    (20, "text", "netto",     "Groceries"),
    (20, "text", "rewe",      "Groceries"),
    (20, "text", "spar",      "Groceries"),
    (20, "text", "billa",     "Groceries"),
    (20, "text", "hofer",     "Groceries"),
    (20, "text", "lidl",      "Groceries"),
    (20, "text", "aldi",      "Groceries"),
    (22, "text", "netflix",   "Subscriptions"),
    (22, "text", "spotify",   "Subscriptions"),
    (22, "text", "disney",    "Subscriptions"),

    # --- transport ---
    (24, "text", "dsb",       "Public Transport"),    # mock (Danish rail)
    (24, "text", "öbb",       "Public Transport"),
    (24, "text", "wiener linien", "Public Transport"),

    # --- transfers (keyword) ---
    (26, "text", "mobilepay", "P2P Transfer"),         # mock
    (26, "text", "vipps",     "P2P Transfer"),
    (26, "text", "weshare",   "Shared Expenses"),      # mock
    (26, "text", "paypal",    "P2P Transfer"),

    # --- MCC (card payments) ---
    (50, "mcc", "5411",        "Groceries"),
    (50, "mcc", "5412",        "Groceries"),
    (50, "mcc", "5811-5814",   "Restaurants"),
    (50, "mcc", "5541-5542",   "Fuel"),
    (50, "mcc", "4111",        "Public Transport"),
    (50, "mcc", "4121",        "Taxi & Rideshare"),
    (50, "mcc", "5912",        "Pharmacy"),
    (50, "mcc", "5651-5699",   "Clothing"),
    (50, "mcc", "5732-5734",   "Electronics"),
    (50, "mcc", "4829",        "P2P Transfer"),        # money transfer (mock)
    (50, "mcc", "7997",        "Fitness"),
    (50, "mcc", "4899",        "Internet & Phone"),

    # --- bank transaction code hints (lowest) ---
    (80, "btc", "fee",       "Bank Fees"),
    (80, "btc", "charge",    "Bank Fees"),
    (80, "btc", "interest",  "Interest"),
]


def seed(conn: sqlite3.Connection) -> None:
    if conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0] == 0:
        conn.executemany(
            "INSERT INTO categories (name, group_name, kind, color) VALUES (?,?,?,?)",
            CATEGORIES)
    if conn.execute("SELECT COUNT(*) FROM category_rules").fetchone()[0] == 0:
        ids = {n: i for i, n in conn.execute("SELECT id, name FROM categories")}
        conn.executemany(
            "INSERT INTO category_rules (priority, match_type, pattern, category_id) "
            "VALUES (?,?,?,?)",
            [(p, mt, pat, ids[cat]) for p, mt, pat, cat in RULES])
    conn.commit()


# --- engine ---------------------------------------------------------------

def _mcc_match(pattern: str, mcc: str | None) -> bool:
    if not mcc:
        return False
    if "-" in pattern:
        lo, hi = pattern.split("-", 1)
        return lo <= mcc <= hi
    return mcc == pattern


def _matches(match_type: str, pattern: str, t: dict) -> bool:
    if match_type == "mcc":
        return _mcc_match(pattern, t["merchant_category_code"])
    pat = pattern.lower()
    if match_type == "merchant":
        hay = f"{t['creditor_name'] or ''} {t['debtor_name'] or ''}"
    elif match_type == "remittance":
        hay = t["remittance_information"] or ""
    elif match_type == "btc":
        hay = t["bank_transaction_code"] or ""
    else:  # 'text' and 'learned' = merchant + remittance
        hay = f"{t['creditor_name'] or ''} {t['debtor_name'] or ''} {t['remittance_information'] or ''}"
    return pat in hay.lower()


# --- learning from a manual classification --------------------------------

# SQL expression matching what merchant_key() reads, for the propagation query.
_HAY_SQL = ("UPPER(COALESCE(t.creditor_name,'')||' '||COALESCE(t.debtor_name,'')"
            "||' '||COALESCE(t.remittance_information,''))")


def merchant_key(creditor_name: str | None, debtor_name: str | None,
                 remittance_information: str | None) -> str | None:
    """Derive a stable, upper-cased merchant signature from a transaction.

    Prefers a real counterparty name; falls back to the remittance text, where
    the mock ASPSP (and many real banks) pack the merchant into the first
    segment before a backslash/newline, e.g. "WWW ZALANDO DK\\ \\BERLIN\\" or
    "LOOKFANTASTIC.COM\\ \\0161813171". Returns None if nothing distinctive
    enough to learn from (too short after cleaning).
    """
    src = creditor_name or debtor_name or remittance_information or ""
    seg = re.split(r"[\\\n\r]", src, maxsplit=1)[0]
    seg = re.sub(r"\s{2,}", " ", seg).strip()
    seg = re.sub(r"[*#]?\s*\d{5,}\s*$", "", seg).strip()  # drop trailing ref numbers
    key = seg.upper()
    if len(re.sub(r"[^0-9A-Z]", "", key)) < MIN_KEY_LEN:
        return None
    return key


def learn_and_apply(conn: sqlite3.Connection, dedup_id: str,
                    category_id: int) -> dict:
    """Teach a rule from one manual classification and propagate it.

    Upserts a high-priority 'learned' rule keyed on the transaction's merchant
    signature, then assigns `category_id` to every OTHER transaction that
    matches it and is not manually classified. The taught transaction itself is
    left to the caller (it is already stored as source='manual'). Returns
    {key, applied, rule_id}; applied counts only transactions actually changed.
    """
    conn.row_factory = sqlite3.Row
    t = conn.execute(
        "SELECT creditor_name, debtor_name, remittance_information "
        "FROM transactions WHERE dedup_id=?", (dedup_id,)).fetchone()
    if not t:
        return {"key": None, "applied": 0, "rule_id": None}
    key = merchant_key(t["creditor_name"], t["debtor_name"],
                       t["remittance_information"])
    if not key:
        return {"key": None, "applied": 0, "rule_id": None}

    row = conn.execute("SELECT id FROM category_rules "
                       "WHERE match_type='learned' AND pattern=?", (key,)).fetchone()
    if row:
        rule_id = row["id"]
        conn.execute("UPDATE category_rules SET category_id=? WHERE id=?",
                     (category_id, rule_id))
    else:
        rule_id = conn.execute(
            "INSERT INTO category_rules (priority, match_type, pattern, category_id, note) "
            "VALUES (5, 'learned', ?, ?, 'learned')", (key, category_id)).lastrowid

    like = "%" + key.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    targets = conn.execute(f"""
        SELECT t.dedup_id FROM transactions t
        LEFT JOIN tx_category tc ON tc.dedup_id = t.dedup_id
        WHERE {_HAY_SQL} LIKE ? ESCAPE '\\'
          AND t.dedup_id != ?
          AND (tc.dedup_id IS NULL
               OR (tc.source != 'manual' AND tc.category_id != ?))
    """, (like, dedup_id, category_id)).fetchall()

    for r in targets:
        conn.execute(
            """INSERT INTO tx_category (dedup_id, category_id, source, matched_rule_id, updated_at)
               VALUES (?,?, 'auto', ?, ?)
               ON CONFLICT(dedup_id) DO UPDATE SET
                 category_id=excluded.category_id,
                 matched_rule_id=excluded.matched_rule_id,
                 updated_at=excluded.updated_at
               WHERE tx_category.source != 'manual'""",
            (r["dedup_id"], category_id, rule_id, db.now_iso()))
    conn.commit()
    return {"key": key, "applied": len(targets), "rule_id": rule_id}


def categorize(conn: sqlite3.Connection) -> dict:
    """Auto-categorize every non-manual transaction. Returns a distribution."""
    conn.row_factory = sqlite3.Row
    rules = conn.execute(
        "SELECT priority, match_type, pattern, category_id FROM category_rules "
        "ORDER BY priority, id").fetchall()
    uncategorized = conn.execute(
        "SELECT id FROM categories WHERE name='Uncategorized'").fetchone()[0]
    manual = {r[0] for r in conn.execute(
        "SELECT dedup_id FROM tx_category WHERE source='manual'")}

    txns = conn.execute(
        "SELECT dedup_id, creditor_name, debtor_name, remittance_information, "
        "merchant_category_code, bank_transaction_code FROM transactions").fetchall()

    changed = 0
    for t in txns:
        if t["dedup_id"] in manual:
            continue
        cat_id, rule_id = uncategorized, None
        for r in rules:
            if _matches(r["match_type"], r["pattern"], t):
                cat_id, rule_id = r["category_id"], r["priority"]
                break
        conn.execute(
            """INSERT INTO tx_category (dedup_id, category_id, source, matched_rule_id, updated_at)
               VALUES (?,?, 'auto', ?, ?)
               ON CONFLICT(dedup_id) DO UPDATE SET
                 category_id=excluded.category_id,
                 matched_rule_id=excluded.matched_rule_id,
                 updated_at=excluded.updated_at
               WHERE tx_category.source != 'manual'""",
            (t["dedup_id"], cat_id, rule_id, db.now_iso()))
        changed += 1
    conn.commit()

    dist = conn.execute("""
        SELECT c.name, c.group_name, COUNT(*) n
        FROM tx_category tc JOIN categories c ON c.id = tc.category_id
        GROUP BY c.id ORDER BY n DESC""").fetchall()
    return {"categorized": changed, "distribution": [dict(r) for r in dist]}


def run(conn: sqlite3.Connection) -> dict:
    seed(conn)
    return categorize(conn)


def main() -> None:
    conn = db.connect()
    result = run(conn)
    total = sum(r["n"] for r in result["distribution"])
    print(f"categorized {result['categorized']} transactions\n")
    for r in result["distribution"]:
        pct = 100 * r["n"] / total if total else 0
        print(f"  {r['group_name']:10} {r['name']:20} {r['n']:5}  ({pct:4.1f}%)")


if __name__ == "__main__":
    main()
