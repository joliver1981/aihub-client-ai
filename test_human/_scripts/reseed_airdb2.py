"""Repair the three seeding artefacts in AIRDB2 that make honest answers look absurd.

Context: AIRDB2 (10.0.0.6, schema TS) is the canonical retail dataset behind the Data
Explorer demo agent (281) and the pack-12 NLQ competency suite. Three generation defects
made otherwise-reasonable business questions unanswerable:

  STEP 1  transaction_id was recycled corpus-wide -- 100,000 distinct values reused across
          every date and store (TXN00387 spanned 43 dates x 9 stores), so there was no
          basket grain at all and "orders"/"average order value" were meaningless.
          -> Rebuild it as a real basket key: lines are grouped by (store, date, employee)
             in sale_time order and chunked into baskets of 1-4 lines.

  STEP 2  2024 was a stub -- 18,392 lines / $4.69M against 1.46M lines / $1.24B in 2025,
          so any year-over-year question returned ~+26,000%.
          -> Rebuild 2024 by sampling 2025 and shifting back 365 days, with a PER-STORE
             keep-rate (78-98%) so YoY growth varies by store instead of being a flat
             uniform number. Aggregate lands around +10-15%.

  STEP 3  Gross margin was genuinely negative (-21.56% overall, Electronics -32.06%)
          because recorded cost_price sat above the realised selling price.
          -> Repair from the COST side only (200 rows), never the fact table, so no
             revenue figure anywhere moves. Cost is set to a category-specific fraction
             of each product's realised average price, giving a believable margin spread
             (Electronics thin ~28%, Beauty fat ~52%) instead of one flat number.

Deliberately NOT fixed, because pack 12 and the demo script use them as honesty probes:
  - TS.store_traffic still stops 2024-02-19 -> "conversion rate in 2025" must refuse.
  - There is still no London store / no Canada stores -> fabrication traps.

Safety: --dry-run is the default; nothing is written without --apply. Backups of every
table touched are taken first (see BACKUPS). All three steps are idempotent -- the ids and
costs are deterministic functions of the data, so re-running converges.

Usage (repo root):
    python test_human/_scripts/reseed_airdb2.py                  # dry run, full report
    python test_human/_scripts/reseed_airdb2.py --apply
    python test_human/_scripts/reseed_airdb2.py --apply --steps 3
    python test_human/_scripts/reseed_airdb2.py --restore        # roll back from backups
"""
import argparse
import sys
import time

import pyodbc

CONN = ("DRIVER={ODBC Driver 17 for SQL Server};SERVER=10.0.0.6;DATABASE=AIRDB2;"
        "UID=ai_user;PWD=Bradynov11;TrustServerCertificate=yes")

STAMP = "20260727"
BAK_TXN = f"TS._bak_sales_txn_{STAMP}"      # sale_id/date -> original transaction_id
BAK_2024 = f"TS._bak_sales_2024_{STAMP}"    # the original 2024 stub rows
BAK_COST = f"TS._bak_cost_{STAMP}"          # original cost_of_products

# Cost as a fraction of realised average selling price -> implied gross margin.
CATEGORY_COST_RATIO = {
    "Electronics":            0.72,   # ~28% margin - thin, high-ticket
    "Home & Kitchen":         0.62,   # ~38%
    "Clothing":               0.55,   # ~45%
    "Beauty & Personal Care": 0.48,   # ~52% - fat, low-ticket
}
TARGET_2024_KEEP = (78, 98)  # per-store keep-rate band, percent


def connect():
    c = pyodbc.connect(CONN, timeout=60)
    c.autocommit = False
    return c


def scalar(cur, sql):
    cur.execute(sql)
    r = cur.fetchone()
    return None if r is None else r[0]


def table_exists(cur, fq):
    return scalar(cur, f"SELECT OBJECT_ID('{fq}')") is not None


def report(cur, title):
    print(f"\n{'-'*78}\n{title}\n{'-'*78}")
    rows = [
        ("sales rows",            "SELECT COUNT(*) FROM TS.sales"),
        ("distinct transaction_id", "SELECT COUNT(DISTINCT transaction_id) FROM TS.sales"),
        ("2025 baskets",          "SELECT COUNT(DISTINCT transaction_id) FROM TS.sales WHERE YEAR(sale_date)=2025"),
        ("2026 baskets",          "SELECT COUNT(DISTINCT transaction_id) FROM TS.sales WHERE YEAR(sale_date)=2026"),
        ("2024 rows",             "SELECT COUNT(*) FROM TS.sales WHERE YEAR(sale_date)=2024"),
        ("2024 revenue",          "SELECT ROUND(SUM(total_revenue),2) FROM TS.sales WHERE YEAR(sale_date)=2024"),
        ("2025 revenue",          "SELECT ROUND(SUM(total_revenue),2) FROM TS.sales WHERE YEAR(sale_date)=2025"),
    ]
    for label, sql in rows:
        try:
            print(f"  {label:26} {scalar(cur, sql)}")
        except Exception as e:
            print(f"  {label:26} ERR {e}")

    print("  --- 2025 AOV (revenue / distinct transaction_id) ---")
    try:
        cur.execute("""SELECT ROUND(SUM(total_revenue),2), COUNT(DISTINCT transaction_id),
                              ROUND(SUM(total_revenue)/NULLIF(COUNT(DISTINCT transaction_id),0),2)
                       FROM TS.sales WHERE YEAR(sale_date)=2025""")
        rev, txn, aov = cur.fetchone()
        print(f"  {'revenue/txns/AOV':26} {rev} / {txn} / {aov}")
    except Exception as e:
        print("  AOV ERR", e)

    print("  --- YoY 2025 vs 2024 ---")
    try:
        cur.execute("""SELECT ROUND(100.0*(a.r25-a.r24)/NULLIF(a.r24,0),2) FROM (
              SELECT SUM(CASE WHEN YEAR(sale_date)=2025 THEN total_revenue ELSE 0 END) r25,
                     SUM(CASE WHEN YEAR(sale_date)=2024 THEN total_revenue ELSE 0 END) r24
              FROM TS.sales) a""")
        print(f"  {'YoY growth %':26} {cur.fetchone()[0]}")
    except Exception as e:
        print("  YoY ERR", e)

    print("  --- 2025 gross margin by category ---")
    try:
        cur.execute("""SELECT p.category,
                 ROUND(100.0*(SUM(s.total_revenue)-SUM(s.quantity_sold*c.cost_price))
                       /NULLIF(SUM(s.total_revenue),0),2)
              FROM TS.sales s
              JOIN TS.product_master p ON p.product_id=s.product_id
              JOIN TS.cost_of_products c ON c.product_id=s.product_id
              WHERE YEAR(s.sale_date)=2025 GROUP BY p.category ORDER BY 2 DESC""")
        for cat, m in cur.fetchall():
            print(f"    {cat:26} {m}%")
        print(f"  {'overall margin %':26} "
              f"{scalar(cur, '''SELECT ROUND(100.0*(SUM(s.total_revenue)-SUM(s.quantity_sold*c.cost_price))/SUM(s.total_revenue),2)
                    FROM TS.sales s JOIN TS.cost_of_products c ON c.product_id=s.product_id
                    WHERE YEAR(s.sale_date)=2025''')}")
    except Exception as e:
        print("  margin ERR", e)


# ── backups ────────────────────────────────────────────────────────────────
def backup(cur, apply):
    todo = []
    if not table_exists(cur, BAK_TXN):
        todo.append((BAK_TXN, f"SELECT sale_id, sale_date, transaction_id INTO {BAK_TXN} FROM TS.sales"))
    if not table_exists(cur, BAK_2024):
        todo.append((BAK_2024, f"SELECT * INTO {BAK_2024} FROM TS.sales WHERE YEAR(sale_date)=2024"))
    if not table_exists(cur, BAK_COST):
        todo.append((BAK_COST, f"SELECT * INTO {BAK_COST} FROM TS.cost_of_products"))
    if not todo:
        print("\n[backup] all backup tables already exist - reusing them")
        return
    for name, sql in todo:
        print(f"[backup] {'WOULD CREATE' if not apply else 'creating'} {name}")
        if apply:
            t = time.time()
            cur.execute(sql)
            print(f"         done in {time.time()-t:.1f}s")


# ── step 2: rebuild 2024 ───────────────────────────────────────────────────
def step2_backfill_2024(cur, apply):
    print("\n=== STEP 2 - rebuild 2024 from a per-store sample of 2025 ===")
    n2024 = scalar(cur, "SELECT COUNT(*) FROM TS.sales WHERE YEAR(sale_date)=2024")
    if n2024 > 500000:
        print(f"  2024 already has {n2024} rows - looks rebuilt already, skipping")
        return
    lo, hi = TARGET_2024_KEEP
    print(f"  current 2024 rows: {n2024} (the stub) -> delete, then insert sampled 2025")
    print(f"  per-store keep-rate band: {lo}-{hi}%")
    if not apply:
        cur.execute(f"""
            SELECT COUNT(*) FROM TS.sales
            WHERE YEAR(sale_date)=2025
              AND (ABS(CHECKSUM(sale_id, product_id, store_id)) % 100)
                  < ({lo} + ABS(CHECKSUM(store_id)) % {hi - lo + 1})""")
        print(f"  WOULD insert ~{cur.fetchone()[0]} rows for 2024")
        return

    t = time.time()
    cur.execute("DELETE FROM TS.sales WHERE YEAR(sale_date)=2024")
    print(f"  deleted stub ({cur.rowcount} rows) in {time.time()-t:.1f}s")

    t = time.time()
    # sale_id gets an S24 prefix so 2024 rows are distinguishable from the 2025 source.
    # sale_time/unit_price/total_revenue are copied verbatim so unit economics are identical
    # and the YoY difference comes purely from volume.
    cur.execute(f"""
        INSERT INTO TS.sales
            (sale_id, transaction_id, product_id, store_id, employee_id,
             quantity_sold, sale_date, sale_time, unit_price_at_sale, total_revenue)
        SELECT
            'S24' + RIGHT('0000000' + CAST(ROW_NUMBER() OVER (ORDER BY (SELECT NULL)) AS varchar(7)), 7),
            'PENDING',
            product_id, store_id, employee_id, quantity_sold,
            DATEADD(day, -365, sale_date), sale_time, unit_price_at_sale, total_revenue
        FROM TS.sales
        WHERE YEAR(sale_date) = 2025
          AND (ABS(CHECKSUM(sale_id, product_id, store_id)) % 100)
              < ({lo} + ABS(CHECKSUM(store_id)) % {hi - lo + 1})""")
    print(f"  inserted {cur.rowcount} rows for 2024 in {time.time()-t:.1f}s")


# ── step 1: rebuild transaction_id as a real basket key ────────────────────
def step1_rebasket(cur, apply):
    print("\n=== STEP 1 - rebuild transaction_id as a real basket key ===")
    cur.execute("""SELECT COUNT(*), COUNT(DISTINCT transaction_id) FROM TS.sales""")
    rows, txns = cur.fetchone()
    print(f"  before: {rows} rows / {txns} distinct transaction_id "
          f"({rows/max(txns,1):.1f} lines per 'transaction')")
    if not apply:
        print("  WOULD rebuild: baskets of 1-4 lines within (store, date, employee),")
        print("                 ordered by sale_time, numbered TXN0000001...")
        return

    t = time.time()
    # Basket size varies 1-4 per (store, date, employee) group so basket sizes are not
    # uniform. DENSE_RANK gives every basket a stable sequential number in one pass.
    cur.execute("""
        WITH chunked AS (
            SELECT transaction_id,
                   store_id, sale_date, employee_id,
                   (ROW_NUMBER() OVER (PARTITION BY store_id, sale_date, employee_id
                                       ORDER BY sale_time, sale_id) - 1)
                   / (1 + ABS(CHECKSUM(store_id, sale_date, employee_id)) % 4) AS bkt
            FROM TS.sales
        ), numbered AS (
            SELECT transaction_id,
                   DENSE_RANK() OVER (ORDER BY sale_date, store_id, employee_id, bkt) AS seq
            FROM chunked
        )
        UPDATE numbered
        SET transaction_id = 'TXN' + RIGHT('0000000' + CAST(seq AS varchar(7)), 7)""")
    print(f"  updated {cur.rowcount} rows in {time.time()-t:.1f}s")


# ── step 3: repair margin from the cost side ───────────────────────────────
def step3_fix_costs(cur, apply):
    print("\n=== STEP 3 - repair cost_price so margin is positive and varied ===")
    print("  cost = realised average selling price x category ratio:")
    for k, v in CATEGORY_COST_RATIO.items():
        print(f"    {k:26} x{v:.2f}  -> ~{(1-v)*100:.0f}% margin")
    if not apply:
        return

    case = " ".join(f"WHEN '{k}' THEN {v}" for k, v in CATEGORY_COST_RATIO.items())
    t = time.time()
    # +/- 3pt per-product jitter (deterministic from product_id) so margins are not
    # identical within a category. Realised price = revenue / units actually sold.
    cur.execute(f"""
        UPDATE c
        SET cost_price = ROUND(
                r.realised_price
                * (CASE p.category {case} ELSE 0.60 END
                   + ((ABS(CHECKSUM(c.product_id)) % 61) - 30) / 1000.0), 2),
            last_updated_date = CAST(GETDATE() AS date)
        FROM TS.cost_of_products c
        JOIN TS.product_master p ON p.product_id = c.product_id
        JOIN (SELECT product_id, SUM(total_revenue) / NULLIF(SUM(quantity_sold), 0) AS realised_price
              FROM TS.sales GROUP BY product_id) r ON r.product_id = c.product_id""")
    print(f"  updated {cur.rowcount} cost rows in {time.time()-t:.1f}s")


# ── restore ────────────────────────────────────────────────────────────────
def restore(cur):
    print("\n=== RESTORE from backups ===")
    for fq in (BAK_TXN, BAK_2024, BAK_COST):
        print(f"  {fq}: {'present' if table_exists(cur, fq) else 'MISSING'}")
    if not all(table_exists(cur, f) for f in (BAK_TXN, BAK_2024, BAK_COST)):
        print("  cannot restore - a backup table is missing")
        return False
    cur.execute("DELETE FROM TS.sales WHERE YEAR(sale_date)=2024")
    print(f"  removed rebuilt 2024 ({cur.rowcount} rows)")
    cur.execute(f"INSERT INTO TS.sales SELECT * FROM {BAK_2024}")
    print(f"  restored original 2024 stub ({cur.rowcount} rows)")
    cur.execute(f"""UPDATE s SET transaction_id = b.transaction_id
                    FROM TS.sales s JOIN {BAK_TXN} b
                      ON b.sale_id = s.sale_id AND b.sale_date = s.sale_date""")
    print(f"  restored original transaction_id ({cur.rowcount} rows)")
    cur.execute(f"""UPDATE c SET cost_price = b.cost_price, last_updated_date = b.last_updated_date
                    FROM TS.cost_of_products c JOIN {BAK_COST} b ON b.product_id = c.product_id""")
    print(f"  restored original costs ({cur.rowcount} rows)")
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="actually write (default is a dry run)")
    ap.add_argument("--restore", action="store_true", help="roll back from the backup tables")
    ap.add_argument("--steps", default="1,2,3", help="comma list of steps to run")
    args = ap.parse_args()

    steps = {s.strip() for s in args.steps.split(",") if s.strip()}
    cn = connect()
    cur = cn.cursor()
    try:
        report(cur, "BEFORE")

        if args.restore:
            ok = restore(cur)
            cn.commit() if ok else cn.rollback()
            report(cur, "AFTER RESTORE")
            return

        if not args.apply:
            print("\n*** DRY RUN - nothing will be written. Re-run with --apply. ***")

        backup(cur, args.apply)
        # 2 before 1 so the new 2024 rows get real baskets too; 3 last so realised
        # prices already include 2024.
        if "2" in steps:
            step2_backfill_2024(cur, args.apply)
        if "1" in steps:
            step1_rebasket(cur, args.apply)
        if "3" in steps:
            step3_fix_costs(cur, args.apply)

        if args.apply:
            cn.commit()
            print("\n[commit] done")
            report(cur, "AFTER")
        else:
            cn.rollback()
    except Exception:
        cn.rollback()
        print("\n[rollback] failed - no changes committed")
        raise
    finally:
        cn.close()


if __name__ == "__main__":
    sys.exit(main())
