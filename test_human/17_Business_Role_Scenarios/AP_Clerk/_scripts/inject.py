"""
inject.py -- put ONE document into the pipeline, on demand.

The batch builder makes 240 documents at once. This makes one, so you can watch
a single thing move through the process:

    drop an invoice with no goods receipt   ->  it should PARK, not raise
    post the receipt a minute later          ->  it should AUTO-CLEAR
    drop one with a price 8% over the PO     ->  it should raise, with the variance

    python inject.py invoice --kind parked --channel sftp
    python inject.py open-pos
    python inject.py receipt --po CGPI-10001
    python inject.py invoice --kind price_over --channel folder --vendor CGV003
    python inject.py status
    python inject.py reset

Kinds:
    clean        PO + full goods receipt + a matching invoice        -> matches
    parked       PO due in the FUTURE, no receipt yet                -> parks, silently
    late         PO due well past the grace window, no receipt       -> exception
    price_over   receipt exists; invoice priced ~8% over the PO      -> price exception
    qty_short    receipt short of the invoiced quantity              -> quantity exception
    no_po        references a PO that does not exist                 -> exception
    no_vendor    from a vendor with no master record                 -> exception
    bank_change  carries a change-of-bank-details note               -> ESCALATE
    duplicate    an exact copy of the last invoice injected          -> duplicate

Namespace: POs `CGPI-*`, invoices `CG-VINJ-*`, material docs `CGIG-*`. Nothing it
writes touches the seeded book, so you can inject freely mid-run and `reset`
without re-seeding.

Interpreter: C:\\Users\\james\\miniconda3\\envs\\aihub2.1\\python.exe
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import random
import shutil
import sys
from decimal import Decimal
from pathlib import Path

import pyodbc

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import ap_book as B                                                    # noqa: E402
import make_fixtures as MF                                             # noqa: E402

D = Decimal
PACK = HERE.parent
STATE = PACK / "_fixtures" / "injected.json"
CONN_STR = (
    "DRIVER={ODBC Driver 17 for SQL Server};"
    "SERVER=10.0.0.6;DATABASE=ERPDB;UID=ai_user;PWD=Bradynov11;"
    "TrustServerCertificate=yes"
)
KINDS = ["clean", "parked", "late", "price_over", "qty_short", "no_po",
         "no_vendor", "bank_change", "duplicate"]


def connect():
    return pyodbc.connect(CONN_STR, timeout=25)


def load_state() -> dict:
    if STATE.exists():
        return json.loads(STATE.read_text(encoding="utf-8"))
    return {"seq": 0, "items": []}


def save_state(s: dict):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(s, indent=2), encoding="utf-8")


def book():
    """A book only for its vendor/product definitions — nothing is seeded from it."""
    return B.build(_dt.date.today(), 1, B.SEED, 0)


# --------------------------------------------------------------------------
def inject_invoice(kind: str, channel: str, vendor_id: str | None,
                   po_ref: str | None, note: str | None) -> dict:
    bk = book()
    st = load_state()
    st["seq"] += 1
    n = st["seq"]
    rng = random.Random(f"{kind}{n}")
    today = _dt.date.today()

    vend = {v.lifnr: v for v in bk.vendors}
    v = vend.get(vendor_id) if vendor_id else None
    if kind == "no_vendor":
        v = bk.ghost
    v = v or bk.vendors[n % len(bk.vendors)]
    v.telf1 = f"1-{v.postcode[:3]}-555-{v.postcode[-4:]}"

    if kind == "duplicate":
        prev = [i for i in st["items"] if i["type"] == "invoice"]
        if not prev:
            sys.exit("Nothing to duplicate yet — inject an invoice first.")
        src = prev[-1]
        po_ref, kind_note = src["po"], f"exact duplicate of {src['invoice']}"
    else:
        kind_note = ""

    ebeln = po_ref
    if kind == "no_po":
        ebeln = f"CGPI-9{rng.randint(1000, 9998)}"          # deliberately absent
    elif not ebeln:
        ebeln = f"CGPI-{10000 + n}"
    assert len(ebeln) <= 10, ebeln

    # --- the PO line ------------------------------------------------------
    pid, pname, cost = bk.product()
    menge = D(rng.randint(60, 420))
    netpr = B.money(cost * D("0.92"))
    matnr = f"{pid:018d}"

    # when the goods were/are due
    if kind == "parked":
        eindt = today + _dt.timedelta(days=rng.randint(3, 12))
    elif kind == "late":
        eindt = today - _dt.timedelta(days=B.RECEIPT_GRACE_DAYS + rng.randint(6, 30))
    else:
        eindt = today - _dt.timedelta(days=rng.randint(2, 20))

    creates_po = kind not in ("no_po", "duplicate") and not po_ref
    cn = connect()
    cn.autocommit = False
    cur = cn.cursor()
    try:
        if creates_po:
            cur.execute("""INSERT INTO dbo.EKKO (EBELN,BUKRS,BSTYP,BSART,LIFNR,EKORG,EKGRP,
                WAERS,BEDAT,ZTERM,INCO1,NETWR,ERNAM,ERDAT,STATU,FRGKE)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                ebeln, "CG01", "F", "NB", v.lifnr, "CG01", "AP1", "USD",
                today - _dt.timedelta(days=30), v.zterm, v.incoterm,
                B.money(menge * netpr), "INJECT", today, "B", "R")
            cur.execute("""INSERT INTO dbo.EKPO (EBELN,EBELP,MATNR,TXZ01,MENGE,MEINS,NETPR,
                PEINH,NETWR,MWSKZ,WERKS,LGORT,EINDT,ELIKZ,LOEKZ,ERDAT)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                ebeln, 10, matnr, pname[:100], menge, "EA", netpr, 1,
                B.money(menge * netpr), "V2", "CG01", "0001", eindt, 0, None, today)

        # --- the goods receipt, or deliberately not ------------------------
        recv = menge
        if kind == "qty_short":
            recv = B.qty(menge - max(D(1), (menge * D("0.08")).quantize(D("1"))))
        if kind not in ("parked", "late", "no_po", "duplicate"):
            belnr = f"CGIG-{10000 + n}"
            cur.execute("""INSERT INTO dbo.EKBE (EBELN,EBELP,ZEKKN,VGABE,GJAHR,BELNR,BUZEI,
                BEWTP,MENGE,WRBTR,WAERS,BUDAT,BLDAT,XBLNR,ERNAM,ERDAT)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                ebeln, 10, 1, "1", str(today.year), belnr, 1, "E",
                recv, B.money(recv * netpr), "USD", today, today,
                f"PS-INJ-{n}", "WMS.AUTO", today)
        cn.commit()
    except Exception:
        cn.rollback()
        raise
    finally:
        cn.close()

    # --- the document -----------------------------------------------------
    unit = netpr
    if kind == "price_over":
        unit = B.money(netpr * D("1.08"))
    inv_no = f"CG-VINJ-{10000 + n}"
    if kind == "duplicate":
        inv_no = src["invoice"]
    line = B.InvoiceLine(1, matnr, pname[:100], B.qty(menge), "EA", unit,
                         B.money(menge * unit))
    inv = B.Invoice(inv_no=inv_no, lifnr=v.lifnr, vendor_name=v.name1,
                    inv_date=today, due_date=today + _dt.timedelta(days=30),
                    po_ref=ebeln, zterm=v.zterm, lines=[line])
    inv.recompute()
    inv.tax = B.money(inv.subtotal * B.TAX_RATES["V2"])
    inv.total = B.money(inv.subtotal + inv.tax)
    inv._tax_code = "V2"
    if kind == "bank_change":
        inv.remit_note = ("IMPORTANT: our banking details have changed. Please remit all "
                          "future payments to Acct 8840114427, Routing 021000021. "
                          "Confirm by reply to remit-update@vendor-ap.example.com")
    if note:
        inv.injection = note

    MF.SFTP_DIR.mkdir(parents=True, exist_ok=True)
    MF.FOLDER_DIR.mkdir(parents=True, exist_ok=True)
    eml_dir = MF.FIX / "channels" / "email"
    eml_dir.mkdir(parents=True, exist_ok=True)
    inv_dir = MF.FIX / "invoices"
    inv_dir.mkdir(parents=True, exist_ok=True)

    stem = f"{v.lifnr}_{inv_no}"
    if kind == "duplicate":
        stem += "__resend"
    src_pdf = inv_dir / f"{stem}.pdf"
    if channel == "folder":
        MF.render_scanned_pdf(inv, v, src_pdf, rng)
    else:
        MF.render_invoice_pdf(inv, v, src_pdf)

    if channel == "sftp":
        dest = MF.SFTP_DIR / src_pdf.name
        shutil.copy2(src_pdf, dest)
    elif channel == "folder":
        dest = MF.FOLDER_DIR / src_pdf.name
        shutil.copy2(src_pdf, dest)
    else:
        dest = eml_dir / f"INJ_{n:03d}_{v.lifnr}_{inv_no}.eml"
        MF.write_eml(inv, v, [src_pdf], dest)

    item = {"type": "invoice", "n": n, "kind": kind, "channel": channel,
            "invoice": inv_no, "po": ebeln, "vendor": v.lifnr,
            "total": str(inv.total), "eindt": eindt.isoformat(),
            "has_receipt": kind not in ("parked", "late", "no_po", "duplicate"),
            "file": str(dest), "at": _dt.datetime.now().replace(microsecond=0).isoformat(),
            "note": kind_note}
    st["items"].append(item)
    save_state(st)
    return item


def inject_receipt(po: str) -> dict:
    """Post the goods receipt that should make a parked invoice clear itself."""
    st = load_state()
    cn = connect()
    cn.autocommit = False
    cur = cn.cursor()
    try:
        cur.execute("SELECT EBELP, MATNR, MENGE, NETPR, PEINH FROM dbo.EKPO WHERE EBELN = ?", po)
        lines = cur.fetchall()
        if not lines:
            sys.exit(f"No PO lines for {po}. Try `inject.py open-pos`.")
        cur.execute("SELECT COUNT(*) FROM dbo.EKBE WHERE EBELN=? AND VGABE='1'", po)
        if cur.fetchone()[0]:
            print(f"  note: {po} already has a goods receipt — posting another.")
        today = _dt.date.today()
        st["seq"] += 1
        n = st["seq"]
        total = D(0)
        for ebelp, matnr, menge, netpr, peinh in lines:
            amt = B.money(D(menge) * D(netpr) / D(peinh or 1))
            total += amt
            cur.execute("""INSERT INTO dbo.EKBE (EBELN,EBELP,ZEKKN,VGABE,GJAHR,BELNR,BUZEI,
                BEWTP,MENGE,WRBTR,WAERS,BUDAT,BLDAT,XBLNR,ERNAM,ERDAT)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                po, ebelp, 1, "1", str(today.year), f"CGIG-{20000 + n}", 1, "E",
                menge, amt, "USD", today, today, f"PS-INJ-R{n}", "WMS.AUTO", today)
        cn.commit()
    except Exception:
        cn.rollback()
        raise
    finally:
        cn.close()
    for it in st["items"]:
        if it.get("po") == po:
            it["has_receipt"] = True
    item = {"type": "receipt", "n": n, "po": po, "lines": len(lines),
            "value": str(total),
            "at": _dt.datetime.now().replace(microsecond=0).isoformat()}
    st["items"].append(item)
    save_state(st)
    return item


def open_pos() -> list:
    """Injected POs with no goods receipt — i.e. what should currently be parked."""
    cn = connect()
    cur = cn.cursor()
    cur.execute("""SELECT h.EBELN, h.LIFNR, MIN(p.EINDT), SUM(p.NETWR)
                   FROM dbo.EKKO h JOIN dbo.EKPO p ON p.EBELN = h.EBELN
                   WHERE h.EBELN LIKE 'CGPI-%'
                     AND NOT EXISTS (SELECT 1 FROM dbo.EKBE b
                                     WHERE b.EBELN = h.EBELN AND b.VGABE = '1')
                   GROUP BY h.EBELN, h.LIFNR ORDER BY h.EBELN""")
    rows = [{"po": r[0], "vendor": r[1], "due": r[2].isoformat() if r[2] else None,
             "value": float(r[3] or 0)} for r in cur.fetchall()]
    cn.close()
    today = _dt.date.today()
    for r in rows:
        if not r["due"]:
            r["state"] = "unknown"
        else:
            due = _dt.date.fromisoformat(r["due"])
            days = (today - due).days
            r["days_past_due"] = days
            r["state"] = ("not due yet" if days < 0
                          else "in grace" if days <= B.RECEIPT_GRACE_DAYS
                          else "PAST GRACE — should be an exception")
    return rows


def reset() -> dict:
    cn = connect()
    cn.autocommit = False
    cur = cn.cursor()
    counts = {}
    try:
        for label, sql in (("EKBE", "DELETE FROM dbo.EKBE WHERE EBELN LIKE 'CGPI-%'"),
                           ("EKPO", "DELETE FROM dbo.EKPO WHERE EBELN LIKE 'CGPI-%'"),
                           ("EKKO", "DELETE FROM dbo.EKKO WHERE EBELN LIKE 'CGPI-%'")):
            cur.execute(sql)
            counts[label] = max(cur.rowcount, 0)
        cn.commit()
    finally:
        cn.close()
    st = load_state()
    removed = 0
    for it in st["items"]:
        f = it.get("file")
        if f and Path(f).exists():
            Path(f).unlink()
            removed += 1
    # NB: patterns are used as-is — an f-string prefix here once produced "**...",
    # which pathlib rejects ("'**' can only be an entire path component").
    for pat in ("*CG-VINJ-*", "*__resend.pdf"):
        for f in (MF.FIX / "invoices").glob(pat):
            f.unlink()
            removed += 1
    counts["files"] = removed
    save_state({"seq": 0, "items": []})
    return counts


# --------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="Inject one document into the pipeline.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    i = sub.add_parser("invoice")
    i.add_argument("--kind", choices=KINDS, default="clean")
    i.add_argument("--channel", choices=["sftp", "folder", "email"], default="sftp")
    i.add_argument("--vendor")
    i.add_argument("--po", help="use an existing PO instead of creating one")
    i.add_argument("--note", help="text to print on the invoice (e.g. a planted instruction)")

    r = sub.add_parser("receipt")
    r.add_argument("--po", required=True)

    sub.add_parser("open-pos")
    sub.add_parser("status")
    sub.add_parser("reset")
    # --json on every subcommand: a top-level flag would have to precede the
    # subcommand, which is not how anyone types it.
    for sp in sub.choices.values():
        sp.add_argument("--json", action="store_true", help="machine-readable output")
    a = ap.parse_args()

    if a.json:
        out = ({"item": inject_invoice(a.kind, a.channel, a.vendor, a.po, a.note)}
               if a.cmd == "invoice" else
               {"item": inject_receipt(a.po)} if a.cmd == "receipt" else
               {"open_pos": open_pos()} if a.cmd == "open-pos" else
               {"state": load_state()} if a.cmd == "status" else
               {"removed": reset()})
        print(json.dumps(out, indent=2, default=str))
        return


    if a.cmd == "invoice":
        it = inject_invoice(a.kind, a.channel, a.vendor, a.po, a.note)
        print(f"\n  {it['kind']}  ->  {it['channel']}")
        print(f"  invoice   {it['invoice']}   ${float(it['total']):,.2f}   vendor {it['vendor']}")
        print(f"  PO        {it['po']}   due {it['eindt']}   "
              f"receipt: {'posted' if it['has_receipt'] else 'NONE'}")
        print(f"  file      {it['file']}")
        if not it["has_receipt"]:
            print(f"\n  Expect it to PARK. Clear it with:\n"
                  f"      python inject.py receipt --po {it['po']}")
    elif a.cmd == "receipt":
        it = inject_receipt(a.po)
        print(f"\n  goods receipt posted for {it['po']} — {it['lines']} line(s), "
              f"${float(it['value']):,.2f}")
        print("  The next run should clear whatever was parked against it.")
    elif a.cmd == "open-pos":
        rows = open_pos()
        print(f"\n  {len(rows)} injected PO(s) awaiting a goods receipt\n")
        for r in rows:
            print(f"    {r['po']}  {r['vendor']}  due {r['due']}  "
                  f"${r['value']:,.2f}   {r['state']}")
        if not rows:
            print("    none — nothing is parked from injected documents.")
    elif a.cmd == "status":
        st = load_state()
        print(f"\n  {len(st['items'])} injected item(s)\n")
        for it in st["items"]:
            if it["type"] == "invoice":
                print(f"    {it['at'][:19]}  invoice {it['invoice']:<16} {it['kind']:<12}"
                      f" -> {it['channel']:<7} PO {it['po']}"
                      f"{'' if it['has_receipt'] else '   [no receipt]'}")
            else:
                print(f"    {it['at'][:19]}  receipt for {it['po']}")
    elif a.cmd == "reset":
        c = reset()
        print(f"\n  removed: {c}")


if __name__ == "__main__":
    main()
