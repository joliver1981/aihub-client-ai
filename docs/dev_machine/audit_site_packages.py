"""Verify installed packages against their pip RECORD hashes.

For each given conda env, walks every *.dist-info/RECORD in site-packages and
re-hashes the installed files. Any mismatch = a file modified after install
(i.e., a local patch that `pip install` will NOT reproduce on a new machine).

Also reports:
  - packages installed editable / from local paths (direct_url.json)
  - top-level site-packages dirs with no owning dist-info (vendored/hand-copied)

Output: JSON + markdown summary in the same directory as this script.
"""
import base64
import csv
import hashlib
import io
import json
import os
import sys
import time

ENVS_ROOT = r"C:\Users\james\miniconda3\envs"
OUT_DIR = os.path.dirname(os.path.abspath(__file__))

SKIP_SUFFIXES = (".pyc", ".pyo")


def urlsafe_b64_nopad(digest: bytes) -> str:
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def hash_file(path: str, algo: str):
    h = hashlib.new(algo)
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return urlsafe_b64_nopad(h.digest())


def audit_env(env_name: str):
    sp = os.path.join(ENVS_ROOT, env_name, "Lib", "site-packages")
    result = {
        "env": env_name,
        "site_packages": sp,
        "packages_checked": 0,
        "files_checked": 0,
        "modified": {},   # package -> [files]
        "missing": {},    # package -> [files]
        "local_installs": [],  # direct_url.json packages
        "unowned_toplevel": [],
        "errors": [],
    }
    if not os.path.isdir(sp):
        result["errors"].append("site-packages not found")
        return result

    owned_toplevel = set()
    dist_infos = [d for d in os.listdir(sp) if d.endswith(".dist-info")]
    for di in sorted(dist_infos):
        di_path = os.path.join(sp, di)
        pkg = di.rsplit("-", 1)[0]
        record = os.path.join(di_path, "RECORD")
        durl = os.path.join(di_path, "direct_url.json")
        if os.path.isfile(durl):
            try:
                with open(durl, "r", encoding="utf-8") as f:
                    info = json.load(f)
                result["local_installs"].append({"package": di, "url": info.get("url", "")})
            except Exception as e:
                result["errors"].append(f"{di}: direct_url.json unreadable: {e}")
        if not os.path.isfile(record):
            result["errors"].append(f"{di}: no RECORD (conda-managed or broken)")
            continue
        result["packages_checked"] += 1
        try:
            with open(record, "r", encoding="utf-8", newline="") as f:
                rows = list(csv.reader(f))
        except Exception as e:
            result["errors"].append(f"{di}: RECORD unreadable: {e}")
            continue
        for row in rows:
            if len(row) < 2 or not row[1]:
                continue  # no hash recorded (RECORD itself, pyc files)
            rel, hashspec = row[0], row[1]
            if rel.endswith(SKIP_SUFFIXES) or "__pycache__" in rel:
                continue
            try:
                algo, expected = hashspec.split("=", 1)
            except ValueError:
                continue
            path = os.path.normpath(os.path.join(sp, rel))
            # track top-level ownership for unowned-dir detection
            relnorm = os.path.normpath(rel)
            if not relnorm.startswith(".."):
                owned_toplevel.add(relnorm.split(os.sep)[0])
            if not os.path.isfile(path):
                result["missing"].setdefault(di, []).append(rel)
                continue
            result["files_checked"] += 1
            try:
                actual = hash_file(path, algo.replace("-", "_"))
            except Exception as e:
                result["errors"].append(f"{di}:{rel}: hash error {e}")
                continue
            if actual != expected:
                result["modified"].setdefault(di, []).append(rel)

    for entry in sorted(os.listdir(sp)):
        full = os.path.join(sp, entry)
        if not os.path.isdir(full):
            continue
        if entry.endswith((".dist-info", ".egg-info")) or entry in ("__pycache__",):
            continue
        if entry not in owned_toplevel:
            result["unowned_toplevel"].append(entry)
    return result


def main():
    envs = sys.argv[1:]
    if not envs:
        print("usage: audit_site_packages.py env1 env2 ...")
        sys.exit(2)
    all_results = []
    for i, env in enumerate(envs, 1):
        t0 = time.time()
        print(f"[{i}/{len(envs)}] auditing {env} ...", flush=True)
        r = audit_env(env)
        r["seconds"] = round(time.time() - t0, 1)
        print(
            f"    {r['packages_checked']} pkgs, {r['files_checked']} files, "
            f"{len(r['modified'])} modified pkgs, {len(r['missing'])} pkgs w/ missing files "
            f"({r['seconds']}s)",
            flush=True,
        )
        all_results.append(r)
        with open(os.path.join(OUT_DIR, "audit_results.json"), "w", encoding="utf-8") as f:
            json.dump(all_results, f, indent=1)

    md = io.StringIO()
    md.write("# Site-packages local-modification audit\n\n")
    for r in all_results:
        md.write(f"## {r['env']}\n")
        md.write(
            f"- {r['packages_checked']} packages / {r['files_checked']} files checked in {r['seconds']}s\n"
        )
        if r["modified"]:
            md.write("- **MODIFIED (local patches — pip cannot reproduce):**\n")
            for pkg, files in sorted(r["modified"].items()):
                md.write(f"  - `{pkg}`\n")
                for fp in files[:20]:
                    md.write(f"    - {fp}\n")
                if len(files) > 20:
                    md.write(f"    - ... +{len(files) - 20} more\n")
        else:
            md.write("- no modified package files detected\n")
        if r["missing"]:
            md.write(f"- packages with missing files: {', '.join(sorted(r['missing']))}\n")
        if r["local_installs"]:
            md.write("- local/editable installs (direct_url.json):\n")
            for li in r["local_installs"]:
                md.write(f"  - {li['package']} <- {li['url']}\n")
        if r["unowned_toplevel"]:
            md.write(
                f"- unowned top-level dirs (vendored/hand-copied): {', '.join(r['unowned_toplevel'][:30])}\n"
            )
        interesting = [e for e in r["errors"] if "no RECORD" not in e][:10]
        no_record = len([e for e in r["errors"] if "no RECORD" in e])
        if no_record:
            md.write(f"- dists without RECORD (unverifiable): {no_record}\n")
        for e in interesting:
            md.write(f"- error: {e}\n")
        md.write("\n")
    with open(os.path.join(OUT_DIR, "audit_results.md"), "w", encoding="utf-8") as f:
        f.write(md.getvalue())
    print("done -> audit_results.md / audit_results.json", flush=True)


if __name__ == "__main__":
    main()
