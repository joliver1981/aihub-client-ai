"""
Consolidated data preprocessing service.
Handles encoding, scaling, and data validation for all model types.
"""
import logging
import numpy as np
import pandas as pd
from sklearn.preprocessing import MinMaxScaler, StandardScaler, RobustScaler, OneHotEncoder
from sklearn.compose import ColumnTransformer

logger = logging.getLogger(__name__)

SCALER_MAP = {
    'minmax': MinMaxScaler,
    'standard': StandardScaler,
    'robust': RobustScaler,
}


def validate_dataframe(df, feature_columns, target_column):
    """Validate a DataFrame before processing. Returns list of issues or empty list."""
    issues = []

    if df.empty:
        issues.append('DataFrame is empty.')
        return issues

    if target_column not in df.columns:
        issues.append(f'Target column "{target_column}" not found in data.')

    missing_features = [c for c in feature_columns if c not in df.columns]
    if missing_features:
        issues.append(f'Missing feature columns: {missing_features}')

    if len(df) < 10:
        issues.append(f'Very few rows ({len(df)}). Minimum recommended is 10.')

    if target_column in df.columns:
        target_dtype = df[target_column].dtype
        if not np.issubdtype(target_dtype, np.number):
            issues.append(f'Target column "{target_column}" is not numeric (dtype={target_dtype}).')

    # Check for feature-target leakage (identical columns)
    if target_column in feature_columns:
        issues.append(f'Target column "{target_column}" is also listed as a feature column.')

    return issues


def detect_column_types(df, feature_columns):
    """Auto-detect categorical vs numerical columns from the selected features."""
    X = df[feature_columns]
    categorical = X.select_dtypes(include=['object', 'category']).columns.tolist()
    numerical = X.select_dtypes(include=['number']).columns.tolist()
    return categorical, numerical


def get_data_summary(df):
    """Return a summary dict for data preview in the UI."""
    summary = {
        'row_count': len(df),
        'column_count': len(df.columns),
        'columns': [],
    }
    for col in df.columns:
        col_info = {
            'name': col,
            'dtype': str(df[col].dtype),
            'missing': int(df[col].isnull().sum()),
            'missing_pct': round(df[col].isnull().mean() * 100, 1),
            'unique': int(df[col].nunique()),
        }
        if np.issubdtype(df[col].dtype, np.number):
            col_info['min'] = float(df[col].min()) if not df[col].isnull().all() else None
            col_info['max'] = float(df[col].max()) if not df[col].isnull().all() else None
            col_info['mean'] = round(float(df[col].mean()), 2) if not df[col].isnull().all() else None
            col_info['is_numeric'] = True
        else:
            col_info['top_values'] = df[col].value_counts().head(5).to_dict()
            col_info['is_numeric'] = False
        summary['columns'].append(col_info)
    return summary


class DataPreprocessor:
    """Handles all encoding and scaling for training and inference."""

    def __init__(self, scaler_type='minmax'):
        self.scaler_type = scaler_type
        self.feature_scaler = None
        self.target_scaler = None
        self.one_hot_encoder = None
        self.categorical_columns = None
        self.numerical_columns = None
        self.fitted = False

    def fit_transform(self, df, feature_columns, target_column,
                      categorical_columns=None, numerical_columns=None):
        """
        Fit encoders/scalers on data and return transformed X, y.

        Returns:
            X_encoded (np.ndarray): Transformed feature matrix
            y_scaled (np.ndarray): Scaled target array
            feature_names (list): Names of all encoded feature columns
        """
        X = df[feature_columns].copy()
        y = df[target_column].copy()

        # Detect column types if not provided
        if categorical_columns is None or numerical_columns is None:
            self.categorical_columns, self.numerical_columns = detect_column_types(df, feature_columns)
        else:
            self.categorical_columns = list(categorical_columns)
            self.numerical_columns = list(numerical_columns)

        logger.info(f'Categorical columns: {self.categorical_columns}')
        logger.info(f'Numerical columns: {self.numerical_columns}')

        # Build and fit the column transformer
        transformers = []
        if self.categorical_columns:
            transformers.append(
                ('cat', OneHotEncoder(handle_unknown='ignore', sparse_output=False), self.categorical_columns)
            )
        if self.numerical_columns:
            ScalerClass = SCALER_MAP.get(self.scaler_type, MinMaxScaler)
            self.feature_scaler = ScalerClass()
            transformers.append(
                ('num', self.feature_scaler, self.numerical_columns)
            )

        self.one_hot_encoder = ColumnTransformer(
            transformers=transformers,
            remainder='drop'
        )

        X_encoded = self.one_hot_encoder.fit_transform(X)
        feature_names = list(self.one_hot_encoder.get_feature_names_out())

        # Scale target
        self.target_scaler = MinMaxScaler()
        y_scaled = self.target_scaler.fit_transform(y.values.reshape(-1, 1)).flatten()

        self.fitted = True
        logger.info(f'Preprocessed: {X_encoded.shape[0]} rows, {X_encoded.shape[1]} features')

        return X_encoded, y_scaled, feature_names

    def transform(self, df, feature_columns, target_column=None):
        """
        Transform new data using already-fitted encoders/scalers.

        Returns:
            X_encoded (np.ndarray): Transformed feature matrix
            y_scaled (np.ndarray or None): Scaled target if target_column provided
        """
        if not self.fitted:
            raise RuntimeError('Preprocessor has not been fitted. Call fit_transform first.')

        X = df[feature_columns].copy()
        X_encoded = self.one_hot_encoder.transform(X)

        y_scaled = None
        if target_column is not None and target_column in df.columns:
            y = df[target_column].copy()
            y_scaled = self.target_scaler.transform(y.values.reshape(-1, 1)).flatten()

        return X_encoded, y_scaled

    def inverse_transform_target(self, y_scaled):
        """Reverse scaling on target values."""
        if self.target_scaler is None:
            return y_scaled
        return self.target_scaler.inverse_transform(
            np.array(y_scaled).reshape(-1, 1)
        ).flatten()

    def get_artifacts(self):
        """Return serializable artifacts for saving."""
        return {
            'one_hot_encoder': self.one_hot_encoder,
            'feature_scaler': self.feature_scaler,
            'target_scaler': self.target_scaler,
            'categorical_columns': self.categorical_columns,
            'numerical_columns': self.numerical_columns,
            'scaler_type': self.scaler_type,
        }

    @classmethod
    def from_artifacts(cls, artifacts):
        """Reconstruct a preprocessor from saved artifacts."""
        instance = cls(scaler_type=artifacts.get('scaler_type', 'minmax'))
        instance.one_hot_encoder = artifacts['one_hot_encoder']
        instance.feature_scaler = artifacts.get('feature_scaler')
        instance.target_scaler = artifacts['target_scaler']
        instance.categorical_columns = artifacts.get('categorical_columns', [])
        instance.numerical_columns = artifacts.get('numerical_columns', [])
        instance.fitted = True
        return instance
