# Cut + Fairness Coordinator (LangGraph)

## Purpose
`cut_fairness_coordinator.py` orchestrates **Cut Advisor** and **Fairness Agent** in a single, deterministic flow. It ensures the proposed cut list is **validated for fairness** before the final recommendation is returned.

## What it does every run
1. **Run Cut Advisor** to generate an initial cut list and protected regions.
2. **Run Fairness review** to validate the proposed cut list against historical fairness metrics.
3. **Finalize the output**:
   - If fairness approves → keep the original list.
   - If fairness rejects and suggests a substitution → replace the last cut with the suggested region.
   - Attach the fairness review summary to the final output.

## Workflow graph
The coordinator uses LangGraph with a simple, linear DAG:

```
cut_advisor  ->  fairness_review  ->  finalize  ->  END
```

## Inputs
- **Cut Advisor inputs**: Uses live data through `DataLoader` and the DB connection.
- **Fairness Agent inputs**: Uses `silver.cut_history` and `gold.regional_consumption` to compute fairness.

## Outputs
The coordinator returns a dictionary that includes:
- `cut_priority_list`: final cut list (possibly adjusted)
- `protected_regions`: regions to protect
- `fairness_review`: decision payload from the Fairness Agent
- all other fields from `CutAdvisorAgent.analyze()`

## How to run
- Script entry point: `agents/cut_fairness_coordinator.py`
- It prints a short summary to stdout and returns the final result in code.

## Notes
- If there is **no cut history**, fairness defaults to approval.
- The substitution logic replaces the **last** region in the cut list to minimize disruption.
- You can change the substitution policy in `finalize_output()` if needed.
