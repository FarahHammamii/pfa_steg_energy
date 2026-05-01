import calendar
import logging
from datetime import datetime
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
from sklearn.linear_model import SGDClassifier, SGDRegressor

from utils.db import get_cursor
from utils.logger import get_logger

logger = get_logger(__name__)

MONTH_NAME_TO_NUMBER = {name.lower(): num for num, name in enumerate(calendar.month_name) if name}
MONTH_NUMBER_TO_NAME = {num: name for name, num in MONTH_NAME_TO_NUMBER.items()}

SEASONAL_FACTORS = {
    1: 0.95,  # January
    2: 0.96,
    3: 0.98,
    4: 1.00,
    5: 1.02,
    6: 1.05,
    7: 1.08,
    8: 1.07,
    9: 1.03,
    10: 1.00,
    11: 0.97,
    12: 0.96,
}

SEASON_NAMES = {
    1: "winter",
    2: "winter",
    3: "spring",
    4: "spring",
    5: "spring",
    6: "summer",
    7: "summer",
    8: "summer",
    9: "fall",
    10: "fall",
    11: "fall",
    12: "winter",
}

FEATURE_COLUMNS = [
    "month_sin",
    "month_cos",
    "avg_renewable_share_pct",
    "growth_rate_pct",
]


def get_season_for_month(month: int) -> str:
    return SEASON_NAMES.get(month, "unknown")


REASON_TEMPLATES = {
    "summer_peak": "Summer season trend is strong, lifting the forecast.",
    "winter_dip": "Winter season typically reduces production, which lowers the forecast.",
    "positive_growth": "A positive growth trend is supporting the forecast.",
    "negative_growth": "A negative growth trend is limiting the forecast.",
    "strong_renewables": "High renewable share supports a stable, resilient forecast.",
    "seasonal_pattern": "Seasonal patterns and production trends together define the forecast.",
}


def describe_prediction_reason(category: str, predicted: float) -> str:
    return REASON_TEMPLATES.get(category, REASON_TEMPLATES["seasonal_pattern"])


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
        loss="log",
        random_state=42,
    )
    classifier.fit(X, y)
    return classifier


def predict_reason_category(model: SGDClassifier, row: pd.Series) -> str:
    X = row[FEATURE_COLUMNS].to_frame().T
    predicted_category = model.predict(X)[0]
    return str(predicted_category)


def ensure_table_exists() -> None:
    with get_cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS gold.monthly_production (
                year INT,
                month INT,
                month_name VARCHAR,
                total_production_gwh FLOAT,
                avg_renewable_share_pct FLOAT,
                growth_rate_pct FLOAT,
                PRIMARY KEY (year, month)
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS model_metrics (
                year INT,
                month_name VARCHAR,
                predicted_gwh FLOAT,
                actual_gwh FLOAT,
                absolute_error FLOAT,
                prediction_reason TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        cur.execute(
            """
            ALTER TABLE model_metrics
            ADD COLUMN IF NOT EXISTS prediction_reason TEXT
            """
        )
    logger.info("Ensured gold.monthly_production and model_metrics tables exist")


def month_name_to_number(month_name: str) -> int:
    return MONTH_NAME_TO_NUMBER.get(month_name.strip().lower(), 0)


def month_number_to_name(month_number: int) -> str:
    return MONTH_NUMBER_TO_NAME.get(month_number, "Unknown")


def next_month_year(month: int, year: int) -> tuple[int, int]:
    if month == 12:
        return 1, year + 1
    return month + 1, year


def cyclical_month_features(month: int) -> Dict[str, float]:
    radians = 2 * np.pi * (month - 1) / 12.0
    return {
        "month_sin": float(np.sin(radians)),
        "month_cos": float(np.cos(radians)),
    }


def fetch_recent_production_rows(limit: int = 2) -> List[Dict]:
    query = """
    SELECT year, month, month_name, total_production_gwh,
           avg_renewable_share_pct, growth_rate_pct
    FROM gold.monthly_production
    ORDER BY year DESC, month DESC
    LIMIT %s
    """
    with get_cursor(dict_cursor=True) as cur:
        cur.execute(query, (limit,))
        return cur.fetchall()


def generate_synthetic_row(previous: Dict, previous_previous: Optional[Dict] = None) -> Dict:
    prev_year = int(previous["year"])
    prev_month = int(previous["month"])
    next_month, next_year = next_month_year(prev_month, prev_year)

    prev_production = float(previous["total_production_gwh"])
    prev_growth = float(previous.get("growth_rate_pct", 0.0) or 0.0) / 100.0
    prev_renew = float(previous.get("avg_renewable_share_pct", 0.0) or 0.0)

    if previous_previous:
        prev_prev_production = float(previous_previous["total_production_gwh"])
        monthly_trend = (prev_production - prev_prev_production) / max(prev_prev_production, 1.0)
    else:
        monthly_trend = prev_growth

    trend_factor = 1 + monthly_trend * 0.8
    seasonality = SEASONAL_FACTORS.get(next_month, 1.0)

    noisy_production = prev_production * trend_factor * seasonality
    synthetic_production = float(np.round(noisy_production * (1 + np.random.normal(0, 0.03)), 2))
    synthetic_production = max(synthetic_production, 0.1)

    renewal_noise = np.random.normal(0, 0.8)
    growth_noise = np.random.normal(0, 1.2)

    synthetic_renewable_share = float(np.clip(prev_renew + renewal_noise, 0.0, 100.0))
    synthetic_growth_rate = float(np.clip((monthly_trend * 100.0) + growth_noise, -20.0, 20.0))

    return {
        "year": next_year,
        "month": next_month,
        "month_name": month_number_to_name(next_month),
        "total_production_gwh": synthetic_production,
        "avg_renewable_share_pct": synthetic_renewable_share,
        "growth_rate_pct": synthetic_growth_rate,
    }


def insert_monthly_production_row(row: Dict) -> None:
    insert_sql = """
    INSERT INTO gold.monthly_production (
        year, month, month_name,
        total_production_gwh, avg_renewable_share_pct,
        growth_rate_pct
    ) VALUES (%s, %s, %s, %s, %s, %s)
    ON CONFLICT (year, month) DO UPDATE SET
        month_name = EXCLUDED.month_name,
        total_production_gwh = EXCLUDED.total_production_gwh,
        avg_renewable_share_pct = EXCLUDED.avg_renewable_share_pct,
        growth_rate_pct = EXCLUDED.growth_rate_pct
    """
    with get_cursor() as cur:
        cur.execute(
            insert_sql,
            (
                row["year"],
                row["month"],
                row["month_name"],
                row["total_production_gwh"],
                row["avg_renewable_share_pct"],
                row["growth_rate_pct"],
            ),
        )
    logger.info(
        "Inserted monthly production: %s %s (%.2f GWh)",
        row["month_name"],
        row["year"],
        row["total_production_gwh"],
    )


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


def insert_model_metric(year: int, month_name: str, predicted: float, actual: float, reason: str) -> None:
    insert_sql = """
    INSERT INTO model_metrics (year, month_name, predicted_gwh, actual_gwh, absolute_error, prediction_reason)
    VALUES (%s, %s, %s, %s, %s, %s)
    """
    with get_cursor() as cur:
        cur.execute(
            insert_sql,
            (year, month_name, predicted, actual, abs(predicted - actual), reason),
        )
    logger.info(
        "Recorded model metric for %s %s: predicted=%.2f actual=%.2f error=%.2f",
        month_name,
        year,
        predicted,
        actual,
        abs(predicted - actual),
    )
    logger.info("Prediction reason: %s", reason)


def run_monthly_feedback_loop() -> None:
    logger.info("Starting monthly feedback loop")
    ensure_table_exists()

    recent_rows = fetch_recent_production_rows(limit=2)
    if not recent_rows:
        raise RuntimeError("No existing monthly production data found in gold.monthly_production")

    previous = recent_rows[0]
    previous_previous = recent_rows[1] if len(recent_rows) > 1 else None
    synthetic_row = generate_synthetic_row(previous, previous_previous)
    insert_monthly_production_row(synthetic_row)

    history_df = load_monthly_history()
    if history_df.empty:
        raise RuntimeError("Unable to load gold.monthly_production history after synthetic insertion")

    model = train_online_model(history_df)
    reason_model = train_reason_model(history_df)
    latest_row = history_df.iloc[-1]
    latest_features = latest_row[FEATURE_COLUMNS].to_frame().T
    prediction = float(model.predict(latest_features)[0])
    reason_category = predict_reason_category(reason_model, latest_row)
    reason = describe_prediction_reason(reason_category, prediction)

    insert_model_metric(
        year=int(latest_row["year"]),
        month_name=str(latest_row["month_name"]),
        predicted=prediction,
        actual=float(latest_row["total_production_gwh"]),
        reason=reason,
    )

    logger.info(
        "Monthly feedback loop complete: generated and scored %s %s",
        latest_row["month_name"],
        latest_row["year"],
    )


if __name__ == "__main__":
    try:
        run_monthly_feedback_loop()
    except Exception as exc:
        logger.exception("Monthly feedback loop failed: %s", exc)
        raise
