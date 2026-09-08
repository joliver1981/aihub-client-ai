"""Run the pack-23 question bank against the DOCUMENT STORE lane.

    python run_docstore.py --limit 2        # smoke first
    python run_docstore.py                  # all 75
    python run_docstore.py --ids Q001,Q014

This targets `POST /api/internal/document-search-unified` -> `document_search_unified`,
the production facade that routes COUNT-shaped questions to `doc_search_v3.enumerate`
(real denominator, one verdict per document) and LOOKUP questions to the legacy engine.

NOT the agent-knowledge lane. `KNOWLEDGE_BRUTE_FORCE_PAGE_THRESHOLD` is read only in
`agent_knowledge_integration.py` and has no effect here -- there is no brute-force branch
in this engine. What this runner records instead is the per-question `engine` and
`approach` the facade actually chose, which is the equivalent routing fact for this lane.

Writes answers incrementally to `<out>/answers_docstore.json` so a long run is resumable
and partial results are always readable.
"""
import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, REPO)

import requests                                        # noqa: E402

BASE = os.getenv("PACK23_BASE_URL", "http://127.0.0.1:5001")
ENDPOINT = f"{BASE}/api/internal/document-search-unified"


def internal_key():
    """Derive the same internal key the server derived.

    get_internal_api_key() = PBKDF2(machine_id + ':' + os.environ['API_KEY']). The server
    gets API_KEY from .env; a bare python process does not, so deriving without loading
    .env first yields a different key and a 401 that looks like an auth bug rather than a
    missing environment variable.
    """
    if not os.getenv("API_KEY"):
        env = os.path.join(REPO, ".env")
        with open(env, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line.startswith("API_KEY=") and "=" in line:
                    os.environ["API_KEY"] = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
    if not os.getenv("API_KEY"):
        raise SystemExit("API_KEY not found in environment or .env — cannot derive the "
                         "internal key the server will accept")
    from role_decorators import get_internal_api_key
    return get_internal_api_key()


def ask(question, key, timeout=300, retries=4):
    delay = 5
    for attempt in range(retries):
        try:
            r = requests.post(ENDPOINT, headers={"X-Internal-API-Key": key},
                              json={"question": question, "check_completeness": True},
                              timeout=timeout)
        except requests.exceptions.ReadTimeout:
            return {"_error": f"client timeout after {timeout}s"}
        if r.status_code == 503:
            wait = int(r.headers.get("Retry-After") or delay)
            print(f"      503 busy, retry in {wait}s")
            time.sleep(wait)
            delay = min(delay * 2, 60)
            continue
        if r.status_code != 200:
            return {"_error": f"HTTP {r.status_code}: {r.text[:200]}"}
        body = r.json()
        if body.get("status") != "success":
            return {"_error": str(body.get("message"))[:200]}
        return body.get("result") or {}
    return {"_error": "gave up after repeated 503s"}


def answer_text(res):
    """The facade returns the prose in `answer` for some engines and `text` for others."""
    for k in ("answer", "text"):
        v = res.get(k)
        if isinstance(v, str) and v.strip():
            return v
    passages = res.get("passages") or []
    if passages:
        return "\n".join(f"{p.get('filename', '')} p{p.get('page', '')}: {p.get('text', '')}"
                         for p in passages[:40])
    return ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=r"C:\temp\doc_corpus_250")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--ids")
    ap.add_argument("--cls", help="only this question class")
    ap.add_argument("--fresh", action="store_true", help="ignore existing answers")
    a = ap.parse_args()

    qb = json.load(open(os.path.join(a.out, "questions.json"), encoding="utf-8"))
    qs = qb["questions"]
    if a.cls:
        qs = [q for q in qs if q["class"] == a.cls]
    if a.ids:
        want = {x.strip().upper() for x in a.ids.split(",")}
        qs = [q for q in qs if q["id"] in want]

    apath = os.path.join(a.out, "answers_docstore.json")
    mpath = os.path.join(a.out, "routing_docstore.json")
    answers = [] if a.fresh or not os.path.exists(apath) else \
        json.load(open(apath, encoding="utf-8"))
    meta = {} if a.fresh or not os.path.exists(mpath) else \
        json.load(open(mpath, encoding="utf-8"))
    done = {x["id"] for x in answers}
    todo = [q for q in qs if q["id"] not in done]
    if a.limit:
        todo = todo[: a.limit]

    key = internal_key()
    print(f"{len(todo)} question(s) to ask -> {ENDPOINT}")
    t0 = time.time()
    for i, q in enumerate(todo, 1):
        qt0 = time.time()
        res = ask(q["question"], key)
        txt = answer_text(res)
        answers.append({"id": q["id"], "answer": txt})
        meta[q["id"]] = {
            "cls": q["class"],
            "engine": res.get("engine"),
            "approach": res.get("approach"),
            "count": res.get("count"),
            "passages": len(res.get("passages") or []),
            "chars": len(txt),
            "secs": round(time.time() - qt0, 1),
            "error": res.get("_error") or res.get("error"),
        }
        json.dump(answers, open(apath, "w", encoding="utf-8"), indent=1)
        json.dump(meta, open(mpath, "w", encoding="utf-8"), indent=1)
        m = meta[q["id"]]
        flag = f" ERROR: {m['error']}" if m["error"] else ""
        print(f"  [{i}/{len(todo)}] {q['id']} {q['class']:<9} "
              f"{str(m['engine'])[:28]:<29} {m['secs']:>5}s {m['chars']:>6}ch{flag}")
        if i % 10 == 0:
            el = time.time() - t0
            print(f"      -- {el / 60:.1f} min, eta {(len(todo) - i) * el / i / 60:.0f} min")

    print(f"\n{len(answers)} answers -> {apath}")
    print(f"routing metadata      -> {mpath}")
    errs = [k for k, v in meta.items() if v.get("error")]
    if errs:
        print(f"{len(errs)} question(s) errored: {', '.join(sorted(errs)[:12])}")


if __name__ == "__main__":
    main()
