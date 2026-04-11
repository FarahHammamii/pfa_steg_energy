# STEG Observatory System

## Overview
The STEG Observatory System is an intelligent, data-driven platform designed to support electricity management in Tunisia. It monitors production, predicts shortages, and recommends fair power-cut strategies using AI agents.

## Problem Statement
Electricity cuts in Tunisia are often applied without a clear or fair strategy:
- Some regions are cut too frequently
- Others are rarely affected
- No data-driven decision system exists

## Proposed Solution
An intelligent observatory system that:
- Monitors electricity production in real time
- Predicts future shortages
- Recommends which regions to cut or protect
- Ensures fair distribution of power cuts across regions

## System Components

### 1. Cut Advisor Agent
Purpose: Identify which regions should be cut first during shortages

Logic:
Each region is scored based on:
- Low solar panel presence → higher priority for cuts
- Low CO₂ savings → higher priority
- Low investment → higher priority

Outputs:
- Regions to cut first
- Regions to protect
- AI-generated explanation

### 2. Fairness Agent
Purpose: Ensure power cuts are distributed fairly

Logic:
- Counts number of cuts per region
- Computes average and standard deviation
- Flags over-cut and under-cut regions
- Calculates Gini coefficient (0 = fair, 1 = unfair)

Outputs:
- Fairness score (Gini coefficient)
- List of unfairly treated regions
- Recommendations

### 3. Forecast Agent
Purpose: Predict future electricity demand

Logic:
- Uses historical monthly data
- Adjusts for trends and seasonality
- Predicts next 3 months

Outputs:
- Forecast values
- Confidence range
- Trend direction
- Anomaly detection

## Synthetic Data Usage

Problem:
Real datasets are incomplete.

Solution:
Synthetic data generation to simulate:
- Cut history
- Recovery times
- Daily production

Note: Synthetic data is flagged with `is_synthetic = TRUE`.

## System Architecture

DATA LAYER
PostgreSQL (Neon)

AGENT LAYER
- Cut Advisor Agent
- Fairness Agent
- Forecast Agent

PRESENTATION LAYER
- Dashboard
- n8n automation
- Executive summary

## Key Metrics

- Alert Level: GREEN
- Gini Coefficient: 0.273
- Forecast Production: 1336 GWh
- Trend: Increasing

## Core Contributions

1. Prevents unfair electricity cuts
2. Predicts shortages in advance
3. Protects regions with solar investments

##  Summary
An intelligent observatory system with three AI agents that monitors Tunisia's electricity grid, predicts future production, and recommends fair, data-driven power cut strategies.

## Workflow

1. Forecast Agent predicts production
2. Compare with historical average
3. If deficit detected:
   - Cut Advisor selects regions
   - Fairness Agent validates
4. Output decisions
5. Alerts sent via n8n
