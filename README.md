# Energy Consumption Streaming Pipeline

[![Python 3.8+](https://img.shields.io/badge/python-3.8%2B-blue)](https://www.python.org/)
[![scikit-learn](https://img.shields.io/badge/sklearn-1.0%2B-blue)](https://scikit-learn.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A lightweight, self-correcting machine learning pipeline for **real-time daily energy consumption prediction** on your homeserver. Convert monthly batch predictions to streaming daily predictions with automatic weekly retraining.

## 🎯 Problem & Solution

### The Problem
- You have a **monthly energy prediction model** trained on aggregate data
- You want **daily predictions** with **real-time feedback**
- Each day you get actual consumption, but your model doesn't learn from it
- No feedback loop = predictions get stale

### The Solution
This pipeline:
1. **Streams one day at a time** through your CSV (simulating real-time data)
2. **Predicts daily consumption** using historical patterns
3. **Compares prediction vs actual** and logs the error
4. **Auto-retrains weekly** to self-correct using recent data
5. **Runs fully automated** via cron on your homeserver

```
Your CSV (365 days of daily consumption)
        ↓
[Cron job triggers daily]
        ↓
Read day N consumption → Predict day N+1 → Store result
        ↓
[Every 7 days: retrain on error feedback]
        ↓
Improved model for better predictions
```

## ✨ Key Features

- ✅ **Streaming predictions** — process one day at a time, not bulk batches
- ✅ **Self-correcting** — automatic weekly retraining with error feedback
- ✅ **Lightweight** — pure Python, no Docker, no cloud, runs on any homeserver
- ✅ **Observable** — detailed logs track predictions, errors, and model performance
- ✅ **Extensible** — easy to add custom features, change models, adjust retraining frequency
- ✅ **Low-latency** — predictions compute in milliseconds
- ✅ **Automated** — cron-based scheduling, zero manual intervention

## 🚀 Quick Start

### Installation (2 minutes)

```bash
# Clone or download the files
cd ~/energy_pipeline

# Install dependencies
pip install pandas scikit-learn numpy

# Run one-time setup
python3 setup_energy_pipeline.py
```

When prompted, provide the path to your CSV file (e.g., `/home/user/consumption_2025.csv`).

### Setup Cron (1 minute)

The setup script prints a cron line. Add it to your crontab:

```bash
crontab -e
# Paste the printed line, save and exit
```

That's it! Pipeline runs automatically every day. ✅

### Verify It Works

```bash
# Test manually first
python3 energy_pipeline.py

# Check predictions
cat logs/predictions.csv

# Watch daily predictions (after cron runs)
tail -f logs/predictions.csv
```

## 📖 Documentation

| Document | Purpose |
|----------|---------|
| **QUICK_START.md** | Step-by-step setup (10 min) |
| **ENERGY_PIPELINE_README.md** | Complete guide + troubleshooting |
| **This file** | Architecture & overview |

Start with **QUICK_START.md** if this is your first time!

## 🏗️ Architecture

### Daily Workflow

```
1:00 AM — Cron triggers
    ↓
Load state: current_day = N
    ↓
Read CSV line N (today's consumption)
    ↓
Load trained model & scaler
    ↓
Create features from historical data
    ↓
Predict tomorrow's consumption
    ↓
Store actual vs predicted in logs
    ↓
Check: is it time to retrain? (weekly)
    │
    ├─ NO  → Save state, exit
    │
    └─ YES → Retrain on last 7 days
            ↓
            Train new RandomForest
            ↓
            Save model + scaler
            ↓
            Update last_retrain timestamp
            ↓
            Save state, exit
```

### Self-Correction Cycle

```
Week 1: Daily predictions
Jan 1-7  Predict | Actual | Error
Day 1    45.2    | 45.2   | 0.0
Day 2    47.1    | 48.1   | -1.0 (underpredicted)
Day 3    42.8    | 42.5   | +0.3
...
Average error: 1.2 kWh

         ↓↓↓ RETRAIN on real data ↓↓↓

Week 2: Improved predictions
Day 8    46.8    | 47.2   | -0.4 ✅ (error reduced)
Day 9    45.8    | 45.6   | +0.2 ✅ (error reduced)
Average error: 0.4 kWh
```

### File Structure

```
energy_pipeline/
├── energy_pipeline.py          # Main entry point (run by cron)
├── setup_energy_pipeline.py    # One-time setup script
├── consumption_2025.csv        # Your consumption data
├── QUICK_START.md              # Get started in 10 min
├── ENERGY_PIPELINE_README.md   # Full documentation
├── README.md                   # This file
│
├── data/
│   └── pipeline_state.json     # Current day, last retrain time
│
├── models/
│   ├── model.pkl              # Trained RandomForest
│   └── scaler.pkl             # Feature scaler
│
└── logs/
    ├── pipeline_202501.log     # Daily run logs
    ├── predictions.csv         # All predictions (actual vs predicted)
    └── cron.log               # Cron output
```

## 📊 Data Format

Your CSV must have at least two columns:

```csv
date,consumption
2025-01-01,45.2
2025-01-02,48.1
2025-01-03,42.5
...
```

- **date**: Any date format (YYYY-MM-DD recommended)
- **consumption**: Daily energy consumption (numeric)

See `example_consumption.csv` for a template.

## 🔧 Configuration

All settings are in `energy_pipeline.py`:

```python
# Change the CSV path
CSV_PATH = "path/to/your/consumption.csv"

# Change retraining frequency (default: 7 days)
def should_retrain(last_retrain):
    return (datetime.now() - last_retrain).days >= 7  # ← change 7 to 3 for weekly→every 3 days

# Customize features
def create_features(df, day_idx):
    # Add your own features here
    features = {
        'prev_consumption': ...,
        'my_custom_feature': ...,
    }
```

## 📈 Monitoring

### Real-Time Predictions

```bash
# Watch predictions as they come in
tail -f logs/predictions.csv
```

Output:
```
date,day_idx,actual_consumption,predicted_consumption,error,abs_error
2025-01-02,1,48.10,45.23,2.87,2.87
2025-01-03,2,42.50,47.10,-4.60,4.60
2025-01-09,7,52.30,51.80,0.50,0.50    ← errors shrinking!
```

### Model Performance

```bash
# Check if error is improving
python3 -c "
import pandas as pd
df = pd.read_csv('logs/predictions.csv')
print(f'First week MAE:  {df.head(7)[\"abs_error\"].mean():.2f} kWh')
print(f'Latest week MAE: {df.tail(7)[\"abs_error\"].mean():.2f} kWh')
"
```

### Logs

```bash
# See daily runs
tail -f logs/pipeline_202501.log

# See cron output
tail -f logs/cron.log

# Search for errors
grep ERROR logs/pipeline_202501.log
```

Example log output:
```
2025-01-08 01:00:22 | INFO | Processing day 8/365: 2025-01-08
2025-01-08 01:00:23 | INFO | Day 8: actual=52.30, predicted=51.80, error=0.50
2025-01-08 01:00:23 | INFO | WEEKLY RETRAIN TRIGGERED
2025-01-08 01:00:24 | INFO | Model trained on 7 samples | MAE: 2.14 | RMSE: 2.68
2025-01-08 01:00:24 | INFO | Model and scaler saved
```

## 🔄 How Self-Correction Works

### Feedback Loop

1. **Make prediction** (using historical data)
2. **Compare to actual** (when the day is over)
3. **Log error** (prediction - actual)
4. **After 7 days** → retrain model on those 7 days of errors
5. **Model adjusts** → learns from mistakes
6. **Repeat** → next week's predictions are better

### Example: Underpredicting Weekends

- Week 1: Model predicts 45 kWh for Saturdays, actual is 52 kWh
  - Error: -7 kWh (consistently underpredicting)
  - Logged daily in `predictions.csv`
  
- Week 1 ends: Retrain happens
  - Model sees pattern: "Saturdays are 7 kWh higher"
  - Adjusts weights for weekends
  
- Week 2: Model predicts 52 kWh for Saturdays
  - Error: -0.3 kWh ✅ (much better!)

## 🛠️ Customization

### Add Custom Features

Edit `create_features()` in `energy_pipeline.py`:

```python
def create_features(df, day_idx):
    hist = df.iloc[:day_idx]
    features = {
        'prev_consumption': hist['consumption'].iloc[-1],
        'rolling_7day': hist['consumption'].iloc[-7:].mean(),
        
        # Add your own:
        'temperature': external_temp_api.get(date),  # external data source
        'is_holiday': holiday_calendar.is_holiday(date),
        'hour_of_day': df['date'].iloc[day_idx].hour,
    }
    return features
```

### Change the Model

Replace RandomForest with any sklearn model:

```python
# In train_model()
from sklearn.ensemble import GradientBoostingRegressor  # or XGBoost, LSTM, etc.

model = GradientBoostingRegressor(n_estimators=100, max_depth=5)
model.fit(X_scaled, y)
```

### Speed Up/Slow Down Retraining

```python
# Retrain every 3 days instead of 7
def should_retrain(last_retrain):
    return (datetime.now() - last_retrain).days >= 3
```

### Adjust Cron Schedule

```bash
# Edit crontab
crontab -e

# Run at 2:00 AM instead of 1:00 AM:
0 2 * * * /usr/bin/python3 /path/to/energy_pipeline.py

# Run twice daily (1 AM and 6 PM):
0 1 * * * /usr/bin/python3 /path/to/energy_pipeline.py
0 18 * * * /usr/bin/python3 /path/to/energy_pipeline.py
```

## ⚠️ Troubleshooting

### Cron not running?

```bash
# Check if cron is installed
sudo systemctl status cron

# Verify your job is there
crontab -l

# Check cron logs
grep CRON /var/log/syslog | tail -20

# Check pipeline logs
tail -f logs/cron.log
```

### CSV not found?

```bash
# Verify path
ls -la /path/to/your/consumption.csv

# Update CSV_PATH in energy_pipeline.py
# Change: CSV_PATH = "consumption_2025.csv"
# To:     CSV_PATH = "/absolute/path/to/consumption.csv"
```

### Model performance degrading?

```bash
# Check error trend
python3 -c "
import pandas as pd
df = pd.read_csv('logs/predictions.csv')
print('Recent 10 days MAE:', df.tail(10)['abs_error'].mean())
print('Overall MAE:', df['abs_error'].mean())
"

# Options:
# 1. Add better features (temperature, holidays, etc.)
# 2. Retrain more frequently (change days_since >= 7 to >= 3)
# 3. Delete models/ and retrain from scratch
```

### Not enough data?

```bash
# Pipeline needs at least 10 days to train
# Until then: predictions = previous day's consumption
# This is OK; error will improve as data accumulates

# Force train on days 1-N:
python3 -c "
from energy_pipeline import train_model, load_csv
df = load_csv()
model, scaler = train_model(df, up_to_day=30)
"
```

Full troubleshooting in **ENERGY_PIPELINE_README.md**.

## 📦 Dependencies

```
pandas >= 1.0
scikit-learn >= 0.24
numpy >= 1.19
```

Install:
```bash
pip install pandas scikit-learn numpy
```

## 🔐 Privacy & Data

- **No cloud** — everything runs on your homeserver
- **No external APIs** — except if you add custom features
- **Data stays local** — logs and models are in your `~/energy_pipeline/` directory
- **No telemetry** — pipeline doesn't report anything

## 📊 Use Cases

- **Smart home optimization** — predict peak hours, pre-cool/pre-heat
- **Energy trading** — forecast consumption for day-ahead markets
- **Anomaly detection** — alert when consumption deviates from predictions
- **Demand response** — optimize when to run appliances
- **Cost analysis** — understand consumption patterns and trends
- **Grid planning** — aggregate predictions for microgrid management

## 🎓 Learning

The code is heavily commented and teaches:
- **Time-series ML** — feature engineering from historical data
- **Online learning** — retraining on streaming feedback
- **Production ML** — logging, monitoring, error handling
- **Cron scheduling** — running jobs on Linux
- **scikit-learn** — model training and prediction

## 🤝 Contributing

Ideas for improvement:

1. **Add external data** — temperature, humidity, day type (weekday/holiday)
2. **Try advanced models** — Prophet, LSTM, XGBoost
3. **Implement ensemble** — combine multiple models
4. **Add API** — serve predictions via REST endpoint
5. **Web dashboard** — visualize predictions vs actual
6. **Database backend** — store in InfluxDB/Prometheus instead of CSV
7. **Alert system** — notify when error exceeds threshold

## 📝 License

MIT License — use freely for personal/research/commercial use.

## 📬 Support

**Questions?**
1. Check **QUICK_START.md** for setup help
2. See **ENERGY_PIPELINE_README.md** for detailed explanations
3. Review code comments in `energy_pipeline.py`
4. Check logs: `tail -f logs/pipeline_*.log`

**Issues?**
1. Verify CSV format: `head -5 your_file.csv`
2. Test manually: `python3 energy_pipeline.py`
3. Check permissions: `ls -la energy_pipeline.py`
4. Ensure deps: `pip install --upgrade pandas scikit-learn numpy`

## 🚀 Next Steps

1. **Read** → QUICK_START.md (10 min)
2. **Setup** → Run setup_energy_pipeline.py
3. **Test** → python3 energy_pipeline.py
4. **Deploy** → Add to crontab
5. **Monitor** → tail -f logs/predictions.csv
6. **Customize** → Edit features, add new data sources
7. **Learn** → Read ENERGY_PIPELINE_README.md for advanced topics

---

**Ready?** Start with [QUICK_START.md](QUICK_START.md) 🎯
