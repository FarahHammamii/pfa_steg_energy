"""
agents/monthly_feedback_agent.py
Monthly production feedback loop agent with forecasting
"""
import calendar
import json
import sys
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Any, Tuple

import numpy as np
import pandas as pd
from sklearn.linear_model import SGDClassifier, SGDRegressor

sys.path.append(str(Path(__file__).parent.parent))
from utils.db import get_cursor
from utils.logger import get_logger

logger = get_logger(__name__)

# ================================================================
# CONSTANTS
# ================================================================

MONTH_NAME_TO_NUMBER = {name.lower(): num for num, name in enumerate(calendar.month_name) if name}
MONTH_NUMBER_TO_NAME = {num: name for name, num in MONTH_NAME_TO_NUMBER.items()}

SEASONAL_FACTORS = {
    1: 0.95,   # January
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
    1: "winter", 2: "winter", 3: "spring", 4: "spring",
    5: "spring", 6: "summer", 7: "summer", 8: "summer",
    9: "fall", 10: "fall", 11: "fall", 12: "winter",
}

FEATURE_COLUMNS = [
    "month_sin",
    "month_cos",
    "avg_renewable_share_pct",
    "growth_rate_pct",
]

REASON_TEMPLATES = {
    "summer_peak": "Summer season trend is strong, lifting the forecast.",
    "winter_dip": "Winter season typically reduces production, which lowers the forecast.",
    "positive_growth": "A positive growth trend is supporting the forecast.",
    "negative_growth": "A negative growth trend is limiting the forecast.",
    "strong_renewables": "High renewable share supports a stable, resilient forecast.",
    "seasonal_pattern": "Seasonal patterns and production trends together define the forecast.",
}

# ================================================================
# HELPER FUNCTIONS
# ================================================================

def month_number_to_name(month_number: int) -> str:
    return MONTH_NUMBER_TO_NAME.get(month_number, "Unknown")


def next_month_year(month: int, year: int) -> Tuple[int, int]:
    if month == 12:
        return 1, year + 1
    return month + 1, year


def cyclical_month_features(month: int) -> Dict[str, float]:
    radians = 2 * np.pi * (month - 1) / 12.0
    return {
        "month_sin": float(np.sin(radians)),
        "month_cos": float(np.cos(radians)),
    }


def get_season_for_month(month: int) -> str:
    return SEASON_NAMES.get(month, "unknown")


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


def describe_prediction_reason(category: str, predicted: float) -> str:
    return REASON_TEMPLATES.get(category, REASON_TEMPLATES["seasonal_pattern"])


# ================================================================
# TABLE MANAGEMENT
# ================================================================

def ensure_tables_exist() -> None:
    """Ensure all required tables exist"""
    with get_cursor() as cur:
        # Monthly production table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS gold.monthly_production (
                year INT,
                month INT,
                month_name VARCHAR,
                total_production_gwh FLOAT,
                avg_renewable_share_pct FLOAT,
                growth_rate_pct FLOAT,
                PRIMARY KEY (year, month)
            )
        """)
        
        # Model metrics table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS model_metrics (
                id SERIAL PRIMARY KEY,
                year INT,
                month_name VARCHAR,
                predicted_gwh FLOAT,
                actual_gwh FLOAT,
                absolute_error FLOAT,
                prediction_reason TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Production forecast table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS gold.production_forecast (
                id SERIAL PRIMARY KEY,
                forecast_date DATE,
                year INT,
                month INT,
                month_name VARCHAR,
                predicted_gwh FLOAT,
                confidence_lower FLOAT,
                confidence_upper FLOAT,
                forecast_horizon_months INT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
    logger.info("Ensured all production tables exist")


# ================================================================
# DATA LOADING & SYNTHETIC GENERATION
# ================================================================

def fetch_recent_production_rows(limit: int = 2) -> List[Dict]:
    """Fetch most recent production rows"""
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
    """Generate synthetic production data for next month"""
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
    """Insert a single production row - check existence first"""
    with get_cursor() as cur:
        # First check if record already exists
        cur.execute("""
            SELECT 1 FROM gold.monthly_production 
            WHERE year = %s AND month = %s
        """, (row["year"], row["month"]))
        
        exists = cur.fetchone()
        
        if not exists:
            insert_sql = """
                INSERT INTO gold.monthly_production (
                    year, month, month_name,
                    total_production_gwh, avg_renewable_share_pct,
                    growth_rate_pct
                ) VALUES (%s, %s, %s, %s, %s, %s)
            """
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
        else:
            logger.info(
                "Monthly production already exists: %s %s (%.2f GWh) - skipping",
                row["month_name"],
                row["year"],
                row["total_production_gwh"],
            )


def load_monthly_history() -> pd.DataFrame:
    """Load historical production data as DataFrame with features"""
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
    
    # Convert Decimal columns to float
    for col in ['total_production_gwh', 'avg_renewable_share_pct', 'growth_rate_pct']:
        if col in df.columns:
            df[col] = df[col].apply(lambda x: float(x) if x is not None else 0.0)
    
    df["month_number"] = df["month"].astype(int)
    cyclical = df["month_number"].apply(cyclical_month_features).tolist()
    df = pd.concat([df, pd.DataFrame(cyclical)], axis=1)
    df.fillna(0, inplace=True)
    return df

# ================================================================
# MODEL TRAINING & PREDICTION
# ================================================================

def train_production_model(history: pd.DataFrame) -> Tuple[SGDRegressor, Dict]:
    """Train the production prediction model"""
    model = SGDRegressor(
        max_iter=1000,
        tol=1e-3,
        penalty="l2",
        learning_rate="invscaling",
        eta0=0.001,
        random_state=42,
    )
    
    if history.shape[0] < 6:
        return None, {"error": "insufficient_data", "reason": "Need at least 6 months of data"}
    
    X = history[FEATURE_COLUMNS]
    y = history["total_production_gwh"]
    model.fit(X, y)
    
    return model, {"status": "trained", "data_points": len(history)}


def train_reason_model(history: pd.DataFrame) -> Optional[SGDClassifier]:
    """Train the reason classification model"""
    if history.shape[0] < 6:
        return None
    
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


def predict_next_month(
    model: SGDRegressor,
    reason_model: Optional[SGDClassifier],
    history_df: pd.DataFrame
) -> Dict[str, Any]:
    """Predict production for next month"""
    if model is None or history_df.empty:
        return {"error": "model_not_available", "prediction": None}
    
    # Get latest row and create features for next month
    latest_row = history_df.iloc[-1]
    last_month = int(latest_row["month"])
    last_year = int(latest_row["year"])
    
    next_month_num, next_year = next_month_year(last_month, last_year)
    
    # Convert Decimal values to float
    avg_renewable = float(latest_row["avg_renewable_share_pct"]) if latest_row["avg_renewable_share_pct"] else 0.0
    growth_rate = float(latest_row["growth_rate_pct"]) if latest_row["growth_rate_pct"] else 0.0
    
    # Create feature vector for next month
    next_features = cyclical_month_features(next_month_num)
    next_row = pd.DataFrame([{
        "month_sin": next_features["month_sin"],
        "month_cos": next_features["month_cos"],
        "avg_renewable_share_pct": avg_renewable,
        "growth_rate_pct": growth_rate,
    }])
    
    # Make prediction
    prediction = float(model.predict(next_row)[0])
    prediction = max(prediction, float(latest_row["total_production_gwh"]) * 0.7)  # Cap drop at 30%
    
    # Get reason
    if reason_model:
        try:
            reason_category = reason_model.predict(next_row)[0]
            reason = describe_prediction_reason(reason_category, prediction)
        except Exception:
            reason = describe_prediction_reason("seasonal_pattern", prediction)
    else:
        reason = describe_prediction_reason("seasonal_pattern", prediction)
    
    # Calculate confidence based on historical error
    with get_cursor(dict_cursor=True) as cur:
        cur.execute("""
            SELECT AVG(absolute_error) as avg_error, STDDEV(absolute_error) as std_error
            FROM model_metrics
            WHERE created_at >= NOW() - INTERVAL '12 months'
        """)
        error_stats = cur.fetchone()
    
    if error_stats and error_stats.get('avg_error'):
        avg_error = float(error_stats['avg_error']) if error_stats['avg_error'] else 0
        confidence_lower = prediction - (avg_error * 1.5)
        confidence_upper = prediction + (avg_error * 1.5)
    else:
        confidence_lower = prediction * 0.85
        confidence_upper = prediction * 1.15
    
    return {
        "prediction_gwh": round(prediction, 2),
        "confidence_lower": round(confidence_lower, 2),
        "confidence_upper": round(confidence_upper, 2),
        "forecast_month": next_month_num,
        "forecast_year": next_year,
        "forecast_month_name": month_number_to_name(next_month_num),
        "reason": reason,
        "based_on_data_points": len(history_df)
    }

def predict_future_months(
    model: SGDRegressor,
    reason_model: Optional[SGDClassifier],
    history_df: pd.DataFrame,
    months_ahead: int = 6
) -> List[Dict[str, Any]]:
    """Predict production for multiple months ahead"""
    predictions = []
    current_df = history_df.copy()
    
    # Convert Decimal columns to float in the entire dataframe
    for col in ['total_production_gwh', 'avg_renewable_share_pct', 'growth_rate_pct']:
        if col in current_df.columns:
            current_df[col] = current_df[col].apply(lambda x: float(x) if x is not None else 0.0)
    
    for i in range(months_ahead):
        pred = predict_next_month(model, reason_model, current_df)
        if pred.get("error"):
            break
        
        predictions.append(pred)
        
        # Append prediction as a new row for next iteration
        new_row = pd.DataFrame([{
            "year": pred["forecast_year"],
            "month": pred["forecast_month"],
            "month_name": pred["forecast_month_name"],
            "total_production_gwh": pred["prediction_gwh"],
            "avg_renewable_share_pct": float(current_df.iloc[-1]["avg_renewable_share_pct"]),
            "growth_rate_pct": float(current_df.iloc[-1]["growth_rate_pct"]),
            "month_sin": cyclical_month_features(pred["forecast_month"])["month_sin"],
            "month_cos": cyclical_month_features(pred["forecast_month"])["month_cos"],
        }])
        current_df = pd.concat([current_df, new_row], ignore_index=True)
    
    return predictions


def save_forecast(predictions: List[Dict[str, Any]]) -> None:
    """Save forecast predictions to database"""
    with get_cursor() as cur:
        # First, clear old forecasts for the same dates
        for pred in predictions:
            forecast_date = datetime(pred["forecast_year"], pred["forecast_month"], 1).date()
            cur.execute("""
                DELETE FROM gold.production_forecast 
                WHERE forecast_date = %s
            """, (forecast_date,))
        
        # Then insert new forecasts
        for pred in predictions:
            cur.execute("""
                INSERT INTO gold.production_forecast 
                (forecast_date, year, month, month_name, predicted_gwh, 
                 confidence_lower, confidence_upper, forecast_horizon_months)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                datetime(pred["forecast_year"], pred["forecast_month"], 1).date(),
                pred["forecast_year"],
                pred["forecast_month"],
                pred["forecast_month_name"],
                pred["prediction_gwh"],
                pred["confidence_lower"],
                pred["confidence_upper"],
                pred["forecast_month"] - datetime.now().month if pred["forecast_year"] == datetime.now().year else 12
            ))
    logger.info(f"Saved {len(predictions)} forecast entries")

def insert_model_metric(year: int, month_name: str, predicted: float, actual: float, reason: str) -> None:
    """Record model performance metrics"""
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


# ================================================================
# MAIN FEEDBACK LOOP
# ================================================================

def run_monthly_feedback_loop() -> Dict[str, Any]:
    """
    Run the monthly feedback loop and return predictions.
    This is the main entry point for the API.
    """
    logger.info("Starting monthly feedback loop")
    ensure_tables_exist()
    
    result = {
        "status": "success",
        "message": "",
        "historical_data_points": 0,
        "latest_actual": None,
        "predictions": [],
        "model_info": {},
        "triggered_at": datetime.now().isoformat()
    }
    
    try:
        # Check if we have existing data
        recent_rows = fetch_recent_production_rows(limit=2)
        
        # Instead of direct insert, use a check
        if not recent_rows:
            logger.info("No existing data found. Generating synthetic seed data...")
            
            # Check if each month already exists before inserting
            seed_data = [
                {"year": 2024, "month": 1, "month_name": "January", "total_production_gwh": 1150.0, 
                 "avg_renewable_share_pct": 25.0, "growth_rate_pct": 0.5},
                {"year": 2024, "month": 2, "month_name": "February", "total_production_gwh": 1160.0, 
                 "avg_renewable_share_pct": 26.0, "growth_rate_pct": 0.8},
            ]
            for row in seed_data:
                with get_cursor() as cur:
                    cur.execute("SELECT 1 FROM gold.monthly_production WHERE year = %s AND month = %s", 
                               (row["year"], row["month"]))
                    if not cur.fetchone():
                        insert_monthly_production_row(row)
            recent_rows = fetch_recent_production_rows(limit=2)
        
        # Generate synthetic next month data
        previous = recent_rows[0]
        previous_previous = recent_rows[1] if len(recent_rows) > 1 else None
        synthetic_row = generate_synthetic_row(previous, previous_previous)
        insert_monthly_production_row(synthetic_row)
        
        # Load full history for training
        history_df = load_monthly_history()
        
        if history_df.empty:
            result["status"] = "warning"
            result["message"] = "Unable to load production history"
            return result
        
        result["historical_data_points"] = len(history_df)
        
        # Get latest actual data point
        latest_actual = {
            "year": int(history_df.iloc[-1]["year"]),
            "month": int(history_df.iloc[-1]["month"]),
            "month_name": str(history_df.iloc[-1]["month_name"]),
            "total_production_gwh": float(history_df.iloc[-1]["total_production_gwh"]),
            "growth_rate_pct": float(history_df.iloc[-1]["growth_rate_pct"]),
            "avg_renewable_share_pct": float(history_df.iloc[-1]["avg_renewable_share_pct"]),
        }
        result["latest_actual"] = latest_actual
        
        # Train models
        production_model, model_info = train_production_model(history_df)
        reason_model = train_reason_model(history_df)
        result["model_info"] = model_info
        
        if production_model is None:
            result["status"] = "warning"
            result["message"] = model_info.get("reason", "Insufficient data for prediction")
            return result
        
        # Generate predictions for next 6 months
        predictions = predict_future_months(production_model, reason_model, history_df, months_ahead=6)
        result["predictions"] = predictions
        
        # Save forecasts to database
        if predictions:
            save_forecast(predictions)
            
            # Record model metric for latest actual vs predicted
            if len(predictions) > 0:
                insert_model_metric(
                    year=int(latest_actual["year"]),
                    month_name=str(latest_actual["month_name"]),
                    predicted=predictions[0]["prediction_gwh"],
                    actual=float(latest_actual["total_production_gwh"]),
                    reason=predictions[0].get("reason", "Monthly feedback loop prediction")
                )
        
        result["message"] = f"Successfully predicted {len(predictions)} months ahead. Next month: {predictions[0]['prediction_gwh']} GWh" if predictions else "No predictions generated"
        
        logger.info(f"Monthly feedback loop complete: {result['message']}")
        
    except Exception as exc:
        logger.exception("Monthly feedback loop failed: %s", exc)
        result["status"] = "error"
        result["message"] = str(exc)
    
    return result


def get_production_forecast(months: int = 6) -> Dict[str, Any]:
    """Get saved production forecast from database"""
    with get_cursor(dict_cursor=True) as cur:
        cur.execute("""
            SELECT forecast_date, year, month, month_name, predicted_gwh, 
                   confidence_lower, confidence_upper, created_at
            FROM gold.production_forecast
            WHERE forecast_date >= CURRENT_DATE
            ORDER BY forecast_date
            LIMIT %s
        """, (months,))
        forecasts = cur.fetchall()
        
        cur.execute("""
            SELECT year, month, month_name, total_production_gwh, 
                   avg_renewable_share_pct, growth_rate_pct
            FROM gold.monthly_production
            ORDER BY year DESC, month DESC
            LIMIT 12
        """)
        historical = cur.fetchall()
    
    return {
        "forecasts": list(forecasts),
        "historical": list(historical),
        "forecast_method": "Seasonal + Trend Model with ML (SGD)",
        "as_of": datetime.now().isoformat()
    }


if __name__ == "__main__":
    # Run the feedback loop and print results
    result = run_monthly_feedback_loop()
    print(json.dumps(result, indent=2, default=str))