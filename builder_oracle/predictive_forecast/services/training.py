"""
Model training service supporting multiple ML algorithms.
Supports: Neural Network (Keras), XGBoost, Random Forest, LightGBM, Gradient Boosting.
"""
import logging
import os
import json
import time
import shutil
import numpy as np
import pandas as pd
import joblib
from sklearn.model_selection import train_test_split, KFold, cross_val_score
from sklearn.metrics import mean_squared_error, r2_score, mean_absolute_error
from sklearn.ensemble import RandomForestRegressor, GradientBoostingRegressor
from lightgbm import LGBMRegressor

from config.settings import Config
from services.preprocessing import DataPreprocessor, validate_dataframe

logger = logging.getLogger(__name__)

# Algorithm registry
ALGORITHMS = {
    'neural_network': 'Neural Network (Keras DNN)',
    'xgboost': 'XGBoost Regressor',
    'random_forest': 'Random Forest Regressor',
    'lightgbm': 'LightGBM Regressor',
    'gradient_boosting': 'Gradient Boosting Regressor',
}


def get_available_algorithms():
    """Return dict of available algorithm keys and display names."""
    algos = dict(ALGORITHMS)
    try:
        import xgboost  # noqa: F401
    except ImportError:
        algos.pop('xgboost', None)
    return algos


def _build_keras_model(input_dim, config=None):
    """Build a Keras Sequential DNN for regression."""
    import tensorflow as tf
    from tensorflow.keras.models import Sequential
    from tensorflow.keras.layers import Dense, Dropout, BatchNormalization

    cfg = config or {}
    layer_1 = cfg.get('layer_1', Config.DNN_LAYER_1)
    layer_2 = cfg.get('layer_2', Config.DNN_LAYER_2)
    dropout = cfg.get('dropout', Config.DNN_DROPOUT)

    model = Sequential([
        Dense(layer_1, activation='relu', input_shape=(input_dim,)),
        BatchNormalization(),
        Dropout(dropout),
        Dense(layer_2, activation='relu'),
        BatchNormalization(),
        Dropout(dropout),
        Dense(32, activation='relu'),
        Dropout(dropout * 0.5),
        Dense(1, activation='linear'),
    ])
    model.compile(optimizer='adam', loss='mean_squared_error', metrics=['mae'])
    return model


def _train_keras(X_train, y_train, X_val, y_val, config=None):
    """Train a Keras DNN model."""
    from tensorflow.keras.callbacks import ModelCheckpoint, EarlyStopping, ReduceLROnPlateau
    import tempfile

    cfg = config or {}
    epochs = cfg.get('epochs', Config.DNN_EPOCHS)
    batch_size = cfg.get('batch_size', Config.DNN_BATCH_SIZE)
    patience = cfg.get('patience', Config.DNN_PATIENCE)
    use_kfold = cfg.get('use_kfold', Config.DNN_USE_KFOLD)
    num_folds = cfg.get('num_folds', Config.DNN_NUM_FOLDS)

    if use_kfold:
        logger.info(f'Training Keras DNN with {num_folds}-fold cross-validation')
        X_all = np.vstack([X_train, X_val])
        y_all = np.concatenate([y_train, y_val])
        kf = KFold(n_splits=num_folds, shuffle=True, random_state=Config.DEFAULT_RANDOM_STATE)
        fold_metrics = []

        best_model = None
        best_val_loss = float('inf')

        for fold, (train_idx, val_idx) in enumerate(kf.split(X_all)):
            logger.info(f'Fold {fold + 1}/{num_folds}')
            X_fold_train, X_fold_val = X_all[train_idx], X_all[val_idx]
            y_fold_train, y_fold_val = y_all[train_idx], y_all[val_idx]

            model = _build_keras_model(X_fold_train.shape[1], cfg)

            tmp_path = os.path.join(tempfile.gettempdir(), f'fc_fold_{fold}.keras')
            callbacks = [
                ModelCheckpoint(tmp_path, monitor='val_loss', save_best_only=True, verbose=0),
                EarlyStopping(monitor='val_loss', patience=patience, restore_best_weights=True, verbose=0),
                ReduceLROnPlateau(monitor='val_loss', factor=0.5, patience=max(3, patience // 3), verbose=0),
            ]

            history = model.fit(
                X_fold_train, y_fold_train,
                epochs=epochs, batch_size=batch_size,
                validation_data=(X_fold_val, y_fold_val),
                callbacks=callbacks, verbose=0,
            )

            val_loss = min(history.history['val_loss'])
            fold_metrics.append({
                'fold': fold + 1,
                'val_loss': round(val_loss, 6),
                'epochs_run': len(history.history['loss']),
            })

            if val_loss < best_val_loss:
                best_val_loss = val_loss
                import tensorflow as tf
                best_model = tf.keras.models.load_model(tmp_path)

            # Cleanup temp file
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

        return best_model, {'fold_metrics': fold_metrics}

    else:
        logger.info('Training Keras DNN (single split)')
        model = _build_keras_model(X_train.shape[1], cfg)

        tmp_path = os.path.join(Config.MODEL_FOLDER, '_tmp_best.keras')
        callbacks = [
            ModelCheckpoint(tmp_path, monitor='val_loss', save_best_only=True, verbose=0),
            EarlyStopping(monitor='val_loss', patience=patience, restore_best_weights=True, verbose=0),
            ReduceLROnPlateau(monitor='val_loss', factor=0.5, patience=max(3, patience // 3), verbose=0),
        ]

        history = model.fit(
            X_train, y_train,
            epochs=epochs, batch_size=batch_size,
            validation_data=(X_val, y_val),
            callbacks=callbacks, verbose=0,
        )

        import tensorflow as tf
        if os.path.exists(tmp_path):
            model = tf.keras.models.load_model(tmp_path)
            os.remove(tmp_path)

        return model, {
            'epochs_run': len(history.history['loss']),
            'final_train_loss': round(history.history['loss'][-1], 6),
            'final_val_loss': round(history.history['val_loss'][-1], 6),
        }


def _train_sklearn_model(model_class, X_train, y_train, X_val, y_val, params=None):
    """Train a scikit-learn compatible model (RF, GB, LGBM, XGB)."""
    params = params or {}
    model = model_class(**params)

    # Combine for cross-validation score
    X_all = np.vstack([X_train, X_val])
    y_all = np.concatenate([y_train, y_val])

    cv_scores = cross_val_score(model, X_all, y_all, cv=5, scoring='neg_mean_squared_error')

    # Fit on full train set
    model.fit(X_train, y_train)

    extra_info = {
        'cv_mse_mean': round(-cv_scores.mean(), 6),
        'cv_mse_std': round(cv_scores.std(), 6),
    }
    return model, extra_info


def train_model(df, feature_columns, target_column, model_name, algorithm='neural_network',
                scaler_type='minmax', hyperparams=None):
    """
    Main training entry point.

    Args:
        df: Training DataFrame
        feature_columns: List of feature column names
        target_column: Target column name
        model_name: Name to save the model as
        algorithm: One of ALGORITHMS keys
        scaler_type: 'minmax', 'standard', or 'robust'
        hyperparams: Optional dict of algorithm-specific hyperparameters

    Returns:
        dict with training results and metrics
    """
    start_time = time.time()
    hyperparams = hyperparams or {}

    # Validate
    issues = validate_dataframe(df, feature_columns, target_column)
    if issues:
        return {'success': False, 'errors': issues}

    # Preprocess
    preprocessor = DataPreprocessor(scaler_type=scaler_type)
    X, y, feature_names = preprocessor.fit_transform(df, feature_columns, target_column)

    # Split
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=Config.DEFAULT_TEST_SIZE, random_state=Config.DEFAULT_RANDOM_STATE
    )

    logger.info(f'Training {algorithm} model "{model_name}" on {X_train.shape[0]} samples, {X_train.shape[1]} features')

    # Train
    extra_info = {}
    if algorithm == 'neural_network':
        model, extra_info = _train_keras(X_train, y_train, X_test, y_test, hyperparams)
    elif algorithm == 'xgboost':
        import xgboost as xgb
        defaults = {'n_estimators': 500, 'max_depth': 6, 'learning_rate': 0.05,
                     'subsample': 0.8, 'colsample_bytree': 0.8, 'random_state': Config.DEFAULT_RANDOM_STATE}
        defaults.update(hyperparams)
        model, extra_info = _train_sklearn_model(xgb.XGBRegressor, X_train, y_train, X_test, y_test, defaults)
    elif algorithm == 'random_forest':
        defaults = {'n_estimators': 300, 'max_depth': None, 'min_samples_split': 5,
                     'min_samples_leaf': 2, 'random_state': Config.DEFAULT_RANDOM_STATE, 'n_jobs': -1}
        defaults.update(hyperparams)
        model, extra_info = _train_sklearn_model(RandomForestRegressor, X_train, y_train, X_test, y_test, defaults)
    elif algorithm == 'lightgbm':
        defaults = {'n_estimators': 500, 'max_depth': -1, 'learning_rate': 0.05,
                     'num_leaves': 31, 'random_state': Config.DEFAULT_RANDOM_STATE, 'verbose': -1, 'n_jobs': -1}
        defaults.update(hyperparams)
        model, extra_info = _train_sklearn_model(LGBMRegressor, X_train, y_train, X_test, y_test, defaults)
    elif algorithm == 'gradient_boosting':
        defaults = {'n_estimators': 300, 'max_depth': 5, 'learning_rate': 0.05,
                     'subsample': 0.8, 'random_state': Config.DEFAULT_RANDOM_STATE}
        defaults.update(hyperparams)
        model, extra_info = _train_sklearn_model(GradientBoostingRegressor, X_train, y_train, X_test, y_test, defaults)
    else:
        return {'success': False, 'errors': [f'Unknown algorithm: {algorithm}']}

    # Evaluate
    if algorithm == 'neural_network':
        y_train_pred = model.predict(X_train, verbose=0).flatten()
        y_test_pred = model.predict(X_test, verbose=0).flatten()
    else:
        y_train_pred = model.predict(X_train)
        y_test_pred = model.predict(X_test)

    metrics = {
        'train_mse': round(float(mean_squared_error(y_train, y_train_pred)), 6),
        'test_mse': round(float(mean_squared_error(y_test, y_test_pred)), 6),
        'train_r2': round(float(r2_score(y_train, y_train_pred)), 4),
        'test_r2': round(float(r2_score(y_test, y_test_pred)), 4),
        'train_mae': round(float(mean_absolute_error(y_train, y_train_pred)), 6),
        'test_mae': round(float(mean_absolute_error(y_test, y_test_pred)), 6),
    }

    # Save model artifacts
    model_dir = os.path.join(Config.MODEL_FOLDER, model_name)
    os.makedirs(model_dir, exist_ok=True)

    if algorithm == 'neural_network':
        model.save(os.path.join(model_dir, 'model.keras'))
    else:
        joblib.dump(model, os.path.join(model_dir, 'model.pkl'))

    # Save preprocessor artifacts
    joblib.dump(preprocessor.get_artifacts(), os.path.join(model_dir, 'preprocessor.pkl'))

    # Save training data for feature importance analysis
    df[feature_columns + [target_column]].to_csv(
        os.path.join(model_dir, 'training_data.csv'), index=False
    )

    # Save config
    config = {
        'model_name': model_name,
        'algorithm': algorithm,
        'algorithm_display': ALGORITHMS.get(algorithm, algorithm),
        'feature_columns': feature_columns,
        'target_column': target_column,
        'categorical_columns': preprocessor.categorical_columns,
        'numerical_columns': preprocessor.numerical_columns,
        'scaler_type': scaler_type,
        'feature_names_encoded': feature_names,
        'metrics': metrics,
        'extra_info': extra_info,
        'hyperparams': hyperparams,
        'training_rows': len(df),
        'training_features': len(feature_names),
        'timestamp': time.strftime('%Y-%m-%d %H:%M:%S'),
    }
    with open(os.path.join(model_dir, 'config.json'), 'w') as f:
        json.dump(config, f, indent=2, default=str)

    elapsed = round(time.time() - start_time, 1)
    logger.info(f'Model "{model_name}" trained in {elapsed}s. Test R2={metrics["test_r2"]}, Test MSE={metrics["test_mse"]}')

    return {
        'success': True,
        'model_name': model_name,
        'algorithm': algorithm,
        'metrics': metrics,
        'extra_info': extra_info,
        'elapsed_seconds': elapsed,
    }


def delete_model(model_name):
    """Delete a saved model and all its artifacts."""
    model_dir = os.path.join(Config.MODEL_FOLDER, model_name)
    if os.path.exists(model_dir):
        shutil.rmtree(model_dir)
        logger.info(f'Deleted model: {model_name}')
        return True
    return False


def list_models():
    """List all saved models with their metadata."""
    models = []
    model_root = Config.MODEL_FOLDER
    if not os.path.exists(model_root):
        return models

    for name in sorted(os.listdir(model_root)):
        model_dir = os.path.join(model_root, name)
        if not os.path.isdir(model_dir):
            continue
        config_path = os.path.join(model_dir, 'config.json')
        info = {'name': name, 'has_config': False}
        if os.path.exists(config_path):
            try:
                with open(config_path, 'r') as f:
                    config = json.load(f)
                info['has_config'] = True
                info['algorithm'] = config.get('algorithm_display', config.get('algorithm', 'Unknown'))
                info['target_column'] = config.get('target_column', 'N/A')
                info['metrics'] = config.get('metrics', {})
                info['timestamp'] = config.get('timestamp', 'N/A')
                info['training_rows'] = config.get('training_rows', 'N/A')
            except Exception:
                pass
        models.append(info)
    return models


def get_model_config(model_name):
    """Load a model's config.json."""
    config_path = os.path.join(Config.MODEL_FOLDER, model_name, 'config.json')
    if os.path.exists(config_path):
        with open(config_path, 'r') as f:
            return json.load(f)
    return None
