"""
send_email_batch.py -- put the email-channel invoices into a REAL agent mailbox.

The email channel is the one intake path that runs through the platform's own
inbound email (Agent Email / A6): a real message, really delivered, really
polled, with the attachment text really extracted. That is why it is small --
eight invoices, not sixty-four (see ap_book.CHANNELS for why).

    python send_email_batch.py                      # DRY RUN: show what would be sent
    python send_email_batch.py --status             # what the agent has actually received
    python send_email_batch.py --send               # actually send (asks first)
    python send_email_batch.py --send --yes         # actually send, no prompt

    python send_email_batch.py --to marcus-ap-agent.1@mail.everiai.ai

SENDING IS OPT-IN AND NEVER AUTOMATIC. `--send` transmits real mail from your
account to a real address; the default does nothing but print.

SMTP credentials come from the environment, never from this file:

    AP_SMTP_HOST      e.g. smtp.gmail.com
    AP_SMTP_PORT      default 587 (STARTTLS)
    AP_SMTP_USER      the account you send from
    AP_SMTP_PASS      an app password, not your login password
    AP_AGENT_ADDRESS  the agent mailbox to deliver to (or pass --to)

If you would rather not wire up SMTP at all, the .eml files in
_fixtures/channels/email/ are ordinary RFC822 -- open them in any mail client
and forward them to the agent address by hand. Eight is a small enough number.

Interpreter: C:\\Users\\james\\miniconda3\\envs\\aihub2.1\\python.exe
"""

from __future__ import annotations

import argparse
import email
import email.policy
import os
import smtplib
import sqlite3
import sys
from pathlib import Path

PACK = Path(__file__).resolve().parents[1]
EML_DIR = PACK / "_fixtures" / "channels" / "email"
LEDGER = Path(r"C:\src\aihub-client-ai-dev\data\agent\mywork.db")
MATCH = "CG-VINV"          # how a pack email is recognised in the ledger


def load_messages() -> list[tuple[Path, email.message.EmailMessage]]:
    if not EML_DIR.is_dir():
        sys.exit(f"No email fixtures at {EML_DIR}. Run make_fixtures.py first.")
    out = []
    for p in sorted(EML_DIR.glob("*.eml")):
        msg = email.message_from_bytes(p.read_bytes(), policy=email.policy.default)
        out.append((p, msg))
    if not out:
        sys.exit(f"No .eml files in {EML_DIR}. Run make_fixtures.py first.")
    return out


def describe(msgs) -> None:
    print(f"\n{len(msgs)} message(s) in {EML_DIR}\n")
    total_att = 0
    for p, m in msgs:
        atts = [a.get_filename() for a in m.iter_attachments()]
        total_att += len(atts)
        print(f"  {p.name}")
        print(f"      from    {m['From']}")
        print(f"      subject {m['Subject']}")
        print(f"      attach  {len(atts)}: {', '.join(a or '?' for a in atts)}")
    print(f"\n  {total_att} attachment(s) across {len(msgs)} message(s).")


def agent_addresses() -> list[dict]:
    if not LEDGER.exists():
        return []
    cn = sqlite3.connect(f"file:{LEDGER}?mode=ro", uri=True)
    cn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in cn.execute(
            "SELECT user_id, username, email_address, is_active, cooldown_minutes, "
            "auto_send FROM user_email_addresses WHERE is_active = 1")]
    finally:
        cn.close()


def status() -> None:
    addrs = agent_addresses()
    print("\nAgent mailboxes configured on this box:\n")
    if not addrs:
        print("  none — set one up from The Agent (setup_agent_email) first.\n")
        return
    for a in addrs:
        cd = a["cooldown_minutes"]
        warn = ""
        if cd is None:
            warn = "  <-- uses the env default (2 min); 8 emails ≈ 16 min to drain"
        elif int(cd) > 0:
            warn = f"  <-- {cd} min cooldown; 8 emails ≈ {int(cd) * 8} min to drain"
        print(f"  {a['email_address']}")
        print(f"      user {a['user_id']} ({a['username']}) · auto_send="
              f"{'ON' if a['auto_send'] else 'off'} · cooldown={cd}{warn}")

    if not LEDGER.exists():
        return
    cn = sqlite3.connect(f"file:{LEDGER}?mode=ro", uri=True)
    cn.row_factory = sqlite3.Row
    try:
        rows = [dict(r) for r in cn.execute(
            "SELECT address, sender, subject, outcome, processed_at "
            "FROM processed_emails WHERE subject LIKE ? OR subject LIKE ? "
            "ORDER BY processed_at DESC LIMIT 25",
            (f"%{MATCH}%", "%invoices attached%"))]
    finally:
        cn.close()
    print(f"\nPack emails the agent has processed: {len(rows)}\n")
    for r in rows:
        print(f"  {r['processed_at'][:19]}  {r['outcome']:<16} {str(r['subject'])[:52]}")
    if not rows:
        print("  none yet — nothing from this pack has reached the mailbox.")
    print()


def send(msgs, to_addr: str, assume_yes: bool) -> None:
    host = os.getenv("AP_SMTP_HOST")
    user = os.getenv("AP_SMTP_USER")
    pw = os.getenv("AP_SMTP_PASS")
    port = int(os.getenv("AP_SMTP_PORT", "587"))
    missing = [n for n, v in (("AP_SMTP_HOST", host), ("AP_SMTP_USER", user),
                              ("AP_SMTP_PASS", pw)) if not v]
    if missing:
        sys.exit(f"Missing environment variable(s): {', '.join(missing)}.\n"
                 f"Set them, or forward the .eml files by hand — see the header.")
    if not to_addr:
        sys.exit("No destination. Pass --to, or set AP_AGENT_ADDRESS.")

    print(f"\nAbout to send {len(msgs)} real email(s)")
    print(f"  from  {user}  via {host}:{port}")
    print(f"  to    {to_addr}")
    if not assume_yes:
        if input("\nType 'send' to confirm: ").strip().lower() != "send":
            print("Cancelled. Nothing was sent.")
            return

    with smtplib.SMTP(host, port, timeout=30) as s:
        s.starttls()
        s.login(user, pw)
        for i, (p, m) in enumerate(msgs, start=1):
            # Keep the vendor identity in the visible From so the invoice still
            # looks like it came from the supplier, but send from the real
            # account -- most providers reject a forged envelope sender.
            del m["To"]
            m["To"] = to_addr
            if m["Reply-To"] is None:
                m["Reply-To"] = m["From"]
            del m["From"]
            m["From"] = user
            s.send_message(m, from_addr=user, to_addrs=[to_addr])
            print(f"  [{i}/{len(msgs)}] sent {p.name} -> {to_addr}")
    print(f"\nSent. The poller runs every ~60s; watch with --status, or in the "
          f"console's Vendor email tile.\n")


def main():
    ap = argparse.ArgumentParser(description="Send the AP email-channel batch.")
    ap.add_argument("--send", action="store_true", help="actually transmit")
    ap.add_argument("--yes", action="store_true", help="skip the confirmation")
    ap.add_argument("--to", default=os.getenv("AP_AGENT_ADDRESS", ""))
    ap.add_argument("--status", action="store_true")
    a = ap.parse_args()

    if a.status:
        status()
        return

    msgs = load_messages()
    describe(msgs)
    if a.send:
        send(msgs, a.to, a.yes)
    else:
        print("\nDRY RUN — nothing sent. Re-run with --send to transmit, "
              "or forward the .eml files by hand.\n")
        status()


if __name__ == "__main__":
    main()
