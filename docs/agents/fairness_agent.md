
# Fairness Agent

**Ensures fair distribution of power cuts across Tunisian regions**

The `FairnessAgent` is a specialized agent in STEG's electricity grid management system. It analyzes historical power-cut data, measures fairness using the **Gini coefficient**, detects unfair patterns, seasonal biases, and "double-penalty" regions (low solar investment + high cuts), and provides actionable recommendations and rotation schedules.

---

## Features

- **Gini Coefficient Calculation**  
  - Count-based (number of cuts per region)  
  - Duration-weighted (total minutes of cuts per region)

- **Seasonal Analysis**  
  - Separate Gini for summer vs winter  
  - Flags regions penalized only in peak seasons

- **Trend Tracking**  
  - Monthly cumulative Gini trend to see if fairness is improving or worsening

- **Unfairness Detection**  
  - Regions cut significantly more or less than average (±1 std dev)

- **Double-Penalty Detection**  
  - Regions with **below-average solar investment** AND **above-average cuts**

- **Pre-Approval Validation**  
  - `validate_cut_list()` — simulates proposed cuts and blocks any that would push Gini above threshold

- **Automatic Rotation Schedule**  
  - When Gini > 0.4, generates a 4-week fair rotation plan targeting Gini ≤ 0.3

- **Smart Recommendations**  
  - Automatic suggestions for rebalancing

- **Natural Language Explanation**  
  - Powered by Groq (Llama 3.3 70B) when available, otherwise intelligent fallback

---

## Configuration Constants

| Constant            | Value  | Description |
|---------------------|--------|-------------|
| `GINI_THRESHOLD`    | 0.4    | Gini above this = needs rebalancing |
| `GINI_TARGET`       | 0.3    | Target after rotation schedule |
| `SUMMER_MONTHS`     | {6,7,8,9} | (Defined but season column is used) |
| `WINTER_MONTHS`     | {12,1,2,3} | (Defined but season column is used) |

---

## Requirements

### Environment
- `GROQ_API_KEY` (optional — enables rich LLM explanations)
- Database connection via `utils.db.get_cursor()`

### Data Tables
- **`silver.cut_history`**  
  Columns: `region`, `cut_date`, `duration_minutes`, `season`
- **`gold.solar_impact`**  
  Columns: `region`, `total_investment_dt`

---

## Usage

### 1. Full Fairness Analysis (Main Entry Point)

```python
from agents.fairness_agent import FairnessAgent

agent = FairnessAgent()
result = agent.analyze()

print(f"Fairness Score (Gini): {result['fairness_score']}")
print(f"Needs rebalancing: {result['needs_rebalancing']}")
print(result['explanation'])           # Human-readable summary
print(result['recommendations'])       # List of actions