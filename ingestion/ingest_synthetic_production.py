"""
ingestion/ingest_synthetic_production.py
Generate and ingest synthetic monthly production data
"""
import calendar
from pathlib import Path
import sys
from typing import Dict, List, Optional

import numpy as np

sys.path.append(str(Path(__file__).parent.parent))
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


def month_name_to_number(month_name: str) -> int:
    return MONTH_NAME_TO_NUMBER.get(month_name.strip().lower(), 0)


def month_number_to_name(month_number: int) -> str:
    return MONTH_NUMBER_TO_NAME.get(month_number, "Unknown")


def next_month_year(month: int, year: int) -> tuple[int, int]:
    if month == 12:
        return 1, year + 1
    return month + 1, year


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


def ingest_synthetic_monthly_production() -> None:
    """Generate and ingest synthetic monthly production data"""
    logger.info("Starting synthetic monthly production ingestion")

    recent_rows = fetch_recent_production_rows(limit=2)
    if not recent_rows:
        raise RuntimeError("No existing monthly production data found in gold.monthly_production")

    previous = recent_rows[0]
    previous_previous = recent_rows[1] if len(recent_rows) > 1 else None
    synthetic_row = generate_synthetic_row(previous, previous_previous)
    insert_monthly_production_row(synthetic_row)

    logger.info("Synthetic monthly production ingestion complete")


if __name__ == "__main__":
    try:
        ingest_synthetic_monthly_production()
    except Exception as exc:
        logger.exception("Synthetic ingestion failed: %s", exc)
        raise