"""
etl/gold_monthly_model.py
Train monthly production forecasting models
"""
from pathlib import Path
import sys
from typing import Dict, List

import numpy as np
import pandas as pd
from sklearn.linear_model import SGDClassifier, SGDRegressor

sys.path.append(str(Path(__file__).parent.parent))
from utils.db import get_cursor
from utils.logger import get_logger

logger = get_logger(__name__)

FEATURE_COLUMNS = [
    "month_sin",
    "month_cos",
    "avg_renewable_share_pct",
    "growth_rate_pct",
]


def cyclical_month_features(month: int) -> Dict[str, float]:
    radians = 2 * np.pi * (month - 1) / 12.0
    return {
        "month_sin": float(np.sin(radians)),
        "month_cos": float(np.cos(radians)),
    }


def load_monthly_history() -> pd.DataFrame:
    query = """
    SELECT year, month, month_name, total_production_gwh,
           avg_renewable_share_pct, growth_rate_pct
    FROM gold.monthly_production
    ORDER BY year, month
    """
    with get_cursor(dict_cursor=True) as cur:
        cur.execute(query)
        rows = cur.fetchall()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["month_number"] = df["month"].astype(int)
    cyclical = df["month_number"].apply(cyclical_month_features).tolist()
    df = pd.concat([df, pd.DataFrame(cyclical)], axis=1)
    # Fill NaN values with 0 for features
    df.fillna(0, inplace=True)
    return df


def train_online_model(history: pd.DataFrame) -> SGDRegressor:
    model = SGDRegressor(
        max_iter=1,
        tol=None,
        penalty="l2",
        learning_rate="invscaling",
        eta0=0.001,
        random_state=42,
        warm_start=True,
    )

    if history.shape[0] < 2:
        # Warm start with the first row only if there is no history to train on.
        initial = history.iloc[[0]]
        model.partial_fit(initial[FEATURE_COLUMNS], initial["total_production_gwh"])
        return model

    train_df = history.iloc[:-1]
    X = train_df[FEATURE_COLUMNS]
    y = train_df["total_production_gwh"]
    model.partial_fit(X, y)
    return model


def make_reason_label(row: pd.Series) -> str:
    if int(row["month"]) in [6, 7, 8]:
        return "summer_peak"
    if int(row["month"]) in [12, 1, 2]:
        return "winter_dip"

    growth_value = float(row.get("growth_rate_pct", 0.0) or 0.0)
    renew_value = float(row.get("avg_renewable_share_pct", 0.0) or 0.0)

    if growth_value >= 1.0:
        return "positive_growth"
    if growth_value < 0:
        return "negative_growth"
    if renew_value >= 50:
        return "strong_renewables"
    return "seasonal_pattern"


def train_reason_model(history: pd.DataFrame) -> SGDClassifier:
    if history.empty:
        raise RuntimeError("No history available for training the reason model")

    reason_df = history.copy()
    reason_df["reason_label"] = reason_df.apply(make_reason_label, axis=1)
    X = reason_df[FEATURE_COLUMNS]
    y = reason_df["reason_label"]

    classifier = SGDClassifier(
        max_iter=1000,
        tol=1e-3,
        loss="log_loss",
        random_state=42,
    )
    classifier.fit(X, y)
    return classifier


def predict_reason_category(model: SGDClassifier, row: pd.Series) -> str:
    X = row[FEATURE_COLUMNS].to_frame().T
    predicted_category = model.predict(X)[0]
    return str(predicted_category)


def describe_prediction_reason(category: str, predicted: float) -> str:
    REASON_TEMPLATES = {
        "summer_peak": "Summer season trend is strong, lifting the forecast.",
        "winter_dip": "Winter season typically reduces production, which lowers the forecast.",
        "positive_growth": "A positive growth trend is supporting the forecast.",
        "negative_growth": "A negative growth trend is limiting the forecast.",
        "strong_renewables": "High renewable share supports a stable, resilient forecast.",
        "seasonal_pattern": "Seasonal patterns and production trends together define the forecast.",
    }
    return REASON_TEMPLATES.get(category, REASON_TEMPLATES["seasonal_pattern"])


def run_monthly_model_training() -> tuple[SGDRegressor, SGDClassifier, pd.Series, str, float]:
    """Train models and generate prediction for latest data"""
    logger.info("Starting monthly model training")

    history_df = load_monthly_history()
    if history_df.empty:
        raise RuntimeError("Unable to load gold.monthly_production history")

    model = train_online_model(history_df)
    reason_model = train_reason_model(history_df)
    latest_row = history_df.iloc[-1]
    latest_features = latest_row[FEATURE_COLUMNS].to_frame().T
    prediction = float(model.predict(latest_features)[0])
    reason_category = predict_reason_category(reason_model, latest_row)
    reason = describe_prediction_reason(reason_category, prediction)

    logger.info("Monthly model training complete")
    return model, reason_model, latest_row, reason, prediction


if __name__ == "__main__":
    try:
        run_monthly_model_training()
    except Exception as exc:
        logger.exception("Model training failed: %s", exc)
        raise