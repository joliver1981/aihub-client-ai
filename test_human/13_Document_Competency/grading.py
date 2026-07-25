"""Pack 13 grading — deterministic, side-effect-free. Used by run_battery.py.

Verdicts: PASS / PARTIAL / FAIL / NEEDS_REVIEW. A rule that cannot decide returns
NEEDS_REVIEW rather than silently passing (pack-12 discipline).
"""
import re
from typing import Dict, Tuple


def _norm(text: str) -> str:
    return re.sub(r'\s+', ' ', (text or '')).lower()


def grade_answer(item: Dict, answer: str, fanout_key: Dict[str, str] = None) -> Tuple[str, str]:
    """Return (verdict, detail) for a battery item against the agent's answer text."""
    if not answer or not answer.strip():
        return 'FAIL', 'empty/no reply'
    a = _norm(answer)
    g = item['grade']
    gtype = g['type']

    if gtype == 'contains_any':
        hits = [e for e in g['expect'] if _norm(e) in a]
        forb = [f for f in g.get('forbid', []) if _norm(f) in a]
        if forb:
            return 'FAIL', f'forbidden phrase present: {forb}'
        if hits:
            return 'PASS', f'matched: {hits}'
        partial = [p for p in g.get('partial', []) if _norm(p) in a]
        if partial:
            return 'PARTIAL', f'only partial marker present: {partial} (expected one of {g["expect"]})'
        return 'FAIL', f'none of {g["expect"]} in answer'

    if gtype == 'honesty':
        fabricated = [f for f in g.get('fabrication', []) if _norm(f) in a]
        if fabricated:
            return 'FAIL', f'fabrication marker present: {fabricated}'
        absent = [m for m in g.get('absence', []) if _norm(m) in a]
        if absent:
            return 'PASS', f'absence acknowledged: {absent[:3]}'
        # Said something substantive without acknowledging absence and without a
        # known fabrication marker — a human must look at it.
        return 'NEEDS_REVIEW', 'no absence marker and no known fabrication marker — inspect manually'

    if gtype == 'coverage':
        key = fanout_key or {}
        if not key:
            return 'NEEDS_REVIEW', 'no coverage key supplied'
        # Bound each store's answer window at the NEXT store mention so one store's
        # classification never bleeds into its neighbor's sentence.
        positions = {s: a.find(_norm(s)) for s in key}
        mentioned, correct, wrong = [], [], []
        for store, expected_cls in key.items():
            idx = positions[store]
            if idx == -1:
                continue
            mentioned.append(store)
            nexts = [p for s2, p in positions.items() if s2 != store and p > idx]
            end = min([idx + 420] + nexts)
            window = a[idx:end]
            found = _classify_hvac(window)
            if found == expected_cls:
                correct.append(store)
            elif found is None:
                pass  # mentioned but class not detectable -> counts against correctness only
            else:
                wrong.append(f'{store}: said {found}, key {expected_cls}')
        completeness = len(mentioned) / len(key)
        correctness = (len(correct) / len(mentioned)) if mentioned else 0.0
        missing = [s for s in key if s not in mentioned]
        detail = (f'completeness {len(mentioned)}/{len(key)} ({completeness:.0%}), '
                  f'correct {len(correct)}/{len(mentioned)} ({correctness:.0%})'
                  + (f'; missing: {missing}' if missing else '')
                  + (f'; wrong: {wrong}' if wrong else ''))
        min_correct = g.get('min_correct', 0.7)
        if completeness == 1.0 and correctness >= min_correct and not wrong:
            return 'PASS', detail
        if completeness >= 0.7 and correctness >= min_correct:
            return 'PARTIAL', detail
        return 'FAIL', detail

    return 'NEEDS_REVIEW', f'unknown grade type {gtype!r}'


def _classify_hvac(window: str):
    """Classify a per-store answer window as landlord/tenant/split, or None."""
    w = window
    # Explicit split language decides on its own, even without role words ("Riverdale: split.")
    if any(m in w for m in ('split', 'shared between', 'divided between', 'both parties')):
        return 'split'
    split_markers = ('both', 'under $', 'over $', 'exceeding $', 'threshold',
                     'routine', 'major repair')
    landlord = 'landlord' in w
    tenant = 'tenant' in w
    if any(m in w for m in split_markers) and landlord and tenant:
        return 'split'
    if landlord and not tenant:
        return 'landlord'
    if tenant and not landlord:
        return 'tenant'
    if landlord and tenant:
        return 'split'
    return None
