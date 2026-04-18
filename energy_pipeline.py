#!/usr/bin/env python3
"""
Energy consumption streaming prediction pipeline.
Reads CSV daily, makes predictions, self-corrects via weekly retraining.
"""

import os
import json
import pickle
import logging
from datetime import datetime, timedelta
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import mean_absolute_error, mean_squared_error

# ============================================================================
# CONFIG
# ============================================================================

BASE_DIR = Path(__file__).parent / "energy_pipeline"
DATA_DIR = BASE_DIR / "data"
MODELS_DIR = BASE_DIR / "models"
LOGS_DIR = BASE_DIR / "logs"

# Create directories if they don't exist
for d in [DATA_DIR, MODELS_DIR, LOGS_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# Logging setup
log_file = LOGS_DIR / f"pipeline_{datetime.now().strftime('%Y%m')}.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler(log_file),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# File paths
CSV_PATH = "consumption_2026.csv"  # Change this to your CSV path
STATE_FILE = DATA_DIR / "pipeline_state.json"
MODEL_FILE = MODELS_DIR / "model.pkl"
SCALER_FILE = MODELS_DIR / "scaler.pkl"
PREDICTIONS_FILE = LOGS_DIR / "predictions.csv"

# ============================================================================
# HELPERS
# ============================================================================

def load_state():
    """Load pipeline state (which day we're on, last retrain date)."""
    if STATE_FILE.exists():
        with open(STATE_FILE) as f:
            return json.load(f)
    return {
        "current_day": 0,  # Index into CSV (0 = first row)
        "last_retrain": datetime.now().isoformat(),
        "total_days_seen": 0
    }

def save_state(state):
    """Save pipeline state."""
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f)
    logger.info(f"State saved: day {state['current_day']}")

def load_csv():
    """Load the consumption CSV."""
    if not os.path.exists(CSV_PATH):
        raise FileNotFoundError(f"CSV not found: {CSV_PATH}")
    df = pd.read_csv(CSV_PATH)
    # Expected columns: date, consumption (or similar)
    # If your CSV is different, adjust these column names
    if 'date' not in df.columns.str.lower():
        df.insert(0, 'date', pd.date_range(start='2025-01-01', periods=len(df)))
    df.columns = [c.lower() for c in df.columns]
    return df

def create_features(df, day_idx):
    """
    Extract features from historical data up to day_idx.
    Simple: rolling mean, day of week, month, previous consumption.
    """
    if day_idx == 0:
        return None  # No history yet
    
    hist = df.iloc[:day_idx]
    consumption = hist['consumption'].values
    
    # Basic features
    features = {
        'prev_consumption': consumption[-1],
        'rolling_7day_mean': np.mean(consumption[-7:]) if len(consumption) >= 7 else np.mean(consumption),
        'rolling_30day_mean': np.mean(consumption[-30:]) if len(consumption) >= 30 else np.mean(consumption),
        'day_of_week': hist['date'].iloc[-1].dayofweek,
        'day_of_month': hist['date'].iloc[-1].day,
        'month': hist['date'].iloc[-1].month,
        'is_weekend': 1 if hist['date'].iloc[-1].dayofweek >= 5 else 0,
    }
    return features

def train_model(df, up_to_day=None):
    """
    Train or retrain the RandomForest model on available data.
    """
    if up_to_day is None:
        up_to_day = len(df)
    
    if up_to_day < 10:
        logger.warning(f"Only {up_to_day} days available; model needs at least 10 days")
        return None, None
    
    X, y = [], []
    for i in range(1, up_to_day):  # Start from day 1 (day 0 has no prior history)
        feat = create_features(df, i)
        if feat is not None:
            X.append(feat)
            y.append(df['consumption'].iloc[i])
    
    if len(X) < 5:
        logger.warning(f"Not enough training samples ({len(X)})")
        return None, None
    
    X_df = pd.DataFrame(X)
    y = np.array(y)
    
    # Scale features
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X_df)
    
    # Train model
    model = RandomForestRegressor(n_estimators=50, max_depth=10, random_state=42, n_jobs=-1)
    model.fit(X_scaled, y)
    
    # Evaluate
    train_pred = model.predict(X_scaled)
    mae = mean_absolute_error(y, train_pred)
    rmse = np.sqrt(mean_squared_error(y, train_pred))
    logger.info(f"Model trained on {len(X)} samples | MAE: {mae:.2f} | RMSE: {rmse:.2f}")
    
    return model, scaler

def load_model():
    """Load saved model and scaler."""
    if MODEL_FILE.exists() and SCALER_FILE.exists():
        with open(MODEL_FILE, 'rb') as f:
            model = pickle.load(f)
        with open(SCALER_FILE, 'rb') as f:
            scaler = pickle.load(f)
        return model, scaler
    return None, None

def save_model(model, scaler):
    """Save model and scaler."""
    with open(MODEL_FILE, 'wb') as f:
        pickle.dump(model, f)
    with open(SCALER_FILE, 'wb') as f:
        pickle.dump(scaler, f)
    logger.info("Model and scaler saved")

def predict_consumption(df, day_idx, model, scaler):
    """
    Predict consumption for day_idx using current model.
    """
    if model is None or scaler is None:
        logger.warning("No model available yet; returning last observed value")
        return df['consumption'].iloc[day_idx - 1] if day_idx > 0 else 0
    
    feat = create_features(df, day_idx)
    if feat is None:
        return 0
    
    feat_df = pd.DataFrame([feat])
    feat_scaled = scaler.transform(feat_df)
    pred = model.predict(feat_scaled)[0]
    
    return max(0, pred)  # Consumption can't be negative

def save_prediction(day_idx, date, actual, predicted, error=None):
    """
    Append prediction result to CSV log.
    """
    record = {
        'date': date,
        'day_idx': day_idx,
        'actual_consumption': actual,
        'predicted_consumption': predicted,
        'error': actual - predicted if error is None else error,
        'abs_error': abs(actual - predicted) if error is None else abs(error),
        'timestamp': datetime.now().isoformat()
    }
    
    if PREDICTIONS_FILE.exists():
        df_log = pd.read_csv(PREDICTIONS_FILE)
        df_log = pd.concat([df_log, pd.DataFrame([record])], ignore_index=True)
    else:
        df_log = pd.DataFrame([record])
    
    df_log.to_csv(PREDICTIONS_FILE, index=False)
    logger.info(f"Day {day_idx}: actual={actual:.2f}, predicted={predicted:.2f}, error={actual-predicted:.2f}")

def should_retrain(last_retrain):
    """Check if it's time to retrain (weekly)."""
    last_retrain_dt = datetime.fromisoformat(last_retrain)
    days_since = (datetime.now() - last_retrain_dt).days
    return days_since >= 7

# ============================================================================
# MAIN PIPELINE
# ============================================================================

def run_daily_prediction():
    """
    Main daily prediction job.
    1. Load state (which day we're on)
    2. Read CSV
    3. Get today's actual consumption
    4. Load model and predict
    5. Save prediction
    6. If weekly, retrain
    """
    
    logger.info("=" * 70)
    logger.info("DAILY PREDICTION JOB STARTED")
    
    try:
        # Load state
        state = load_state()
        day_idx = state["current_day"]
        
        # Load CSV
        df = load_csv()
        
        # Check if we've reached the end
        if day_idx >= len(df):
            logger.warning(f"All {len(df)} days consumed. Pipeline complete or restart needed.")
            return
        
        # Get today's data
        today_row = df.iloc[day_idx]
        today_date = today_row['date']
        today_consumption = today_row['consumption']
        
        logger.info(f"Processing day {day_idx + 1}/{len(df)}: {today_date}")
        
        # Load existing model (or None)
        model, scaler = load_model()
        
        # Predict for today (using yesterday's data)
        if day_idx > 0:
            prediction = predict_consumption(df, day_idx, model, scaler)
        else:
            prediction = today_consumption  # First day: predict = actual
            logger.info("First day: no prior history to predict from")
        
        # Save prediction
        save_prediction(day_idx, today_date, today_consumption, prediction)
        
        # Check if it's time to retrain (weekly)
        if should_retrain(state["last_retrain"]):
            logger.info("=" * 70)
            logger.info("WEEKLY RETRAIN TRIGGERED")
            
            retrained_model, retrained_scaler = train_model(df, up_to_day=day_idx + 1)
            if retrained_model is not None:
                save_model(retrained_model, retrained_scaler)
                state["last_retrain"] = datetime.now().isoformat()
                logger.info("Model retrained and saved")
        
        # Update state for next run
        state["current_day"] = day_idx + 1
        state["total_days_seen"] = day_idx + 1
        save_state(state)
        
        logger.info("=" * 70)
        logger.info("DAILY PREDICTION JOB COMPLETED")
        
    except Exception as e:
        logger.error(f"ERROR: {e}", exc_info=True)
        raise

if __name__ == "__main__":
    run_daily_prediction()
