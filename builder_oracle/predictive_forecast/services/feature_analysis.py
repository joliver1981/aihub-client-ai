"""
Feature importance analysis service.
Runs LightGBM-based feature importance and cumulative importance analysis.
"""
import logging
import os
import uuid
import numpy as np
import pandas as pd
from lightgbm import LGBMRegressor
from sklearn.model_selection import train_test_split
from concurrent.futures import ThreadPoolExecutor
import matplotlib
matplotlib.use('Agg')  # Non-interactive backend for server use
import matplotlib.pyplot as plt

from config.settings import Config

logger = logging.getLogger(__name__)

# Thread pool for async analysis
_executor = ThreadPoolExecutor(max_workers=2)

# Job tracking: {job_id: {'status': str, 'model_name': str, 'type': str}}
_jobs = {}


def _run_feature_importance(job_id, training_data_path, target_column, feature_columns, model_dir):
    """Background worker for feature importance analysis."""
    try:
        _jobs[job_id]['status'] = 'Loading training data...'
        df = pd.read_csv(training_data_path)
        X = df[feature_columns]
        y = df[target_column]

        # One-hot encode for LightGBM
        X_encoded = pd.get_dummies(X)
        feature_names = list(X_encoded.columns)
        X_np = X_encoded.values
        y_np = y.values

        n_iterations = Config.FEATURE_IMPORTANCE_ITERATIONS
        feature_importance_values = np.zeros(len(feature_names))

        _jobs[job_id]['status'] = 'Training gradient boosting model...'

        for i in range(n_iterations):
            model = LGBMRegressor(n_estimators=1000, learning_rate=0.05, verbose=-1, n_jobs=-1)
            model.fit(X_np, y_np)
            feature_importance_values += model.feature_importances_ / n_iterations

        # Build importance DataFrame
        importances = pd.DataFrame({
            'feature': feature_names,
            'importance': feature_importance_values,
        }).sort_values('importance', ascending=False).reset_index(drop=True)

        importances['normalized_importance'] = importances['importance'] / importances['importance'].sum()
        importances['cumulative_importance'] = importances['normalized_importance'].cumsum()

        _jobs[job_id]['status'] = 'Generating plots...'

        # Plot feature importance (top N)
        plot_n = min(15, len(importances))
        top = importances.head(plot_n)

        plt.figure(figsize=(10, 6))
        ax = plt.subplot()
        ax.barh(
            range(plot_n - 1, -1, -1),
            top['normalized_importance'].values,
            align='center', edgecolor='k', color='#4C72B0'
        )
        ax.set_yticks(range(plot_n - 1, -1, -1))
        ax.set_yticklabels(top['feature'].values, size=10)
        ax.set_xlabel('Normalized Importance', size=13)
        ax.set_title('Feature Importances', size=15)
        plt.tight_layout()
        plt.savefig(os.path.join(model_dir, 'feature_importance.png'), dpi=120)
        plt.close()

        # Plot cumulative importance
        threshold = Config.CUMULATIVE_IMPORTANCE_THRESHOLD
        plt.figure(figsize=(8, 5))
        plt.plot(range(1, len(importances) + 1), importances['cumulative_importance'].values, 'r-', linewidth=2)
        plt.xlabel('Number of Features', size=13)
        plt.ylabel('Cumulative Importance', size=13)
        plt.title('Cumulative Feature Importance', size=15)

        importance_index = np.min(np.where(importances['cumulative_importance'].values > threshold))
        plt.axvline(x=importance_index + 1, linestyle='--', color='blue', alpha=0.7,
                     label=f'{threshold:.0%} threshold ({importance_index + 1} features)')
        plt.legend()
        plt.tight_layout()
        plt.savefig(os.path.join(model_dir, 'cumulative_importance.png'), dpi=120)
        plt.close()

        # Save importance data as CSV
        importances.to_csv(os.path.join(model_dir, 'feature_importances.csv'), index=False)

        # Zero importance features
        zero_importance = importances[importances['importance'] == 0.0]['feature'].tolist()
        low_importance = importances[importances['cumulative_importance'] > threshold]['feature'].tolist()

        _jobs[job_id]['status'] = 'complete'
        _jobs[job_id]['result'] = {
            'zero_importance_count': len(zero_importance),
            'zero_importance_features': zero_importance,
            'low_importance_count': len(low_importance),
            'features_for_threshold': importance_index + 1,
            'total_features': len(importances),
        }
        logger.info(f'Feature analysis complete for job {job_id}')

    except Exception as e:
        logger.error(f'Feature analysis failed for job {job_id}: {e}', exc_info=True)
        _jobs[job_id]['status'] = f'Error: {str(e)}'


def start_analysis(model_name):
    """
    Start an async feature importance analysis.
    Returns a job_id to poll for status.
    """
    import json
    model_dir = os.path.join(Config.MODEL_FOLDER, model_name)
    config_path = os.path.join(model_dir, 'config.json')
    training_data_path = os.path.join(model_dir, 'training_data.csv')

    if not os.path.exists(config_path) or not os.path.exists(training_data_path):
        return None, 'Model config or training data not found.'

    with open(config_path, 'r') as f:
        config = json.load(f)

    feature_columns = config['feature_columns']
    target_column = config['target_column']

    job_id = str(uuid.uuid4())[:8]
    _jobs[job_id] = {
        'status': 'Queued...',
        'model_name': model_name,
        'result': None,
    }

    _executor.submit(_run_feature_importance, job_id, training_data_path, target_column, feature_columns, model_dir)
    logger.info(f'Started feature analysis job {job_id} for model {model_name}')
    return job_id, None


def get_job_status(job_id):
    """Get the status of an analysis job."""
    job = _jobs.get(job_id)
    if job is None:
        return {'status': 'not_found'}
    return {
        'status': job['status'],
        'result': job.get('result'),
        'model_name': job.get('model_name'),
        'complete': job['status'] == 'complete' or job['status'].startswith('Error'),
    }


def has_analysis_images(model_name):
    """Check if feature importance images exist for a model."""
    model_dir = os.path.join(Config.MODEL_FOLDER, model_name)
    return {
        'feature_importance': os.path.exists(os.path.join(model_dir, 'feature_importance.png')),
        'cumulative_importance': os.path.exists(os.path.join(model_dir, 'cumulative_importance.png')),
    }
