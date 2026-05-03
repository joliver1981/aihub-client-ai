"""Structural + LLM-based judging of model output against an eval's expectation."""
import json
import re


def parse_commands_block(text):
    """Pull a {action, commands[]} dict out of a markdown-fenced or raw JSON response."""
    if not text:
        return None
    m = re.search(r'```(?:json)?\s*(\{[\s\S]*?\})\s*```', text)
    raw = m.group(1) if m else text
    s = raw.find('{')
    e = raw.rfind('}') + 1
    if s < 0 or e <= s:
        return None
    try:
        return json.loads(raw[s:e])
    except Exception:
        return None


def structural_judge(output_text, expected):
    """Grade an output against an eval's `expected` config.

    expected keys honored:
      - min_add_nodes (int)
      - required_node_types (list[str])
      - must_have_set_start_node (bool)
      - forbid_loop_pass_complete (bool) — flags Loop with both pass+complete outgoing
      - forbid_endloop_back_edge (bool)  — flags End Loop -> Loop physical edges
    """
    expected = expected or {}
    parsed = parse_commands_block(output_text)
    if parsed is None or 'commands' not in parsed:
        return {
            'score': 0,
            'max_score': 1,
            'passed': False,
            'reason': 'Could not parse JSON commands block from output',
            'details': {},
        }
    cmds = parsed.get('commands', [])
    add_node_cmds = [c for c in cmds if c.get('type') == 'add_node']
    add_node_count = len(add_node_cmds)
    node_types_present = {c.get('node_type') for c in add_node_cmds}
    has_start = any(c.get('type') == 'set_start_node' for c in cmds)

    checks = []

    # min_add_nodes
    if 'min_add_nodes' in expected:
        target = expected['min_add_nodes']
        passed = add_node_count >= target
        checks.append({
            'name': f'min_add_nodes >= {target}',
            'passed': passed,
            'detail': f'got {add_node_count}',
        })

    # required_node_types
    if 'required_node_types' in expected:
        for nt in expected['required_node_types']:
            passed = nt in node_types_present
            checks.append({
                'name': f'contains node_type "{nt}"',
                'passed': passed,
                'detail': 'present' if passed else 'missing',
            })

    # must_have_set_start_node
    if expected.get('must_have_set_start_node'):
        checks.append({
            'name': 'has set_start_node',
            'passed': has_start,
            'detail': 'present' if has_start else 'missing',
        })

    # Loop anti-pattern checks
    if expected.get('forbid_loop_pass_complete'):
        violations = _find_loop_pass_complete(cmds, add_node_cmds)
        passed = len(violations) == 0
        checks.append({
            'name': 'no Loop with pass+complete outgoing',
            'passed': passed,
            'detail': 'clean' if passed else f'violations on: {violations}',
        })

    if expected.get('forbid_endloop_back_edge'):
        violations = _find_endloop_back_edges(cmds, add_node_cmds)
        passed = len(violations) == 0
        checks.append({
            'name': 'no End Loop -> Loop physical edges',
            'passed': passed,
            'detail': 'clean' if passed else f'violations on: {violations}',
        })

    passed_count = sum(1 for c in checks if c['passed'])
    total = len(checks)
    return {
        'score': passed_count,
        'max_score': total,
        'passed': passed_count == total,
        'reason': f'{passed_count}/{total} checks passed',
        'details': {
            'add_node_count': add_node_count,
            'node_types_present': sorted(list(node_types_present)),
            'total_commands': len(cmds),
            'checks': checks,
        },
    }


def _find_loop_pass_complete(cmds, add_node_cmds):
    nodes = {c.get('node_id'): c for c in add_node_cmds}
    loops = {nid for nid, n in nodes.items() if n.get('node_type') == 'Loop'}
    by_src = {}
    for c in cmds:
        if c.get('type') == 'connect_nodes' and c.get('connection_type') in ('pass', 'complete'):
            by_src.setdefault(c.get('from'), set()).add(c.get('connection_type'))
    return [src for src, types in by_src.items() if src in loops and 'pass' in types and 'complete' in types]


def _find_endloop_back_edges(cmds, add_node_cmds):
    nodes = {c.get('node_id'): c for c in add_node_cmds}
    end_loops = {}
    for nid, n in nodes.items():
        if n.get('node_type') == 'End Loop':
            ref = (n.get('config') or {}).get('loopNodeId')
            if ref:
                end_loops[nid] = ref
    violations = []
    for c in cmds:
        if c.get('type') == 'connect_nodes':
            f, t = c.get('from'), c.get('to')
            if f in end_loops and end_loops[f] == t:
                violations.append(f'{f}->{t}')
    return violations


# ---------- Optional LLM judge ----------

LLM_JUDGE_SYSTEM = """You are evaluating whether a model's output correctly implements a workflow plan.

You will see:
1. The original plan (the user prompt the model received).
2. The expected behavior summary (what a correct response would include).
3. The model's actual output (a JSON commands block).

Score the actual output on these dimensions, each 1-5:
- coverage: did it create all the nodes the plan asked for?
- correctness: are the configs (paths, IDs, queries, conditions) faithful to the plan?
- structure: is the connection graph well-formed (start node, no orphans, branches handled)?

Return ONLY a JSON object on a single line with keys:
{"coverage": int, "correctness": int, "structure": int, "overall": int, "summary": "one sentence"}
"""


def llm_judge(plan_text, expected, output_text, judge_model_config, chat_fn):
    """Run an LLM judge. chat_fn is the same chat() function from llm_clients."""
    expected_summary = json.dumps(expected, indent=2) if expected else '(no specific expectations)'
    prompt = (
        f"PLAN:\n{plan_text}\n\n"
        f"EXPECTED BEHAVIOR:\n{expected_summary}\n\n"
        f"ACTUAL OUTPUT:\n{output_text}"
    )
    result = chat_fn(judge_model_config, LLM_JUDGE_SYSTEM, prompt, temperature=0.0)
    if not result.get('ok'):
        return {'ok': False, 'error': result.get('error')}
    content = (result.get('content') or '').strip()
    try:
        m = re.search(r'\{[\s\S]*?\}', content)
        if m:
            return {'ok': True, 'verdict': json.loads(m.group(0)), 'raw': content}
    except Exception:
        pass
    return {'ok': True, 'raw': content, 'verdict': None}
