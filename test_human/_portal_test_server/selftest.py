"""Round-trip self-test for the Meridian 2FA portal fixture: bad password rejected,
bad code rejected, good password + live TOTP admits, documents list + download work.
Run with the server up:  python selftest.py"""
import io
import sys

import requests

sys.path.insert(0, ".")
from portal_server import PASSWORD, PORT, TOTP_SECRET, USERNAME, totp_now  # noqa: E402

BASE = f"http://127.0.0.1:{PORT}"


def main():
    ok = True
    s = requests.Session()

    r = s.post(f"{BASE}/login", data={"username": USERNAME, "password": "wrong"},
               timeout=10)
    good = "Invalid username or password" in r.text
    ok &= good
    print(f"[{'PASS' if good else 'FAIL'}] wrong password rejected")

    r = s.post(f"{BASE}/login", data={"username": USERNAME, "password": PASSWORD},
               allow_redirects=True, timeout=10)
    good = "/verify" in r.url and "Two-step verification" in r.text
    ok &= good
    print(f"[{'PASS' if good else 'FAIL'}] password accepted -> 2FA gate")

    r = s.post(f"{BASE}/verify", data={"otp_code": "000000"}, allow_redirects=True,
               timeout=10)
    good = "/verify" in r.url and "not valid" in r.text
    ok &= good
    print(f"[{'PASS' if good else 'FAIL'}] wrong TOTP rejected")

    r = s.post(f"{BASE}/verify", data={"otp_code": totp_now(TOTP_SECRET)},
               allow_redirects=True, timeout=10)
    good = "/documents" in r.url and "Documents" in r.text
    ok &= good
    print(f"[{'PASS' if good else 'FAIL'}] live TOTP admits -> documents page")

    fname = None
    for line in r.text.splitlines():
        if "/download/" in line:
            fname = line.split("/download/")[1].split('"')[0]
            break
    good = bool(fname)
    ok &= good
    print(f"[{'PASS' if good else 'FAIL'}] documents listed (first: {fname})")

    if fname:
        r = s.get(f"{BASE}/download/{fname}", timeout=10)
        good = r.status_code == 200 and len(r.content) > 100 \
            and "attachment" in (r.headers.get("Content-Disposition") or "")
        ok &= good
        print(f"[{'PASS' if good else 'FAIL'}] download works ({len(r.content)} bytes)")

    anon = requests.get(f"{BASE}/documents", allow_redirects=False, timeout=10)
    good = anon.status_code in (301, 302)
    ok &= good
    print(f"[{'PASS' if good else 'FAIL'}] anonymous /documents redirected to login")

    print("SELFTEST", "ALL PASS" if ok else "FAILURES ABOVE")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
