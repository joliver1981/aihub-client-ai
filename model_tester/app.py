"""model_tester — internal debug tool for evaluating LLMs against workflow eval scenarios.

A small standalone Flask app, separate from the main aihub service. Reads/writes
JSON files only — no database, no extra Python deps beyond what the main app
already has installed (Flask, requests, openai SDK).

Run with: start_model_tester.bat (or python app.py)
Default port: 5099
"""
import os
import sys
import json
import uuid
import time
from pathlib import Path
from datetime import datetime
from flask import Flask, jsonify, request, send_from_directory, render_template

# ---- Paths
HERE = Path(__file__).parent
DATA = HERE / 'data'
EVALS_DIR = DATA / 'evals'
SYSTEM_PROMPTS_DIR = DATA / 'system_prompts'
RESULTS_DIR = DATA / 'results'
SETTINGS_FILE = DATA / 'settings.json'

for d in (DATA, EVALS_DIR, SYSTEM_PROMPTS_DIR, RESULTS_DIR):
    d.mkdir(parents=True, exist_ok=True)

# Make our local modules importable
sys.path.insert(0, str(HERE))
import llm_clients
import judge as judge_mod

app = Flask(__name__, static_folder='static', template_folder='templates')


# ---------- Settings ----------

DEFAULT_SETTINGS = {
    'active_model_id': 'azure-prod-current',
    'judge_model_id': 'anthropic-opus-4-7',
    'models': {
        'azure-prod-current': {
            'label': 'Azure (production model from main app)',
            'provider': 'azure',
            'deployment': '',         # left blank — picks up from env / main app default
            'endpoint': '',
            'api_version': '2024-08-01-preview',
            'api_key_override': None,
        },
        # ----- OpenAI direct (current generation) -----
        'openai-gpt-5-4': {
            'label': 'OpenAI GPT-5.4',
            'provider': 'openai',
            'model': 'gpt-5.4',
            'api_key_override': None,
        },
        'openai-gpt-5-4-mini': {
            'label': 'OpenAI GPT-5.4 mini',
            'provider': 'openai',
            'model': 'gpt-5.4-mini',
            'api_key_override': None,
        },
        'openai-gpt-5-2': {
            'label': 'OpenAI GPT-5.2',
            'provider': 'openai',
            'model': 'gpt-5.2',
            'api_key_override': None,
        },
        'openai-gpt-5-2-mini': {
            'label': 'OpenAI GPT-5.2 mini',
            'provider': 'openai',
            'model': 'gpt-5.2-mini',
            'api_key_override': None,
        },
        # ----- OpenAI direct (legacy 4.x — kept for comparison testing) -----
        'openai-gpt-4-1': {
            'label': 'OpenAI GPT-4.1 (legacy)',
            'provider': 'openai',
            'model': 'gpt-4.1',
            'api_key_override': None,
        },
        'openai-gpt-4-1-mini': {
            'label': 'OpenAI GPT-4.1 mini (legacy)',
            'provider': 'openai',
            'model': 'gpt-4.1-mini',
            'api_key_override': None,
        },
        # ----- Anthropic (current generation) -----
        'anthropic-opus-4-7': {
            'label': 'Anthropic Claude Opus 4.7',
            'provider': 'anthropic',
            'model': 'claude-opus-4-7',
            'api_key_override': None,
        },
        'anthropic-opus-4-6': {
            'label': 'Anthropic Claude Opus 4.6',
            'provider': 'anthropic',
            'model': 'claude-opus-4-6',
            'api_key_override': None,
        },
        'anthropic-sonnet-4-7': {
            'label': 'Anthropic Claude Sonnet 4.7',
            'provider': 'anthropic',
            'model': 'claude-sonnet-4-7',
            'api_key_override': None,
        },
        'anthropic-sonnet-4-6': {
            'label': 'Anthropic Claude Sonnet 4.6',
            'provider': 'anthropic',
            'model': 'claude-sonnet-4-6',
            'api_key_override': None,
        },
        # ----- Local -----
        'lmstudio-local': {
            'label': 'LMStudio (local)',
            'provider': 'lmstudio',
            'endpoint': 'http://localhost:1234/v1',
            'model': 'local-model',
            'api_key_override': 'lm-studio',
        },
    },
}


def load_settings():
    if not SETTINGS_FILE.exists():
        SETTINGS_FILE.write_text(json.dumps(DEFAULT_SETTINGS, indent=2), encoding='utf-8')
        return dict(DEFAULT_SETTINGS)
    try:
        return json.loads(SETTINGS_FILE.read_text(encoding='utf-8'))
    except Exception:
        return dict(DEFAULT_SETTINGS)


def save_settings(s):
    SETTINGS_FILE.write_text(json.dumps(s, indent=2), encoding='utf-8')


# ---------- System prompts ----------

def resolve_system_prompt(eval_obj):
    """If the eval references a named system prompt, load it from disk.
    Otherwise return the inline system_prompt or empty string."""
    if eval_obj.get('system_prompt'):
        return eval_obj['system_prompt']
    ref = eval_obj.get('system_prompt_ref')
    if ref:
        path = SYSTEM_PROMPTS_DIR / f'{ref}.txt'
        if path.exists():
            return path.read_text(encoding='utf-8')
    return ''


# ---------- Eval CRUD ----------

def list_evals():
    out = []
    for p in sorted(EVALS_DIR.glob('*.json')):
        try:
            obj = json.loads(p.read_text(encoding='utf-8'))
            out.append({
                'id': obj.get('id', p.stem),
                'name': obj.get('name', p.stem),
                'category': obj.get('category', ''),
                'description': obj.get('description', ''),
                'tags': obj.get('tags', []),
                'system_prompt_ref': obj.get('system_prompt_ref'),
                'has_inline_system_prompt': bool(obj.get('system_prompt')),
            })
        except Exception:
            continue
    return out


def get_eval(eval_id):
    p = EVALS_DIR / f'{eval_id}.json'
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding='utf-8'))


def save_eval(eval_obj):
    eval_id = eval_obj.get('id')
    if not eval_id:
        eval_id = 'eval-' + uuid.uuid4().hex[:8]
        eval_obj['id'] = eval_id
    p = EVALS_DIR / f'{eval_id}.json'
    p.write_text(json.dumps(eval_obj, indent=2, ensure_ascii=False), encoding='utf-8')
    return eval_obj


def delete_eval(eval_id):
    p = EVALS_DIR / f'{eval_id}.json'
    if p.exists():
        p.unlink()
        return True
    return False


# ---------- Results ----------

def save_result(result):
    ts = datetime.now().strftime('%Y%m%d_%H%M%S_%f')[:18]
    rid = ts + '_' + (result.get('eval_id') or 'adhoc')
    result['result_id'] = rid
    result['saved_at'] = datetime.now().isoformat(timespec='seconds')
    p = RESULTS_DIR / f'{rid}.json'
    p.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding='utf-8')
    return result


def list_results(limit=200):
    files = sorted(RESULTS_DIR.glob('*.json'), reverse=True)[:limit]
    out = []
    for p in files:
        try:
            obj = json.loads(p.read_text(encoding='utf-8'))
            out.append({
                'result_id': obj.get('result_id', p.stem),
                'eval_id': obj.get('eval_id'),
                'eval_name': obj.get('eval_name'),
                'model_id': obj.get('model_id'),
                'model_label': obj.get('model_label'),
                'saved_at': obj.get('saved_at'),
                'elapsed_ms': obj.get('elapsed_ms'),
                'judge_passed': (obj.get('judge') or {}).get('passed'),
                'judge_score': (obj.get('judge') or {}).get('score'),
                'judge_max_score': (obj.get('judge') or {}).get('max_score'),
            })
        except Exception:
            continue
    return out


def get_result(rid):
    p = RESULTS_DIR / f'{rid}.json'
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding='utf-8'))


# ---------- Routes ----------

@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/settings', methods=['GET'])
def api_get_settings():
    s = load_settings()
    # Mask API keys in the response (we still keep them on disk in plain JSON;
    # this is a local debug tool, not production).
    masked = json.loads(json.dumps(s))
    for m in masked.get('models', {}).values():
        if m.get('api_key_override'):
            k = m['api_key_override']
            m['api_key_override'] = (k[:6] + '...' + k[-4:]) if len(k) > 12 else '***'
    return jsonify(masked)


@app.route('/api/settings', methods=['PUT'])
def api_put_settings():
    body = request.get_json() or {}
    cur = load_settings()
    # Merge top-level fields
    for k in ('active_model_id', 'judge_model_id'):
        if k in body:
            cur[k] = body[k]
    if 'models' in body:
        # Replace entire models map but preserve untouched-key API keys
        new_models = body['models']
        for mid, mcfg in new_models.items():
            existing = cur.get('models', {}).get(mid, {})
            # If the override is masked (contains '...') keep the existing
            override = mcfg.get('api_key_override')
            if override and '...' in str(override):
                mcfg['api_key_override'] = existing.get('api_key_override')
        cur['models'] = new_models
    save_settings(cur)
    return jsonify({'ok': True})


@app.route('/api/evals', methods=['GET'])
def api_list_evals():
    return jsonify(list_evals())


@app.route('/api/evals/<eval_id>', methods=['GET'])
def api_get_eval(eval_id):
    obj = get_eval(eval_id)
    if obj is None:
        return jsonify({'error': 'not found'}), 404
    # Resolve system prompt for the UI to display
    obj['_resolved_system_prompt'] = resolve_system_prompt(obj)
    return jsonify(obj)


@app.route('/api/evals', methods=['POST'])
def api_create_eval():
    body = request.get_json() or {}
    save_eval(body)
    return jsonify({'ok': True, 'id': body.get('id')})


@app.route('/api/evals/<eval_id>', methods=['PUT'])
def api_update_eval(eval_id):
    body = request.get_json() or {}
    body['id'] = eval_id
    save_eval(body)
    return jsonify({'ok': True})


@app.route('/api/evals/<eval_id>', methods=['DELETE'])
def api_delete_eval(eval_id):
    return jsonify({'ok': delete_eval(eval_id)})


@app.route('/api/system_prompts', methods=['GET'])
def api_list_system_prompts():
    out = []
    for p in sorted(SYSTEM_PROMPTS_DIR.glob('*.txt')):
        out.append({'name': p.stem, 'size': p.stat().st_size})
    return jsonify(out)


@app.route('/api/system_prompts/<name>', methods=['GET'])
def api_get_system_prompt(name):
    p = SYSTEM_PROMPTS_DIR / f'{name}.txt'
    if not p.exists():
        return jsonify({'error': 'not found'}), 404
    return jsonify({'name': name, 'content': p.read_text(encoding='utf-8')})


@app.route('/api/run', methods=['POST'])
def api_run():
    """Run a chat against a model.

    Body:
      {
        eval_id: optional — load eval from disk
        model_id: optional — pick a saved model; defaults to active_model_id
        system_prompt: optional — override; otherwise from eval or model
        user_prompt: required if no eval_id
        temperature: optional (default 0.2)
        max_tokens: optional (default 8192)
        run_judge: bool — if true, run structural judge after
      }
    """
    body = request.get_json() or {}
    settings = load_settings()
    model_id = body.get('model_id') or settings.get('active_model_id')
    model_cfg = settings.get('models', {}).get(model_id)
    if not model_cfg:
        return jsonify({'error': f'unknown model_id: {model_id}'}), 400

    eval_obj = None
    if body.get('eval_id'):
        eval_obj = get_eval(body['eval_id'])
        if not eval_obj:
            return jsonify({'error': f'unknown eval_id'}), 404

    system_prompt = body.get('system_prompt')
    if system_prompt is None:
        system_prompt = resolve_system_prompt(eval_obj) if eval_obj else ''

    user_prompt = body.get('user_prompt')
    if user_prompt is None and eval_obj:
        user_prompt = eval_obj.get('user_prompt') or ''
    if user_prompt is None:
        return jsonify({'error': 'user_prompt required'}), 400

    temperature = float(body.get('temperature', 0.2))
    max_tokens = int(body.get('max_tokens', 8192))

    chat_result = llm_clients.chat(model_cfg, system_prompt, user_prompt,
                                   temperature=temperature, max_tokens=max_tokens)

    record = {
        'eval_id': eval_obj.get('id') if eval_obj else None,
        'eval_name': eval_obj.get('name') if eval_obj else None,
        'model_id': model_id,
        'model_label': model_cfg.get('label', model_id),
        'model_provider': model_cfg.get('provider'),
        'model_config_snapshot': {k: v for k, v in model_cfg.items() if k != 'api_key_override'},
        'system_prompt_chars': len(system_prompt),
        'user_prompt_chars': len(user_prompt),
        'temperature': temperature,
        'max_tokens': max_tokens,
        'ok': chat_result.get('ok'),
        'error': chat_result.get('error'),
        'content': chat_result.get('content'),
        'usage': chat_result.get('usage'),
        'elapsed_ms': chat_result.get('elapsed_ms'),
    }

    # Optional structural judge
    if body.get('run_judge') and eval_obj and chat_result.get('ok'):
        record['judge'] = judge_mod.structural_judge(
            chat_result.get('content', ''), eval_obj.get('expected') or {}
        )

    if body.get('save', True):
        record = save_result(record)
    return jsonify(record)


@app.route('/api/judge', methods=['POST'])
def api_judge():
    """Re-run judge against an existing result, or against ad-hoc text.

    Body:
      {
        result_id: optional — load existing result + its eval
        output_text: required if no result_id
        eval_id: optional — eval to grade against
        use_llm_judge: bool
      }
    """
    body = request.get_json() or {}
    output_text = body.get('output_text')
    eval_obj = None

    if body.get('result_id'):
        existing = get_result(body['result_id'])
        if existing:
            output_text = output_text or existing.get('content')
            if existing.get('eval_id'):
                eval_obj = get_eval(existing['eval_id'])

    if not eval_obj and body.get('eval_id'):
        eval_obj = get_eval(body['eval_id'])

    if output_text is None:
        return jsonify({'error': 'output_text required'}), 400

    expected = (eval_obj or {}).get('expected') or {}
    structural = judge_mod.structural_judge(output_text, expected)

    response = {'structural': structural}

    if body.get('use_llm_judge'):
        settings = load_settings()
        judge_model_id = body.get('judge_model_id') or settings.get('judge_model_id')
        judge_model_cfg = settings.get('models', {}).get(judge_model_id)
        if judge_model_cfg:
            llm_result = judge_mod.llm_judge(
                plan_text=(eval_obj or {}).get('user_prompt', ''),
                expected=expected,
                output_text=output_text,
                judge_model_config=judge_model_cfg,
                chat_fn=llm_clients.chat,
            )
            response['llm_judge'] = llm_result
        else:
            response['llm_judge'] = {'ok': False, 'error': f'unknown judge model {judge_model_id}'}

    return jsonify(response)


@app.route('/api/results', methods=['GET'])
def api_list_results():
    return jsonify(list_results(int(request.args.get('limit', 200))))


@app.route('/api/results/<rid>', methods=['GET'])
def api_get_result(rid):
    r = get_result(rid)
    if r is None:
        return jsonify({'error': 'not found'}), 404
    return jsonify(r)


@app.route('/api/results/<rid>', methods=['DELETE'])
def api_delete_result(rid):
    p = RESULTS_DIR / f'{rid}.json'
    if p.exists():
        p.unlink()
        return jsonify({'ok': True})
    return jsonify({'ok': False}), 404


# ---------- Main ----------

if __name__ == '__main__':
    # Load env from main aihub app .env so we can pick up API keys / endpoints
    try:
        from dotenv import load_dotenv
        load_dotenv(r'C:\src\aihub-client-ai-dev\.env')
    except Exception:
        pass
    port = int(os.getenv('MODEL_TESTER_PORT', '6099'))
    print(f'\nmodel_tester listening on http://localhost:{port}\n')
    app.run(host='0.0.0.0', port=port, debug=False, threaded=True)
