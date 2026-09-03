"""
Two-path regression driver — run the human-grade suite against EITHER the local
dev tree (runs from source) or an INSTALLED box (installed from the installer).

WHY: the packs grew up pointed at localhost. Most of them already read the
REGP_BASE / CC_BASE environment convention, but nothing set those consistently,
so "run the suite against the install" meant remembering six different env vars
and two different Python interpreters. Getting one of them wrong silently tests
the WRONG box, which is worse than not running at all.

    python run_regression_suite.py --target local
    python run_regression_suite.py --target 10.0.0.6 \
           --api-key-file C:\\Users\\james\\.secrets\\aihub-10.0.0.6-api-key.txt

WHAT EACH TARGET MEANS
  local        - http://localhost:5001 etc., signing with this tree's API_KEY.
  <host>       - an installed application. Every pack is pointed at that box and
                 tokens are signed with THAT box's key (--api-key-file), because
                 CC and The Agent reject a token signed from another install.

INTERPRETERS: pack 20 imports agent_service.brain -> claude_agent_sdk, which
lives only in the aihub-agent env. Running it under aihub2.1 does not fail a
test, it crashes the pack. The map below picks the right interpreter per pack so
that cannot happen by accident.

REMOTE COVERAGE IS NOT LOCAL COVERAGE. Some checks cannot run against a remote
box (pack 14 verifies engine-written files over an admin share; pack 22 drives
the local stack). Those SKIP with a reason. Read the per-pack reports — a thin
run that says CLEAN is not the same as a full run that says CLEAN.

FIXTURES: a fresh install has no NL->SQL oracle agent, no AIRDB2 dictionary, no
SFTP secret and no simulated-user agent, so the checks that need them SKIP with
a seed hint. `--seed` runs _scripts/seed_target_fixtures.py against the target
first (idempotent; `--seed-verify` also asks the oracle the pack-15 question),
which turns those SKIPs into real coverage.

HISTORY: every pack now keeps an installed box's results in
results_history/host_<ip>/ and its report in REPORT_LATEST_<ip>.md, so a remote
run never diffs against — or becomes — the local baseline. The quarantine
below is kept as a safety net for a pack that forgets.
"""
import argparse
import os
import re
import subprocess
import sys
import time
import datetime

# The pack reports carry status glyphs (✅/❌/⏭). When stdout is a file or a
# cp1252 console, echoing a pack's tail raised UnicodeEncodeError and KILLED
# the driver after pack 24 (2026-09-02) — packs 16-20 never ran. Degrade
# lossily instead of dying.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
TEST_HUMAN = os.path.abspath(os.path.join(HERE, ".."))
REPO = os.path.abspath(os.path.join(TEST_HUMAN, ".."))

CONDA = os.path.join(os.path.expanduser("~"), "miniconda3", "envs")
PY_MAIN = os.path.join(CONDA, "aihub2.1", "python.exe")
PY_AGENT = os.path.join(CONDA, "aihub-agent", "python.exe")

# pack dir -> (interpreter, extra args, supports --competency, remote-capable)
PACKS = [
    ("15_Platform_Regression", PY_MAIN, [], True, True),
    ("24_Installed_Smoke", PY_MAIN, [], False, True),
    ("16_CC_Agent_Matrix", PY_MAIN, [], True, True),
    ("17_Scheduling_Matrix", PY_MAIN, [], True, True),
    ("18_AuthZ_Matrix", PY_MAIN, [], False, True),
    ("19_CC_Tier_C", PY_MAIN, [], False, True),
    ("20_The_Agent", PY_AGENT, [], False, True),
    ("22_GA_Code_Interpreter", PY_MAIN, [], False, False),   # reads the LOCAL ledger
]

SEED = os.path.join(HERE, "seed_target_fixtures.py")

VERDICT_RE = re.compile(r"##\s*Verdict:\s*\*\*(.+?)\*\*\s*[—-]?\s*(.*)")


def read_key(path):
    if not path:
        return ""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return next((ln.strip() for ln in fh if ln.strip()), "")
    except OSError as e:
        print(f"[driver] WARNING: could not read {path}: {e}")
        return ""


def build_env(host, api_key):
    """One place that decides which application every pack talks to."""
    env = dict(os.environ)
    is_local = host in ("local", "localhost", "127.0.0.1")
    h = "localhost" if is_local else host
    env["REGP_BASE"] = f"http://{h}:5001"
    env["CC_BASE"] = f"http://{'127.0.0.1' if is_local else h}:5091"
    env["AIHUB_TARGET_HOST"] = "127.0.0.1" if is_local else h
    env["SERVICE_HOST"] = "127.0.0.1" if is_local else h
    if api_key:
        # Signs CC / agent-service bearer tokens AND the X-API-Key service
        # header. Must be the TARGET box's key or everything 401s.
        env["API_KEY"] = api_key
        env["AI_HUB_API_KEY"] = api_key
        env.pop("CC_JWT_SECRET", None)
    return env, is_local


def quarantine_remote_results(pack_dir, host, started_at):
    """SAFETY NET: move results a remote run wrote into the FLAT history folder
    into results_history/host_<h>/.

    Since 2026-09-02 every pack segregates its own history per target (the
    `_target_host()` block in each runner), so this normally moves nothing.
    It stays because the failure it guards against is expensive: a remote run
    diffed against the LOCAL baseline reads as "REGRESSIONS DETECTED", and the
    remote result then becomes the baseline the next LOCAL run is judged against.
    """
    hist = os.path.join(pack_dir, "results_history")
    if not os.path.isdir(hist):
        return 0
    dest = os.path.join(hist, f"host_{host}")
    moved = 0
    for name in os.listdir(hist):
        src = os.path.join(hist, name)
        if not os.path.isfile(src):
            continue
        try:
            if os.path.getmtime(src) < started_at:
                continue
        except OSError:
            continue
        os.makedirs(dest, exist_ok=True)
        try:
            os.replace(src, os.path.join(dest, name))
            moved += 1
        except OSError:
            pass
    return moved


def pack_cmd(pack, py, extra, supports_comp, host, is_local, competency, api_key_file):
    cmd = [py, "runner.py"] + list(extra)
    if pack == "15_Platform_Regression" and not is_local:
        cmd += ["--host", host]
    if pack == "24_Installed_Smoke":
        cmd += ["--host", "localhost" if is_local else host]
        if api_key_file:
            cmd += ["--api-key-file", api_key_file]
    if competency and supports_comp:
        cmd += ["--competency"]
    return cmd


def seed_target(target, env, verify):
    """Create the fixtures the packs resolve by name (idempotent). Returns the
    seed script's exit code; a failure is reported but does not stop the suite —
    the packs SKIP honestly for whatever is still missing."""
    cmd = [PY_MAIN, SEED, "--target", target] + (["--verify"] if verify else [])
    print(f"\n[driver] ===== seed fixtures =====\n[driver] {' '.join(cmd)}")
    proc = subprocess.run(cmd, cwd=HERE, env=env, timeout=1800, capture_output=True,
                          text=True, encoding="utf-8", errors="replace")
    out = (proc.stdout or "") + (proc.stderr or "")
    for line in out.strip().splitlines()[-14:]:
        print(f"[seed] {line}" if not line.startswith("[seed]") else line)
    if proc.returncode:
        print(f"[driver] WARNING: seeding exited {proc.returncode}; fixture-dependent "
              f"checks will SKIP with the reason")
    return proc.returncode


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", required=True,
                    help="'local' for the dev tree, or an installed host e.g. 10.0.0.6")
    ap.add_argument("--api-key-file", default=None,
                    help="file holding the TARGET box's API_KEY (required for a "
                         "remote target; CC and The Agent 401 without it)")
    ap.add_argument("--competency", action="store_true",
                    help="include the competency tier where a pack supports it")
    ap.add_argument("--seed", action="store_true",
                    help="run seed_target_fixtures.py against the target first")
    ap.add_argument("--seed-verify", action="store_true",
                    help="--seed, then ask the oracle agent the pack-15 question")
    ap.add_argument("--only", default=None, help="substring match on pack dir name")
    ap.add_argument("--skip", default=None,
                    help="comma-separated substrings of pack dirs to leave out "
                         "(e.g. --skip 20_The_Agent while that pack is being edited)")
    ap.add_argument("--timeout", type=int, default=3600)
    args = ap.parse_args()

    api_key = read_key(args.api_key_file)
    env, is_local = build_env(args.target, api_key)
    if not is_local and not api_key:
        print("[driver] WARNING: remote target without --api-key-file — CC and "
              "The Agent checks will SKIP (they cannot authenticate).")

    label = "LOCAL DEV (from source)" if is_local else f"INSTALLED {args.target}"
    print(f"[driver] target: {label}")
    print(f"[driver] REGP_BASE={env['REGP_BASE']}  CC_BASE={env['CC_BASE']}")
    print(f"[driver] signing key: {'target box key' if api_key else 'this tree'}")

    seed_rc = None
    if args.seed or args.seed_verify:
        seed_rc = seed_target(args.target, env, args.seed_verify)

    selected = [p for p in PACKS if not args.only or args.only in p[0]]
    skips = [s.strip() for s in (args.skip or "").split(",") if s.strip()]
    if skips:
        selected = [p for p in selected if not any(s in p[0] for s in skips)]
    rows = []
    for pack, py, extra, supports_comp, remote_ok in selected:
        d = os.path.join(TEST_HUMAN, pack)
        if not os.path.isfile(os.path.join(d, "runner.py")):
            rows.append((pack, "MISSING", "no runner.py", 0.0))
            continue
        if not os.path.isfile(py):
            rows.append((pack, "NO-INTERPRETER", os.path.basename(py), 0.0))
            continue
        if not is_local and not remote_ok:
            rows.append((pack, "SKIPPED", "pack is local-only", 0.0))
            continue

        cmd = pack_cmd(pack, py, extra, supports_comp, args.target, is_local,
                       args.competency, args.api_key_file)
        print(f"\n[driver] ===== {pack} =====")
        print(f"[driver] {' '.join(cmd)}")
        t0 = time.time()
        try:
            # The pack reports are UTF-8 (em-dashes, status glyphs). Without an
            # explicit encoding Windows decodes them as cp1252 and the Verdict
            # line is mangled, so every pack reports a bare rc= instead of its
            # real result — a silent loss of the only thing this driver reads.
            proc = subprocess.run(cmd, cwd=d, env=env, timeout=args.timeout,
                                  capture_output=True, text=True,
                                  encoding="utf-8", errors="replace")
            out = (proc.stdout or "") + (proc.stderr or "")
            rc = proc.returncode
        except subprocess.TimeoutExpired:
            rows.append((pack, "TIMEOUT", f">{args.timeout}s", time.time() - t0))
            continue
        el = time.time() - t0

        verdict, detail = f"rc={rc}", ""
        for line in out.splitlines():
            m = VERDICT_RE.search(line)
            if m:
                verdict, detail = m.group(1).strip(), m.group(2).strip()
        # A pack that stopped for ONE environmental reason (pack 20: the target
        # has no Anthropic key) says so once instead of failing every check.
        mb = re.search(r"\*\*Result: BLOCKED — (.+?)\*\*", out) or re.search(r"\[BLOCKED\] (.+)", out)
        if mb or rc == 3:
            verdict, detail = "BLOCKED", (mb.group(1).strip() if mb else "see pack report")
        # Packs that report PASS/FAIL lines instead of a Verdict header
        elif verdict.startswith("rc=") and "PASS" in out:
            p = len(re.findall(r"\[PASS\]", out))
            f = len(re.findall(r"\[FAIL\]", out))
            if p or f:
                verdict = "CLEAN" if not f else "FAILURES"
                detail = f"{p} PASS / {f} FAIL"
        elif verdict.startswith("rc=") and rc == 0:
            skip = next((ln.strip() for ln in out.splitlines() if ln.startswith("SKIP:")), "")
            verdict, detail = ("SKIPPED", skip[5:].strip()[:120]) if skip else ("CLEAN", "rc=0")
        if not is_local:
            moved = quarantine_remote_results(d, args.target, t0)
            if moved:
                print(f"[driver] moved {moved} result file(s) into "
                      f"results_history/host_{args.target}/")
                if verdict.startswith("REGRESSIONS"):
                    verdict, detail = "FIRST-REMOTE-RUN", (
                        f"{detail} — diffed against a LOCAL baseline; "
                        f"disregard, this pack now has its own remote history")

        rows.append((pack, verdict, detail, el))
        print(f"[driver] {pack}: {verdict} {detail} ({el/60:.1f}m)")
        tail = "\n".join(out.strip().splitlines()[-6:])
        if rc not in (0,) and tail:
            print(f"[driver] tail:\n{tail}")

    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = os.path.join(HERE, "suite_runs")
    os.makedirs(out_dir, exist_ok=True)
    lines = [f"# Regression Suite — {stamp}", "",
             f"- Target: **{label}**", f"- Base: `{env['REGP_BASE']}`",
             f"- Competency tier: {'yes' if args.competency else 'no'}",
             f"- Fixtures seeded first: "
             f"{'no' if seed_rc is None else ('yes' if seed_rc == 0 else f'attempted, exit {seed_rc}')}", "",
             "| pack | verdict | detail | minutes |", "|---|---|---|---:|"]
    for pack, verdict, detail, el in rows:
        lines.append(f"| {pack} | {verdict} | {detail} | {el/60:.1f} |")
    report = "\n".join(lines) + "\n"
    path = os.path.join(out_dir, f"SUITE_{'local' if is_local else args.target}_{stamp}.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(report)

    print("\n" + "=" * 72)
    print(report)
    print(f"Suite report: {path}")
    bad = [r for r in rows if r[1] not in ("CLEAN", "SKIPPED", "BLOCKED")]
    blocked = [r for r in rows if r[1] == "BLOCKED"]
    if blocked:
        print("BLOCKED packs (environment, not product): "
              + ", ".join(f"{r[0]} — {r[2]}" for r in blocked))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
