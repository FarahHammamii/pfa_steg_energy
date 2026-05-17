# STEG Energy Lakehouse & Observatory

[![Python
3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-Neon-336791.svg)](https://neon.tech)
[![Groq](https://img.shields.io/badge/Groq-LLM-orange.svg)](https://groq.com)

A production-grade data lakehouse and observatory for STEG (Tunisian Electricity and
Gas Company). It consolidates operational data, cleans and aggregates it through a
medallion architecture, and exposes analytics through agents, APIs, and dashboards.

## Table of Contents

- [Overview](#overview)
- [Architecture](#architecture)
- [Key Capabilities](#key-capabilities)
- [Repository Layout](#repository-layout)
- [Getting Started](#getting-started)
- [Configuration](#configuration)
- [Usage](#usage)
- [Agents & Orchestration](#agents--orchestration)
- [API](#api)
- [Data Sources](#data-sources)
- [Tests](#tests)

## Overview

The project focuses on energy reliability, renewable adoption, and fairness in cut
planning. It brings together production, solar installation, grid stability, and
benchmark data to enable:

- Operational monitoring and anomaly detection.
- Automated fairness assessment of cut distribution.
- Forecasting and strategic recommendations.
- LLM-powered narrative summaries for decision makers.

## Architecture

Medallion layers organize the data flow:

- **Bronze (Raw)**: raw CSV/PDF ingests and external APIs.
- **Silver (Cleaned)**: validation, type normalization, and deduplication.
- **Gold (Aggregated)**: monthly KPIs, solar impact, grid stability, benchmarks.
- **Analytics Layer**: agents and API endpoints providing insights and alerts.

## Key Capabilities

- **Data integration** from STEG production, PROSOL, grid incidents, and external
	benchmarks (World Bank, IEA).
- **Quality enforcement** with validation, deduplication, and consistency checks.
- **Analytics** for renewable share, CO2 impact, reliability, and fairness.
- **LLM insights** via Groq (optional) for executive summaries and feedback loops.

## Repository Layout

```
.
├── agents/                # Intelligent agents (forecast, fairness, cut advisor)
├── api/                   # FastAPI application and endpoints
├── config/                # Settings and environment template
├── data/                  # Local raw data staging (if used)
├── docs/                  # Architecture and agent documentation
├── etl/                   # Silver/Gold transformations and analytics
├── ingestion/             # Raw data ingestion scripts
├── n8n_webhooks/          # Webhook adapters for automation
├── sql/                   # Database schema and migrations
├── utils/                 # Shared helpers (db, logging, messaging)
├── test_agents.py         # Basic agent tests
├── requirements.txt
└── README.md
```

## Getting Started

### Prerequisites

- Python 3.11+
- PostgreSQL database (Neon or local)
- (Optional) Groq API key for LLM features

### Install

1. Create and activate a virtual environment.
2. Install dependencies from `requirements.txt`.
3. Copy the environment template and update values.
4. Initialize the database schema.

Example (Windows PowerShell):

```
python -m venv venv
venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy config\example.env .env
psql -f sql\schema.sql
```

## Configuration

Use `config/example.env` as a baseline. Key variables:

| Variable | Description |
| --- | --- |
| `NEON_DATABASE_URL` | PostgreSQL connection string |
| `GROQ_API_KEY` | Optional Groq key for LLM summaries |
| `GROQ_MODEL` | Model name (default `llama3-70b-8192`) |
| `RAW_DATA_DIR` | Local raw data directory |
| `PROSOL_REGIONS` | Comma-separated region list |
| `LOG_LEVEL` | Logging level (e.g., INFO) |
| `JWT_SECRET_KEY` | API auth secret |
| `SMTP_*` | Email notification settings |

## Usage

Typical workflow:

```
python ingestion\ingest_all.py
python etl\run_etl.py
```

## Agents & Orchestration

The main orchestration logic lives in `agents/agent_orchestrator.py` and
coordinates:

- Cut advisor recommendations
- Fairness evaluation
- Forecasting and anomaly detection

For webhook-based automation, see `n8n_webhooks/`.

## API

The FastAPI app lives in `api/app.py` and exposes:

- Authentication and supervisor management
- Citizen notifications and alert feeds
- Analytics endpoints and dashboards

To run locally:

```
uvicorn api.app:app --reload
```

## Data Sources

- STEG production and grid stability data
- PROSOL solar installation records
- World Bank energy indicators
- IEA regional benchmarks

## Tests

Run basic agent tests:

```
python test_agents.py
```
