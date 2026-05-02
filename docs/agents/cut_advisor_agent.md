# Cut Advisor Agent

## Purpose
The Cut Advisor Agent recommends which regions should be prioritized for power cuts when electricity production is below normal. It ranks regions based on solar investment and environmental impact so that regions with lower renewable investment and CO₂ savings are considered first for cuts, while high-impact regions are protected.

## How it works
1. **Load data** from the gold layer via `DataLoader`:
   - `gold.solar_impact` (installations, investment, CO₂ savings)
   - `gold.grid_stability` (incidents data)
   - `gold.regional_consumption` (regional summary)
   - `gold.monthly_production` (for current vs normal production)
2. **Assess current production status** by comparing the current month’s production against a 2‑year historical average.
3. **Score each region** using the latest year in `gold.solar_impact`:
   - Fewer installations → higher cut priority
   - Lower CO₂ savings → higher cut priority
   - Lower investment → higher cut priority
4. **Generate recommendations**:
   - If production is normal → no cuts, protect top regions.
   - If production is below normal → cut the highest‑score regions, protect the lowest‑score regions.
5. **Explain reasoning**:
   - Uses Groq (if configured) for a short explanation.
   - Falls back to a rule‑based explanation if LLM is unavailable.

## Inputs & dependencies
- **Database tables** (gold schema):
  - `gold.monthly_production`
  - `gold.solar_impact`
  - `gold.grid_stability`
  - `gold.regional_consumption`
- **Environment variables**:
  - `NEON_DATABASE_URL` (required; used by DB utilities)
  - `GROQ_API_KEY` (optional; enables LLM explanation)

## Outputs
`CutAdvisorAgent.analyze()` returns a dictionary with:
- `status`: `"success"`
- `current_production_status`: deficit and production metrics
- `cut_priority_list`: regions recommended for cuts
- `protected_regions`: regions recommended for protection
- `reasoning`: explanation string
- `region_scores`: full scoring breakdown per region

## Notes
- If current month production data is missing, the agent defaults to “no cuts needed.”
- Recommendations are deterministic and based on the latest available year in the solar impact dataset.
