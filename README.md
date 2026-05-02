# STEG Energy Lakehouse

[![Python
3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-Neon-336791.svg)](https://neon.tech)
[![Groq](https://img.shields.io/badge/Groq-LLM-orange.svg)](https://groq.com)

A modern data lakehouse architecture for the Tunisian Electricity and
Gas Company (STEG), integrating operational data with external
benchmarks and LLM-powered analytics for energy performance
optimization.

## Table of Contents

-   Overview
-   Architecture
-   Features
-   Tech Stack
-   Project Structure
-   Installation
-   Configuration
-   Usage
-   Data Models
-   ETL Pipeline
-   LLM Analytics
-   Agents
-   Contributing
-   License

## Overview

This project implements a complete data lakehouse solution for STEG
(Tunisian Electricity and Gas Company) to: - Centralize energy
production, solar installation, and grid stability data - Benchmark
performance against Mediterranean peer countries - Generate AI-powered
strategic insights for decision-makers - Track renewable energy adoption
and CO2 reduction metrics

## Architecture

The solution follows a medallion architecture with three data layers:

Bronze Layer (Raw) - Raw CSV imports - World Bank API data - IEA
benchmark data

Silver Layer (Cleaned) - Data validation and cleaning - Type
conversions - Deduplication

Gold Layer (Aggregated) - Monthly production metrics - Solar impact
analysis - Grid stability reports - International benchmarks

LLM Analytics Layer - Strategic insights - Performance recommendations -
Peer comparison analysis

## Features

### Data Integration

-   STEG Production Data: Hourly electricity generation metrics
-   PROSOL Installations: Solar panel installations and capacity
-   Grid Stability: Incident tracking and outage analysis
-   World Bank API: International energy indicators (2020-2023)
-   IEA Benchmarks: Mediterranean peer performance data

### Analytics Capabilities

-   Renewable Share Tracking
-   CO2 Impact Analysis
-   Grid Reliability Metrics
-   International Benchmarking
-   LLM-Powered Insights using Groq Llama 3

### Data Quality

-   Automated validation and cleaning
-   Duplicate detection and removal
-   Type consistency enforcement
-   Foreign key integrity

## Tech Stack

-   Python 3.11+
-   PostgreSQL (Neon) with TimescaleDB
-   pandas, psycopg2
-   Groq API (Llama 3 70B)
-   World Bank Open Data, IEA
-   Custom logging
-   Environment variables (.env)

## Project Structure

steg-energy-lakehouse/ ├── agents/ ├── etl/ ├── bronze/ ├── silver/ ├──
gold/ ├── config/ ├── utils/ ├── data/ ├── requirements.txt ├──
.gitignore └── README.md

## Installation

### Prerequisites

-   Python 3.11 or higher
-   PostgreSQL database
-   Groq API key

### Setup

Clone the repository: git clone
https://github.com/FarahHammamii/pfa_steg_energy.git 
cd steg-energy-lakehouse

Create virtual environment: python -m venv venv source venv/bin/activate

Install dependencies: pip install -r requirements.txt

Configure environment: cp config/example.env .env

Initialize database schema: psql -f schema/create_tables.sql

## Configuration

Create a .env file:

NEON_DATABASE_URL=postgresql://username:password@host:5432/database
GROQ_API_KEY=your_groq_api_key_here

## Usage
Run ingestion: python ingestion/ingest_all.py
Run ETL pipeline: python etl/run_etl.py
