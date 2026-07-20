"""Quick look at what's in the DB. Run: ./.venv/bin/python stats.py"""
import db

c = db.connect()

print("accounts    :", c.execute("SELECT COUNT(*) FROM accounts").fetchone()[0])
print("balances    :", c.execute("SELECT COUNT(*) FROM balances").fetchone()[0])
print("transactions:", c.execute("SELECT COUNT(*) FROM transactions").fetchone()[0])
print("distinct dedup_ids:",
      c.execute("SELECT COUNT(DISTINCT dedup_id) FROM transactions").fetchone()[0])
print("sum signed  :",
      c.execute("SELECT ROUND(SUM(signed_amount), 2) FROM transactions").fetchone()[0])

print("\nby dedup strategy:")
for row in c.execute("""
    SELECT CASE
             WHEN dedup_id LIKE 'txid:%' THEN 'transaction_id'
             WHEN dedup_id LIKE 'ref:%'  THEN 'entry_reference'
             ELSE 'content-hash'
           END AS strat, COUNT(*)
    FROM transactions GROUP BY strat ORDER BY 2 DESC"""):
    print(f"  {row[0]:16} {row[1]}")

print("\n5 most recent:")
for row in c.execute("""
    SELECT booking_date, signed_amount, currency,
           COALESCE(creditor_name, debtor_name, ''), remittance_information
    FROM transactions ORDER BY booking_date DESC LIMIT 5"""):
    print("  ", row)
