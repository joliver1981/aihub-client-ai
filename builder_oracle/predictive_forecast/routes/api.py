"""
PredictiveForecast - Unified REST API
All endpoints return JSON. The SPA frontend consumes these exclusively.
"""
import os
import logging
import pandas as pd
from flask import Blueprint, request, jsonify, send_from_directory
from werkzeug.utils import secure_filename

from config.settings import Config
from services.preprocessing import get_data_summary, detect_column_types
from services.training import train_model, get_available_algorithms, list_models, get_model_config, delete_model
from services.prediction import test_model
from services.feature_analysis import start_analysis, get_job_status, has_analysis_images

logger = logging.getLogger(__name__)

api_bp = Blueprint('api', __name__, url_prefix='/api')

ALLOWED_EXTENSIONS = {'csv', 'xlsx', 'xls'}


def _allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def _read_df(filepath):
    if filepath.endswith(('.xlsx', '.xls')):
        return pd.read_excel(filepath)
    return pd.read_csv(filepath)


# ── Health ───────────────────────────────────────────────────────────────────
@api_bp.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok', 'service': 'PredictiveForecast'})


# ── Algorithms ───────────────────────────────────────────────────────────────
@api_bp.route('/algorithms', methods=['GET'])
def algorithms():
    return jsonify(get_available_algorithms())


# ── Upload & Profile ─────────────────────────────────────────────────────────
@api_bp.route('/upload', methods=['POST'])
def upload():
    """Upload a file and return profiling data (column stats, preview rows)."""
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400

    file = request.files['file']
    if file.filename == '' or not _allowed_file(file.filename):
        return jsonify({'error': 'Invalid file. Accepted: CSV, XLSX, XLS'}), 400

    filename = secure_filename(file.filename)
    filepath = os.path.join(Config.UPLOAD_FOLDER, filename)
    file.save(filepath)

    df = _read_df(filepath)
    summary = get_data_summary(df)
    columns = df.columns.tolist()
    categorical, numerical = detect_column_types(df, columns)

    return jsonify({
        'filepath': filepath,
        'filename': filename,
        'summary': summary,
        'columns': columns,
        'categorical_columns': categorical,
        'numerical_columns': numerical,
        'preview': df.head(20).fillna('').to_dict(orient='records'),
    })


# ── Train ────────────────────────────────────────────────────────────────────
@api_bp.route('/train', methods=['POST'])
def train():
    """
    Train a model. Accepts JSON:
    {
        filepath, model_name, algorithm, scaler_type,
        feature_columns[], target_column, hyperparams{}
    }
    """
    data = request.get_json()
    if not data:
        return jsonify({'error': 'JSON body required'}), 400

    required = ['filepath', 'model_name', 'feature_columns', 'target_column']
    missing = [k for k in required if not data.get(k)]
    if missing:
        return jsonify({'error': f'Missing required fields: {missing}'}), 400

    df = _read_df(data['filepath'])

    result = train_model(
        df=df,
        feature_columns=data['feature_columns'],
        target_column=data['target_column'],
        model_name=data['model_name'].replace(' ', '_').replace('/', '_').replace('\\', '_'),
        algorithm=data.get('algorithm', 'neural_network'),
        scaler_type=data.get('scaler_type', 'minmax'),
        hyperparams=data.get('hyperparams'),
    )

    status_code = 200 if result.get('success') else 400
    return jsonify(result), status_code


# ── Test ─────────────────────────────────────────────────────────────────────
@api_bp.route('/test', methods=['POST'])
def test():
    """
    Test a model. Accepts multipart form with 'file' + 'model_name',
    OR JSON with 'filepath' + 'model_name'.
    """
    if 'file' in request.files and request.files['file'].filename:
        file = request.files['file']
        model_name = request.form.get('model_name')
        filename = secure_filename(file.filename)
        filepath = os.path.join(Config.UPLOAD_FOLDER, filename)
        file.save(filepath)
    else:
        data = request.get_json()
        if not data:
            return jsonify({'error': 'Provide file upload or JSON with filepath'}), 400
        filepath = data.get('filepath')
        model_name = data.get('model_name')

    if not model_name or not filepath:
        return jsonify({'error': 'model_name and filepath/file required'}), 400

    try:
        df = _read_df(filepath)
        result = test_model(model_name, df)
        images = has_analysis_images(model_name)
        result['images'] = images
        return jsonify(result)
    except Exception as e:
        logger.error(f'Test failed: {e}', exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 400


# ── Models CRUD ──────────────────────────────────────────────────────────────
@api_bp.route('/models', methods=['GET'])
def models_list():
    return jsonify(list_models())


@api_bp.route('/models/<model_name>', methods=['GET'])
def models_get(model_name):
    config = get_model_config(model_name)
    if config is None:
        return jsonify({'error': 'Model not found'}), 404
    return jsonify(config)


@api_bp.route('/models/<model_name>', methods=['DELETE'])
def models_delete(model_name):
    if delete_model(model_name):
        return jsonify({'success': True})
    return jsonify({'error': 'Model not found'}), 404


# ── Feature Analysis ─────────────────────────────────────────────────────────
@api_bp.route('/analyze', methods=['POST'])
def analyze():
    data = request.get_json()
    if not data or 'model_name' not in data:
        return jsonify({'error': 'model_name required'}), 400
    job_id, error = start_analysis(data['model_name'])
    if error:
        return jsonify({'error': error}), 400
    return jsonify({'job_id': job_id, 'status': 'started'})


@api_bp.route('/analyze/status/<job_id>', methods=['GET'])
def analyze_status(job_id):
    return jsonify(get_job_status(job_id))


# ── Static model files (images, CSV) ────────────────────────────────────────
@api_bp.route('/model-files/<path:filename>', methods=['GET'])
def model_files(filename):
    allowed_ext = {'.png', '.jpg', '.csv', '.json'}
    ext = os.path.splitext(filename)[1].lower()
    if ext not in allowed_ext:
        return 'Forbidden', 403
    return send_from_directory(Config.MODEL_FOLDER, filename)
