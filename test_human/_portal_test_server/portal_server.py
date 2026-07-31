"""Meridian Supply Co. — local 2FA vendor-portal fixture for Portal Workflow demos/tests.

A self-contained, localhost-only web portal with the three things real vendor portals have:
a username/password login, a TOTP 2FA gate (RFC 6238, stdlib implementation — any
authenticator app or pyotp produces matching codes from the same seed), and a
credential-gated Documents page with downloadable files (see make_fixtures.py).

Run:      python portal_server.py            (env aihub2.1; Flask only, no other deps)
Login:    tc_purchasing / Demo2026!          (override: PORTAL_TEST_USER / PORTAL_TEST_PASS)
TOTP:     base32 seed JBSWY3DPEHPK3PXP       (override: PORTAL_TEST_TOTP_SECRET)
Port:     3000                               (override: PORTAL_TEST_PORT)
Extras:   /authenticator — a "demo authenticator app" page showing the live rotating code
          (open it beside the login page on stage to prove the 2FA is real).

These are throwaway LOCAL demo credentials for a fixture that serves generated files —
they gate nothing real and are committed on purpose so the demo Just Works.
"""
import base64
import hashlib
import hmac
import os
import struct
import time

from flask import (Flask, Response, jsonify, redirect, render_template_string,
                   request, send_from_directory, session, url_for)

HERE = os.path.dirname(os.path.abspath(__file__))
FILES_DIR = os.path.join(HERE, "files")

PORT = int(os.getenv("PORTAL_TEST_PORT", "3000"))
USERNAME = os.getenv("PORTAL_TEST_USER", "tc_purchasing")
PASSWORD = os.getenv("PORTAL_TEST_PASS", "Demo2026!")
TOTP_SECRET = os.getenv("PORTAL_TEST_TOTP_SECRET", "JBSWY3DPEHPK3PXP")

app = Flask(__name__)
app.secret_key = "meridian-demo-portal-fixture"  # local demo fixture; not a real secret


# ---------------------------------------------------------------- TOTP (RFC 6238, stdlib)
def totp_now(secret: str, at: float = None, step: int = 30, digits: int = 6) -> str:
    key = base64.b32decode(secret.upper() + "=" * (-len(secret) % 8))
    counter = int((at if at is not None else time.time()) // step)
    mac = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = mac[-1] & 0x0F
    code = (struct.unpack(">I", mac[offset:offset + 4])[0] & 0x7FFFFFFF) % (10 ** digits)
    return str(code).zfill(digits)


def totp_valid(secret: str, code: str, window: int = 1) -> bool:
    code = "".join(c for c in (code or "") if c.isdigit())
    if not code:
        return False
    now = time.time()
    return any(hmac.compare_digest(totp_now(secret, now + i * 30), code)
               for i in range(-window, window + 1))


# ---------------------------------------------------------------- shared page chrome
PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>{{ title }} · Meridian Supply Co.</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
 * { box-sizing: border-box; margin: 0; }
 body { font-family: 'Segoe UI', system-ui, sans-serif; background: #eef1f4; color: #1d2b36;
        min-height: 100vh; display: flex; flex-direction: column; }
 header { background: #123a5c; color: #fff; padding: 14px 28px; display: flex;
          align-items: center; gap: 14px; }
 .logo { width: 34px; height: 34px; border-radius: 8px; background: #2e86ab; display: flex;
         align-items: center; justify-content: center; font-weight: 700; font-size: 18px; }
 header .brand { font-size: 18px; font-weight: 600; letter-spacing: .3px; }
 header .sub { font-size: 12.5px; color: #b9cbda; margin-top: 1px; }
 header .right { margin-left: auto; font-size: 13px; color: #b9cbda; }
 header .right a { color: #cfe3f2; text-decoration: none; margin-left: 16px; }
 main { flex: 1; display: flex; align-items: flex-start; justify-content: center;
        padding: 46px 16px; }
 .card { background: #fff; border: 1px solid #d7dee5; border-radius: 12px;
         box-shadow: 0 8px 28px rgba(18, 58, 92, .08); padding: 34px 36px; width: 430px; }
 .wide { width: 860px; }
 h1 { font-size: 21px; margin-bottom: 6px; color: #123a5c; }
 .hint { font-size: 13.5px; color: #5c7183; margin-bottom: 22px; }
 label { display: block; font-size: 13px; font-weight: 600; color: #35516a; margin: 14px 0 6px; }
 input[type=text], input[type=password] { width: 100%; padding: 11px 12px; font-size: 15px;
   border: 1px solid #b9c6d1; border-radius: 8px; }
 input:focus { outline: 2px solid #2e86ab; border-color: #2e86ab; }
 button { margin-top: 22px; width: 100%; padding: 12px; font-size: 15px; font-weight: 600;
   color: #fff; background: #2e86ab; border: 0; border-radius: 8px; cursor: pointer; }
 button:hover { background: #256e8d; }
 .error { background: #fdecec; border: 1px solid #e5a3a3; color: #8c2f2f; padding: 10px 12px;
   border-radius: 8px; font-size: 13.5px; margin-bottom: 6px; }
 table { width: 100%; border-collapse: collapse; margin-top: 18px; }
 th { text-align: left; font-size: 12.5px; text-transform: uppercase; letter-spacing: .4px;
   color: #5c7183; border-bottom: 2px solid #d7dee5; padding: 8px 10px; }
 td { padding: 12px 10px; border-bottom: 1px solid #e6ebf0; font-size: 14.5px; }
 .tag { display: inline-block; background: #e7f4ec; color: #1e7a44; font-size: 11.5px;
   font-weight: 700; padding: 2px 9px; border-radius: 20px; margin-left: 8px; }
 a.dl { display: inline-block; padding: 8px 16px; background: #2e86ab; color: #fff;
   border-radius: 7px; text-decoration: none; font-size: 13.5px; font-weight: 600; }
 a.dl:hover { background: #256e8d; }
 footer { text-align: center; font-size: 12px; color: #8296a5; padding: 18px; }
 .demo { color: #b06f2f; font-weight: 600; }
 .code-big { font-size: 64px; font-weight: 700; letter-spacing: 10px; color: #123a5c;
   text-align: center; margin: 18px 0 8px; font-variant-numeric: tabular-nums; }
 .count { text-align: center; font-size: 14px; color: #5c7183; }
 .bar { height: 8px; background: #e6ebf0; border-radius: 6px; margin-top: 14px; overflow: hidden; }
 .bar div { height: 100%; background: #2e86ab; transition: width .9s linear; }
</style></head>
<body>
<header>
  <div class="logo">M</div>
  <div><div class="brand">Meridian Supply Co.</div>
       <div class="sub">Supplier &amp; Customer Portal</div></div>
  <div class="right">{{ nav|safe }}</div>
</header>
<main>{{ body|safe }}</main>
<footer>© 2026 Meridian Supply Co. · <span class="demo">LOCAL DEMO ENVIRONMENT</span> ·
 fixture served from test_human\\_portal_test_server</footer>
</body></html>"""


def page(title, body, nav=""):
    return render_template_string(PAGE, title=title, body=body, nav=nav)


# ---------------------------------------------------------------- routes
@app.route("/")
def home():
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    error = ""
    if request.method == "POST":
        if (request.form.get("username", "").strip() == USERNAME
                and request.form.get("password", "") == PASSWORD):
            session.clear()
            session["await_2fa"] = True
            return redirect(url_for("verify"))
        error = '<div class="error">Invalid username or password.</div>'
    body = f"""
    <form class="card" method="post" action="/login">
      <h1>Sign in to your account</h1>
      <div class="hint">Customer and supplier access. Two-step verification is required.</div>
      {error}
      <label for="username">Username</label>
      <input type="text" id="username" name="username" autocomplete="username"
             placeholder="Username" required>
      <label for="password">Password</label>
      <input type="password" id="password" name="password" autocomplete="current-password"
             placeholder="Password" required>
      <button type="submit" id="signin">Sign in</button>
    </form>"""
    return page("Sign in", body)


@app.route("/verify", methods=["GET", "POST"])
def verify():
    if not session.get("await_2fa"):
        return redirect(url_for("login"))
    error = ""
    if request.method == "POST":
        if totp_valid(TOTP_SECRET, request.form.get("otp_code", "")):
            session.pop("await_2fa", None)
            session["authed"] = True
            return redirect(url_for("documents"))
        error = '<div class="error">That verification code is not valid. Codes rotate every 30 seconds — check your authenticator app and try again.</div>'
    body = f"""
    <form class="card" method="post" action="/verify">
      <h1>Two-step verification</h1>
      <div class="hint">Enter the 6-digit one-time code from your authenticator app to
      finish signing in.</div>
      {error}
      <label for="otp-code">Verification code</label>
      <input type="text" id="otp-code" name="otp_code" inputmode="numeric" maxlength="6"
             autocomplete="one-time-code" placeholder="6-digit verification code" required
             autofocus>
      <button type="submit" id="verify-btn">Verify code</button>
    </form>"""
    return page("Two-step verification", body)


def _files():
    """Invoices first (newest first), then everything else — row 1 is the latest invoice."""
    if not os.path.isdir(FILES_DIR):
        return []
    names = [f for f in os.listdir(FILES_DIR)
             if os.path.isfile(os.path.join(FILES_DIR, f))]
    invoices = sorted((f for f in names if f.lower().startswith("invoice")), reverse=True)
    rest = sorted(f for f in names if not f.lower().startswith("invoice"))
    return [(f, os.path.getsize(os.path.join(FILES_DIR, f))) for f in invoices + rest]


@app.route("/documents")
def documents():
    if not session.get("authed"):
        return redirect(url_for("login"))
    rows = []
    for i, (f, size) in enumerate(_files()):
        latest = '<span class="tag">Latest</span>' if i == 0 else ""
        kind = "Invoice" if f.lower().startswith("invoice") else "Price list"
        link_id = ' id="dl-latest"' if i == 0 else ""
        rows.append(
            f"<tr><td><b>{f}</b>{latest}</td><td>{kind}</td><td>{size:,} bytes</td>"
            f'<td><a class="dl"{link_id} href="/download/{f}">Download</a></td></tr>')
    body = f"""
    <div class="card wide">
      <h1>Documents</h1>
      <div class="hint">Invoices and price lists published to your account,
      newest first.</div>
      <table>
        <tr><th>Document</th><th>Type</th><th>Size</th><th></th></tr>
        {''.join(rows) or '<tr><td colspan="4">No documents published (run make_fixtures.py).</td></tr>'}
      </table>
    </div>"""
    nav = f'Signed in as <b>{USERNAME}</b> <a href="/logout">Sign out</a>'
    return page("Documents", body, nav=nav)


@app.route("/download/<path:fname>")
def download(fname):
    if not session.get("authed"):
        return redirect(url_for("login"))
    return send_from_directory(FILES_DIR, fname, as_attachment=True)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ------------------------------------------------- demo "authenticator app" (stage prop)
@app.route("/authenticator")
def authenticator():
    body = """
    <div class="card">
      <h1>Demo authenticator</h1>
      <div class="hint">The same rotating one-time code a phone authenticator app would
      show for this account. Open this beside the sign-in page to demonstrate 2FA live.</div>
      <div class="code-big" id="code">------</div>
      <div class="count"><span id="sec">--</span>s until the code rotates</div>
      <div class="bar"><div id="bar" style="width:100%"></div></div>
    </div>
    <script>
      async function tick() {
        try {
          const r = await fetch('/authenticator/code');
          const j = await r.json();
          document.getElementById('code').textContent = j.code;
          document.getElementById('sec').textContent = j.seconds_left;
          document.getElementById('bar').style.width = (j.seconds_left / 30 * 100) + '%';
        } catch (e) {}
      }
      tick(); setInterval(tick, 1000);
    </script>"""
    return page("Demo authenticator", body)


@app.route("/authenticator/code")
def authenticator_code():
    return jsonify({"code": totp_now(TOTP_SECRET),
                    "seconds_left": int(30 - (time.time() % 30))})


@app.route("/healthz")
def healthz():
    return Response("ok", mimetype="text/plain")


if __name__ == "__main__":
    os.makedirs(FILES_DIR, exist_ok=True)
    print(f"Meridian demo portal on http://127.0.0.1:{PORT}  "
          f"(user={USERNAME}, TOTP seed={TOTP_SECRET})")
    app.run(host="127.0.0.1", port=PORT, debug=False)
