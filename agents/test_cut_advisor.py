"""
Synthetic deficit scenario for Cut Advisor Agent.
Run with: python agents/test_cut_advisor.py
"""

import os
import sys
from datetime import datetime
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.cut_advisor_agent import CutAdvisorAgent


class MockDataLoader:
    """Synthetic DataLoader to force a clear cut vs protect outcome."""

    def load_solar_data(self):
        regions = [
            "Tunis",
            "Sfax",
            "Sousse",
            "Kairouan",
            "Gabes",
            "Bizerte",
            "Nabeul",
            "Monastir",
            "Medenine",
            "Tataouine",
        ]

        high_investment = {"Tunis", "Sfax", "Sousse"}
        mid_investment = {"Kairouan", "Gabes", "Monastir"}

        data = []
        for region in regions:
            if region in high_investment:
                installations = np.random.randint(5000, 9000)
                co2 = np.random.randint(1000, 1800)
                investment = np.random.randint(2500000, 4500000)
            elif region in mid_investment:
                installations = np.random.randint(1200, 2500)
                co2 = np.random.randint(250, 600)
                investment = np.random.randint(700000, 1500000)
            else:
                installations = np.random.randint(150, 600)
                co2 = np.random.randint(20, 140)
                investment = np.random.randint(120000, 400000)

            data.append(
                {
                    "region": region,
                    "year": 2024,
                    "total_installations": installations,
                    "co2_saved_tons": co2,
                    "total_investment_dt": investment,
                }
            )

        return pd.DataFrame(data)

    def load_incidents_data(self):
        return pd.DataFrame(
            [
                {
                    "region": "Kairouan",
                    "incident_date": pd.Timestamp.now() - pd.Timedelta(days=5),
                    "incident_type": "outage",
                    "duration_hours": 6,
                },
                {
                    "region": "Gabes",
                    "incident_date": pd.Timestamp.now() - pd.Timedelta(days=12),
                    "incident_type": "maintenance",
                    "duration_hours": 4,
                },
            ]
        )

    def load_regional_summary(self):
        return pd.DataFrame(
            [
                {"region": "Tunis", "population": 950000, "avg_consumption_mwh": 420},
                {"region": "Sfax", "population": 780000, "avg_consumption_mwh": 390},
                {"region": "Sousse", "population": 620000, "avg_consumption_mwh": 310},
                {"region": "Kairouan", "population": 450000, "avg_consumption_mwh": 220},
                {"region": "Gabes", "population": 410000, "avg_consumption_mwh": 200},
                {"region": "Bizerte", "population": 360000, "avg_consumption_mwh": 190},
                {"region": "Nabeul", "population": 340000, "avg_consumption_mwh": 180},
                {"region": "Monastir", "population": 320000, "avg_consumption_mwh": 175},
                {"region": "Medenine", "population": 280000, "avg_consumption_mwh": 160},
                {"region": "Tataouine", "population": 210000, "avg_consumption_mwh": 140},
            ]
        )


def create_mock_db_cursor(current_production: float, avg_production: float) -> MagicMock:
    mock_cursor = MagicMock()
    mock_cursor.fetchone.return_value = {
        "current_month": datetime.now().month,
        "avg_production": avg_production,
        "current_production": current_production,
    }
    return mock_cursor


def run_synthetic_deficit_scenario():
    print("=" * 60)
    print("CUT ADVISOR AGENT - SYNTHETIC DEFICIT SCENARIO")
    print("=" * 60)

    np.random.seed(42)
    agent = CutAdvisorAgent()
    agent.data_loader = MockDataLoader()

    agent._get_current_production_status = lambda: {
        "need_cuts": True,
        "deficit_percentage": 28.0,
        "current_production_gwh": 720.0,
        "normal_production_gwh": 1000.0,
    }

    with patch("agents.cut_advisor_agent.get_cursor") as mock_get_cursor:
        mock_cursor = create_mock_db_cursor(current_production=720.0, avg_production=1000.0)
        mock_get_cursor.return_value.__enter__.return_value = mock_cursor

        result = agent.analyze(apply_fairness=False)

    status = result.get("current_production_status", {})
    cut_first = result.get("cut_priority_list", [])
    protect = result.get("protected_regions", [])

    print(f"\nProduction deficit: {status.get('deficit_percentage')}%")
    print(f"Need cuts: {status.get('need_cuts')}")
    print(f"Cut first: {cut_first}")
    print(f"Protect: {protect}")
    print(f"Reasoning: {result.get('reasoning')}")

    assert status.get("need_cuts") is True
    assert len(cut_first) > 0
    assert len(protect) > 0
    assert len(result.get("region_scores", [])) > 0

    print("\n✅ Synthetic deficit scenario completed successfully")


if __name__ == "__main__":
    run_synthetic_deficit_scenario()