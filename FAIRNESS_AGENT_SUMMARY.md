# Fairness Agent Implementation Summary

## Document Requirements Fulfilled

### ✅ Fairness Agent (Wassimos) - All Features Implemented

#### 1. Seasonal Fairness Check
**Location:** `FairnessAgent._calculate_seasonal_gini()`

Computes Gini coefficient separately for summer vs winter to identify regions cut disproportionately during peak stress periods. Flags seasonal inequities that don't show in annual averages.

**Output in analyze():**
```python
'seasonal_gini': {
    'summer': 0.42,
    'winter': 0.32,
    'seasonal_flags': ['Sfax: 7 summer vs 2 winter cuts']
}
```

---

#### 2. Historical Trend of Gini Coefficient
**Location:** `FairnessAgent._calculate_monthly_gini_trend()` and `_calculate_trend_direction()`

Tracks Gini month-by-month (cumulative), calculates trend slope using linear regression, tells the jury whether fairness is improving/worsening/stable.

**Output in analyze():**
```python
'monthly_trend': [
    {'year': 2024, 'month': 1, 'gini': 0.45},
    {'year': 2024, 'month': 2, 'gini': 0.42},
    ...
],
'trend_direction': {
    'direction': 'improving',
    'slope': -0.015,
    'summary': 'Fairness is improving (Gini falling by ~0.015 per month).'
}
```

---

#### 3. Rotating Schedule Generator
**Location:** `FairnessAgent._generate_rotation_schedule()`

When Gini > 0.4, automatically generates a 4-week calendar spreading cuts evenly. No region appears twice in same week. Projects resulting Gini to show improvement.

**Output in analyze():**
```python
'rotation_schedule': {
    'schedule': {
        'week_1': ['Kairouan', 'Susa'],
        'week_2': ['Zaghouan', 'Kef'],
        'week_3': ['Jendouba', 'Kairouan'],
        'week_4': ['Siliana', 'Zaghouan']
    },
    'cuts_planned': 8,
    'projected_gini': 0.08
}
```

---

#### 4. Socioeconomic Weighting - Double Penalty Detection
**Location:** `FairnessAgent._detect_double_penalty()` and `_calculate_per_capita_burden()`

Identifies regions with:
- **Below-average solar investment** (from gold.solar_impact)
- **Above-average cuts** (from silver.cut_history)

Flags as "high" severity if investment_gap > 50% AND cut_excess > 50%.

Also calculates per-capita burden (cuts per 1000 residents) for most human-readable fairness metric.

**Output in analyze():**
```python
'double_penalty': [
    {
        'region': 'Tataouine',
        'cut_count': 5,
        'total_investment_dt': 120000,
        'investment_gap_pct': 45.2,
        'cut_excess_pct': 25.3,
        'severity': 'high'
    }
],
'per_capita_burden': [
    {
        'region': 'Kairouan',
        'cut_count': 8,
        'population': 580000,
        'cuts_per_1000': 13.8,
        'vs_average': 45.2
    }
]
```

---

#### 5. Cross-Validation with Cut Advisor
**Location:** `FairnessAgent.validate_cut_list(proposed_regions)`

**When Cut Advisor proposes cuts:**
- Fairness Agent recalculates Gini if those cuts are applied
- If Gini would exceed 0.4: **REJECTS** and suggests substitute region
- If Gini stays ≤ 0.4: **APPROVES**

This is the multi-agent interaction the document specifies.

**Example:**
```python
validation = agent.validate_cut_list(['Sfax', 'Kairouan'])

# If approved:
{
    'approved': True,
    'gini_before': 0.38,
    'gini_after': 0.40,
    'substitution': None,
    'message': 'Approved. Gini stays at 0.40 (≤ 0.4).'
}

# If rejected:
{
    'approved': False,
    'gini_before': 0.38,
    'gini_after': 0.45,
    'substitution': 'Zaghouan',
    'message': "REJECTED: cuts would raise Gini 0.38 → 0.45 (threshold 0.4). Suggest swapping a region for 'Zaghouan'."
}
```

---

#### 6. Fairness Metrics & Analysis
**Location:** `FairnessAgent._calculate_fairness_metrics()`

Computes multiple Gini variants:
- **gini_coefficient** - Standard unweighted Gini (count-based)
- **gini_by_duration** - Duration-weighted (total cut hours per region)
- **gini_weighted** - Consumption-weighted (high-consumption regions carry more weight)
- **gini_by_severity** - Severity-tier weighted (cut_immediately=1.0, cut_if_worsens=0.5, protected=0.0)

**Traffic light system** for non-technical audience:
- Gini ≤ 0.20: 🟢 Very Fair
- Gini ≤ 0.30: 🟢 Fair
- Gini ≤ 0.40: 🟡 Borderline
- Gini ≤ 0.60: 🔴 Unfair
- Gini > 0.60: 🔴 Very Unfair

---

#### 7. LLM Explanations & Recommendations
**Location:** `FairnessAgent._get_llm_explanation()` and `_generate_fairness_recommendations()`

Uses Groq LLM to generate plain-English summaries of fairness findings when issues detected. Falls back gracefully if LLM unavailable.

Generates actionable recommendations:
- "Reduce cut frequency in {region} — cut {count} times, {above_avg} above average"
- "Double-penalty alert: {region} has {pct}% below-average solar investment AND {pct}% more cuts"
- "Consumption-weighted Gini differs significantly — high-consumption regions carrying disproportionate burden"
- "Implement rotating cut schedule across all regions" (when Gini > 0.4)

---

## Data Sources Used

| Source | Purpose |
|--------|---------|
| `silver.cut_history` | Cut event records with date, region, duration, severity_tier, season |
| `gold.regional_consumption` | Regional consumption share (for weighted Gini) |
| `gold.gouvernorat_population` | Population per region (for per-capita metrics) |
| `gold.solar_impact` | Solar investment (for double-penalty detection) |

---

## Key Thresholds

```python
GINI_THRESHOLD = 0.4      # Alert: needs rebalancing
GINI_TARGET    = 0.3      # Ideal: fair distribution
SUMMER_MONTHS  = {6, 7, 8, 9}
WINTER_MONTHS  = {12, 1, 2, 3}
```

---

## Integration Points

### Cut Advisor Integration
```
Cut Advisor generates proposed cuts
           ↓
Fairness Agent.validate_cut_list(proposed_cuts)
           ↓
Returns: approved=True/False, gini_after, substitution
           ↓
Cut Advisor incorporates feedback into final list
```

### n8n Integration
The agent is designed for n8n extensibility through:
- HTTP POST endpoints (Python API layer)
- Structured JSON input/output
- No external service dependencies (Odoo removed, all data from PostgreSQL)
- See `FAIRNESS_AGENT_N8N.md` for workflow examples

---

## Not Implemented (User Request)

❌ **Odoo Integration** - User specified no Odoo
- Maintenance ticket creation
- Discuss messaging
- steg.fairness_report logging
- Cut validation logging to steg.cut_recommendation_log

✅ **These can be added via n8n instead** if needed:
- Create external database records
- Send notifications
- Trigger workflows
- Log decisions

---

## Testing the Agent

```python
from agents.fairness_agent import FairnessAgent

agent = FairnessAgent()

# Test 1: Full analysis
result = agent.analyze()
print(f"Gini: {result['fairness_score']}")
print(f"Status: {result['traffic_light']['label']}")
print(f"Needs rebalancing: {result['needs_rebalancing']}")

# Test 2: Cut validation
validation = agent.validate_cut_list(['Sfax', 'Kairouan'])
print(f"Approved: {validation['approved']}")
print(f"Message: {validation['message']}")
```

---

## Files Modified/Created

| File | Status | Purpose |
|------|--------|---------|
| `agents/fairness_agent.py` | ✅ Updated | Core agent implementation (Odoo removed) |
| `FAIRNESS_AGENT_N8N.md` | ✅ Created | n8n integration guide and examples |

---

## What to Do Next

1. **Test the agent** with real data:
   ```bash
   python test_agents.py
   ```

2. **Integrate with Cut Advisor** - Call `validate_cut_list()` after Cut Advisor generates recommendations

3. **Set up n8n workflows**:
   - Monthly fairness analysis trigger
   - Cut validation pipeline
   - Alert notifications

4. **Create dashboards** - Query analysis results and display fairness metrics in Superset

---

## Document Compliance Checklist

- ✅ Seasonal fairness check
- ✅ Historical trend of Gini coefficient
- ✅ Rotating schedule generator (for Gini > 0.4)
- ✅ Socioeconomic weighting (double penalty)
- ✅ Cross-validation with Cut Advisor
- ✅ Unfair region detection
- ✅ Per-capita burden analysis
- ✅ LLM explanations
- ✅ Recommendations generation
- ✅ n8n extensible (no hardcoded external dependencies)
- ❌ Odoo integration (removed per user request)
