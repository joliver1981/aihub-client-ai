"""Scale battery: the portfolio question at 120 documents, legacy vs v2, graded on
exact store-ID recall/precision from the generator's ground truth.

Usage:  python run_scale.py --label legacy|v2
The orchestrator flips DOC_SEARCH_V2_AGENT_IDS (+ agent-api restart) between passes.
Appends results to SCALE_RESULTS.md and scale_results.json.
"""
import argparse
import datetime
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..', '..'))
sys.path.insert(0, REPO)

import requests
import config as cfg  # noqa: F401

BASE = os.getenv('DCT13_BASE_URL', 'http://127.0.0.1:5001')
API_KEY = os.getenv('AIHUB_API_KEY') or os.getenv('API_KEY') or 'DB27D555-03A8-446E-9C23-8DAAA95EAD21'
CHAT_TIMEOUT = int(os.getenv('DCT13_SCALE_CHAT_TIMEOUT', 1500))

QUESTION = ("Go through every lease in your knowledge base and list EVERY store ID whose lease "
            "makes HVAC (climate control) maintenance the LANDLORD's responsibility. "
            "Answer with the complete list of store IDs.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--label', required=True, choices=['legacy', 'v2'])
    args = ap.parse_args()

    truth = json.load(open(os.path.join(HERE, 'scale_ground_truth.json'), encoding='utf-8'))
    expected = {s for s, t in truth.items() if t['hvac'] == 'landlord'}
    all_ids = set(truth.keys())
    st = json.load(open(os.path.join(HERE, 'scale_state.json'), encoding='utf-8'))
    agent_id = st['agent_id']

    print(f"[{args.label}] asking agent {agent_id} ({len(truth)} docs, {len(expected)} expected landlord stores)")
    started = datetime.datetime.now()
    r = requests.post(f"{BASE}/api/agents/{agent_id}/chat",
                      headers={"X-API-Key": API_KEY, "Authorization": f"Bearer {API_KEY}",
                               "Content-Type": "application/json"},
                      json={'prompt': QUESTION, 'history': '[]'}, timeout=CHAT_TIMEOUT)
    elapsed = (datetime.datetime.now() - started).total_seconds()
    answer = str(r.json().get('response') or r.json().get('answer') or '') if r.status_code == 200 else f'[HTTP {r.status_code}]'

    mentioned = set(re.findall(r'\bS\d{3}\b', answer)) & all_ids
    correct = mentioned & expected
    wrong = mentioned - expected
    missed = expected - mentioned
    recall = len(correct) / len(expected) if expected else 0
    precision = len(correct) / len(mentioned) if mentioned else 0
    syn_expected = {s for s in expected if truth[s]['synonym']}
    syn_found = syn_expected & mentioned
    has_ledger = bool(re.search(r'coverage|in scope|read in full', answer, re.I))

    result = dict(label=args.label, when=started.isoformat(timespec='seconds'),
                  elapsed_s=round(elapsed, 1), expected=len(expected),
                  mentioned=len(mentioned), correct=len(correct),
                  recall=round(recall, 3), precision=round(precision, 3),
                  wrong=sorted(wrong), missed=sorted(missed),
                  synonym_found=f"{len(syn_found)}/{len(syn_expected)}",
                  ledger_relayed=has_ledger, answer_chars=len(answer))

    print(json.dumps(result, indent=1))

    jpath = os.path.join(HERE, 'scale_results.json')
    data = json.load(open(jpath, encoding='utf-8')) if os.path.exists(jpath) else []
    data.append(result)
    json.dump(data, open(jpath, 'w', encoding='utf-8'), indent=1)

    with open(os.path.join(HERE, 'SCALE_RESULTS.md'), 'a', encoding='utf-8') as f:
        f.write(f"\n## {args.label} — {result['when']} ({elapsed:.0f}s)\n\n"
                f"- recall **{recall:.1%}** ({len(correct)}/{len(expected)}) · precision "
                f"**{precision:.1%}** · synonym-only found {result['synonym_found']} · "
                f"ledger relayed: {has_ledger}\n"
                f"- missed: {sorted(missed) or '—'}\n- wrong: {sorted(wrong) or '—'}\n\n"
                f"<details><summary>answer ({len(answer)} chars)</summary>\n\n{answer[:8000]}\n\n</details>\n")
    print(f"appended to SCALE_RESULTS.md")


if __name__ == '__main__':
    main()
