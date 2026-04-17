"""
Prediction and model testing service.
Handles inference, confidence intervals, and accuracy classification.
"""
import logging
import os
import numpy as np
import pandas as pd
import joblib
from sklearn.metrics import mean_squared_error, r2_score, mean_absolute_error
from scipy.stats import norm

from config.settings import Config
from services.preprocessing import DataPreprocessor

logger = logging.getLogger(__name__)


def _load_model_and_preprocessor(model_name):
    """Load a trained model and its preprocessor artifacts."""
    model_dir = os.path.join(Config.MODEL_FOLDER, model_name)
    config_path = os.path.join(model_dir, 'config.json')
    preprocessor_path = os.path.join(model_dir, 'preprocessor.pkl')

    import json
    with open(config_path, 'r') as f:
        config = json.load(f)

    algorithm = config.get('algorithm', 'neural_network')

    # Load model
    if algorithm == 'neural_network':
        import tensorflow as tf
        model_path = os.path.join(model_dir, 'model.keras')
        model = tf.keras.models.load_model(model_path)
    else:
        model_path = os.path.join(model_dir, 'model.pkl')
        model = joblib.load(model_path)

    # Load preprocessor
    artifacts = joblib.load(preprocessor_path)
    preprocessor = DataPreprocessor.from_artifacts(artifacts)

    return model, preprocessor, config


def mc_dropout_predict(model, X, n_iter=None):
    """Monte Carlo Dropout prediction for uncertainty estimation (Keras models only)."""
    import tensorflow as tf
    n_iter = n_iter or Config.MC_DROPOUT_ITERATIONS
    predictions = np.array([model(X, training=True) for _ in range(n_iter)])
    mean_pred = np.mean(predictions, axis=0).flatten()
    std_pred = np.std(predictions, axis=0).flatten()
    return mean_pred, std_pred


def _compute_confidence(model, X, algorithm, y_pred, y_actual=None):
    """
    Compute confidence intervals and categories.
    Uses MC Dropout for neural networks, residual-based for tree models.
    """
    confidence_level = Config.CONFIDENCE_LEVEL
    z_score = norm.ppf(0.5 + confidence_level / 2)

    if algorithm == 'neural_network':
        mean_pred, std_pred = mc_dropout_predict(model, X)
        lower_bound = mean_pred - z_score * std_pred
        upper_bound = mean_pred + z_score * std_pred
    else:
        # For tree-based models, use residual-based confidence
        if y_actual is not None:
            residuals = y_pred - y_actual
            std_residual = np.std(residuals)
        else:
            std_residual = np.std(y_pred) * 0.1  # Fallback estimate

        lower_bound = y_pred - z_score * std_residual
        upper_bound = y_pred + z_score * std_residual
        std_pred = np.full_like(y_pred, std_residual)

    # Categorize confidence using percentile-based thresholds
    if len(std_pred) > 1:
        p25 = np.percentile(std_pred, 25)
        p75 = np.percentile(std_pred, 75)
    else:
        p25, p75 = 0.01, 0.03

    categories = []
    for std in std_pred:
        if std <= p25:
            categories.append('High')
        elif std <= p75:
            categories.append('Medium')
        else:
            categories.append('Low')

    return lower_bound, upper_bound, categories


def _categorize_accuracy(pct):
    """Classify prediction accuracy."""
    if pct > 95:
        return 'Excellent'
    elif pct > 85:
        return 'Good'
    elif pct > 70:
        return 'Fair'
    else:
        return 'Poor'


def test_model(model_name, df):
    """
    Test a trained model against new data.

    Args:
        model_name: Name of the saved model
        df: Test DataFrame containing feature and target columns

    Returns:
        dict with test results, metrics, per-row predictions, and summary
    """
    model, preprocessor, config = _load_model_and_preprocessor(model_name)
    algorithm = config.get('algorithm', 'neural_network')
    feature_columns = config['feature_columns']
    target_column = config['target_column']

    # Preprocess test data
    X_test, y_test_scaled = preprocessor.transform(df, feature_columns, target_column)
    y_actual = df[target_column].values

    # Predict
    if algorithm == 'neural_network':
        y_pred_scaled = model.predict(X_test, verbose=0).flatten()
    else:
        y_pred_scaled = model.predict(X_test)

    # Inverse transform predictions to original scale
    y_pred_original = preprocessor.inverse_transform_target(y_pred_scaled)

    # Metrics on scaled data
    metrics = {
        'mse': round(float(mean_squared_error(y_test_scaled, y_pred_scaled)), 6),
        'r2': round(float(r2_score(y_test_scaled, y_pred_scaled)), 4),
        'mae': round(float(mean_absolute_error(y_test_scaled, y_pred_scaled)), 6),
        'total_delta': round(float(np.sum(np.abs(y_actual - y_pred_original))), 2),
    }

    # Confidence intervals
    lower_bound, upper_bound, confidence_cats = _compute_confidence(
        model, X_test, algorithm, y_pred_scaled, y_test_scaled
    )

    # Build results table
    results_df = df[feature_columns].copy()
    results_df['Actual'] = y_actual
    results_df['Predicted'] = np.round(y_pred_original, 2)

    # Percentage accuracy (handle zero actuals)
    with np.errstate(divide='ignore', invalid='ignore'):
        pct = np.where(
            y_actual != 0,
            (1 - np.abs(y_actual - y_pred_original) / np.abs(y_actual)) * 100,
            np.where(y_pred_original == 0, 100.0, 0.0)
        )
    results_df['Percentage'] = np.round(pct, 2)
    results_df['Accuracy'] = [_categorize_accuracy(p) for p in pct]
    results_df['LowerBound'] = np.round(lower_bound, 4)
    results_df['UpperBound'] = np.round(upper_bound, 4)
    results_df['Confidence'] = confidence_cats

    # Sort by accuracy
    accuracy_order = {'Excellent': 1, 'Good': 2, 'Fair': 3, 'Poor': 4}
    results_df['_sort'] = results_df['Accuracy'].map(accuracy_order)
    results_df = results_df.sort_values('_sort').drop(columns=['_sort'])

    # Reorder columns
    priority_cols = ['Accuracy', 'Predicted', 'Actual', 'Percentage', 'LowerBound', 'UpperBound', 'Confidence']
    other_cols = [c for c in results_df.columns if c not in priority_cols]
    results_df = results_df[priority_cols + other_cols]

    # Summary counts
    summary = results_df['Accuracy'].value_counts().to_dict()
    summary = {k: summary.get(k, 0) for k in ['Excellent', 'Good', 'Fair', 'Poor']}

    overall_confidence = round(float((upper_bound - lower_bound).mean()), 4)

    return {
        'success': True,
        'model_name': model_name,
        'algorithm': config.get('algorithm_display', algorithm),
        'metrics': metrics,
        'summary': summary,
        'overall_confidence': overall_confidence,
        'results': results_df.to_dict(orient='records'),
        'columns': list(results_df.columns),
    }
