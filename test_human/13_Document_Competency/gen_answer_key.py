"""Derive _ANSWER_KEY.md from battery.py (single source of truth). Regenerate after edits."""
import os
import battery as B

HERE = os.path.dirname(os.path.abspath(__file__))
out = []
out.append("# Pack 13 — Answer Key (derived from battery.py — do not hand-edit)\n")

out.append("## Phase A — ingestion integrity (SQL oracle over DocumentPages)\n")
out.append("| id | fixture | pages | max blank | must contain | why |")
out.append("|---|---|---|---|---|---|")
for a in B.INGESTION:
    out.append(f"| {a['id']} | `{a['fixture']}` | {a['pages']} | {a['max_blank']} | "
               f"{'; '.join(a['must_contain']) or '—'} | {a['why']} |")

out.append("\n## Phases B/C — questions and expected answers\n")
out.append("| id | tier | mode | question | expected |")
out.append("|---|---|---|---|---|")
for q in B.ALL_QUESTIONS:
    out.append(f"| {q['id']} | {q['tier']} | {q['mode']} | {q['q']} | {q['key']} |")

out.append("\n## FANOUT portfolio key (C2) — HVAC class per attached knowledge doc\n")
out.append("| store | class |")
out.append("|---|---|")
for store, cls in B.FANOUT_HVAC_KEY.items():
    out.append(f"| {store} | {cls} |")

out.append("\n## Full per-lease ground truth\n")
out.append("| store | property | expiry (base) | current expiry | HVAC | deposit |")
out.append("|---|---|---|---|---|---|")
for sid, L in B.LEASES.items():
    out.append(f"| {sid} | {L['name']} | {L['expiry']} | {L.get('expiry_current', '=')} | "
               f"{L['hvac']} | {L.get('deposit') or '—'} |")

open(os.path.join(HERE, '_ANSWER_KEY.md'), 'w', encoding='utf-8').write('\n'.join(out) + '\n')
print('wrote _ANSWER_KEY.md')
