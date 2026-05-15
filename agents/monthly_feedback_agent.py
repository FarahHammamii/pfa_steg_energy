"""
agents/monthly_feedback_agent.py
Monthly production feedback loop agent
"""
from pathlib import Path
import sys

sys.path.append(str(Path(__file__).parent.parent))
from utils.db import get_cursor
from utils.logger import get_logger
from ingestion.ingest_synthetic_production import ingest_synthetic_monthly_production
from etl.gold_monthly_model import run_monthly_model_training

logger = get_logger(__name__)


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


def run_monthly_feedback_loop() -> None:
    logger.info("Starting monthly feedback loop")
    ensure_table_exists()

    # Ingest synthetic data
    ingest_synthetic_monthly_production()

    # Train models and predict
    model, reason_model, latest_row, reason, prediction = run_monthly_model_training()

    # Record metrics
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