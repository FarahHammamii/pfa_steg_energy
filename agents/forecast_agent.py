import pandas as pd
import numpy as np
from typing import Dict, Any, List
from datetime import datetime, timedelta
from agents.base_agent import BaseAgent
from agents.data_loader import DataLoader
from utils.db import get_cursor
from utils.logger import get_logger
import os
import warnings
warnings.filterwarnings('ignore')

# Try to import sklearn
try:
    from sklearn.linear_model import LinearRegression
    from sklearn.preprocessing import PolynomialFeatures
    SKLEARN_AVAILABLE = True
except ImportError:
    SKLEARN_AVAILABLE = False

logger = get_logger(__name__)

# Groq setup
try:
    from groq import Groq
    GROQ_AVAILABLE = True
except ImportError:
    GROQ_AVAILABLE = False

groq_key = os.getenv("GROQ_API_KEY")
if GROQ_AVAILABLE and groq_key:
    client = Groq(api_key=groq_key)
    GROQ_MODEL = "llama-3.3-70b-versatile"
    logger.info("Groq client ready for Forecast Agent")
else:
    client = None


class ForecastAgent(BaseAgent):
    """Predicts future production using ML models"""
    
    def __init__(self):
        super().__init__("Forecast Agent")
        self.data_loader = DataLoader()
        self.model = None
        self.poly = None
        
    def analyze(self) -> Dict[str, Any]:
        """Generate production forecasts"""
        
        # Load historical production data
        production_df = self.data_loader.load_production_data()
        
        logger.info(f"Loaded production data: {len(production_df)} rows")
        
        if len(production_df) == 0:
            return {
                'status': 'no_data',
                'message': 'No production data available. Please load data first.',
                'forecasts': [],
                'anomalies_detected': [],
                'trend': 'unknown'
            }
        
        # Clean the data - convert to numeric and handle strings
        production_df = self._clean_data(production_df)
        
        if len(production_df) < 3:
            return self._fallback_forecast(production_df)
        
        # Prepare data for forecasting
        X, y = self._prepare_features(production_df)
        
        # Train model if sklearn available and enough data
        if SKLEARN_AVAILABLE and len(X) >= 4:
            self._train_model(X, y)
            forecasts = self._generate_forecasts(production_df)
            model_accuracy = self._calculate_accuracy(X, y)
        else:
            forecasts = self._simple_forecast(production_df)
            model_accuracy = {'accuracy_pct': 70, 'note': 'Using simple moving average'}
        
        # Detect anomalies
        anomalies = self._detect_anomalies(production_df)
        
        # Get trend
        trend = self._calculate_trend(production_df)
        
        # Get LLM explanation if available
        if client and forecasts:
            explanation = self._get_llm_explanation(forecasts, anomalies, trend, model_accuracy)
        else:
            explanation = self._get_fallback_explanation(forecasts, trend)
        
        return {
            'status': 'success',
            'forecasts': forecasts,
            'next_month_prediction': forecasts[0]['predicted_gwh'] if forecasts else None,
            'anomalies_detected': anomalies,
            'trend': trend,
            'model_accuracy': model_accuracy,
            'explanation': explanation
        }
    
    def _clean_data(self, df: pd.DataFrame) -> pd.DataFrame:
        """Clean data and convert to proper numeric types"""
        
        df_clean = df.copy()
        
        # Convert total_production_gwh to numeric, coercing errors to NaN
        if 'total_production_gwh' in df_clean.columns:
            df_clean['total_production_gwh'] = pd.to_numeric(df_clean['total_production_gwh'], errors='coerce')
        
        # Convert growth_rate_pct to numeric if it exists
        if 'growth_rate_pct' in df_clean.columns:
            df_clean['growth_rate_pct'] = pd.to_numeric(df_clean['growth_rate_pct'], errors='coerce')
        
        # Convert avg_renewable_share_pct to numeric if it exists
        if 'avg_renewable_share_pct' in df_clean.columns:
            df_clean['avg_renewable_share_pct'] = pd.to_numeric(df_clean['avg_renewable_share_pct'], errors='coerce')
        
        # Drop rows with NaN in total_production_gwh
        df_clean = df_clean.dropna(subset=['total_production_gwh'])
        
        # Ensure month column exists and is numeric
        if 'month' in df_clean.columns:
            df_clean['month'] = pd.to_numeric(df_clean['month'], errors='coerce')
            df_clean = df_clean.dropna(subset=['month'])
            df_clean['month'] = df_clean['month'].astype(int)
        elif 'month_name' in df_clean.columns:
            # Create month number from month_name
            month_map = {
                'January': 1, 'February': 2, 'March': 3, 'April': 4, 
                'May': 5, 'June': 6, 'July': 7, 'August': 8,
                'September': 9, 'October': 10, 'November': 11, 'December': 12
            }
            df_clean['month'] = df_clean['month_name'].map(month_map)
            df_clean = df_clean.dropna(subset=['month'])
            df_clean['month'] = df_clean['month'].astype(int)
        
        # Ensure year is numeric
        if 'year' in df_clean.columns:
            df_clean['year'] = pd.to_numeric(df_clean['year'], errors='coerce')
            df_clean = df_clean.dropna(subset=['year'])
            df_clean['year'] = df_clean['year'].astype(int)
        
        logger.info(f"Cleaned data: {len(df_clean)} rows remaining")
        
        return df_clean
    
    def _prepare_features(self, df: pd.DataFrame) -> tuple:
        """Prepare features for ML model"""
        
        # Create a copy to avoid warnings
        df_copy = df.copy()
        
        # Sort by year and month
        df_copy = df_copy.sort_values(['year', 'month'])
        
        # Create features
        df_copy['time_index'] = np.arange(len(df_copy), dtype=float)
        
        # Seasonal encoding
        df_copy['sin_month'] = np.sin(2 * np.pi * df_copy['month'] / 12)
        df_copy['cos_month'] = np.cos(2 * np.pi * df_copy['month'] / 12)
        
        # Year as float
        df_copy['year_float'] = df_copy['year'].astype(float)
        
        # Features: time_index, sin_month, cos_month, year_float
        X = df_copy[['time_index', 'sin_month', 'cos_month', 'year_float']].values.astype(float)
        y = df_copy['total_production_gwh'].values.astype(float)
        
        # Remove any NaN or infinite values
        mask = np.isfinite(y) & np.all(np.isfinite(X), axis=1)
        X = X[mask]
        y = y[mask]
        
        logger.info(f"Prepared {len(X)} feature vectors")
        
        return X, y
    
    def _train_model(self, X, y):
        """Train polynomial regression model"""
        if not SKLEARN_AVAILABLE or len(X) < 4:
            return
        
        try:
            # Add polynomial features
            self.poly = PolynomialFeatures(degree=2, include_bias=False)
            X_poly = self.poly.fit_transform(X)
            
            # Train model
            self.model = LinearRegression()
            self.model.fit(X_poly, y)
            logger.info("Forecast model trained successfully")
        except Exception as e:
            logger.error(f"Model training failed: {e}")
            self.model = None
            self.poly = None
    
    def _generate_forecasts(self, df: pd.DataFrame) -> List[Dict]:
        """Generate forecasts for next 3 months"""
        
        forecasts = []
        last_time_index = len(df)
        
        # Get current date info
        current_date = datetime.now()
        current_month = current_date.month
        current_year = current_date.year
        
        # Get the last few values to detect trend
        last_values = df.tail(6)['total_production_gwh'].values if len(df) >= 6 else df['total_production_gwh'].values
        trend_factor = 1.0
        if len(last_values) >= 3:
            # Calculate simple trend
            if last_values[-1] > last_values[-3]:
                trend_factor = 1.02  # Slight increase
            elif last_values[-1] < last_values[-3]:
                trend_factor = 0.98  # Slight decrease
        
        for i in range(1, 4):
            # Calculate next month
            next_month_num = current_month + i
            next_year = current_year
            if next_month_num > 12:
                next_month_num -= 12
                next_year += 1
            
            # Get month name
            month_names = ['January', 'February', 'March', 'April', 'May', 'June',
                          'July', 'August', 'September', 'October', 'November', 'December']
            next_month_name = month_names[next_month_num - 1]
            
            # Get historical average for this month
            historical_same_month = df[df['month'] == next_month_num]['total_production_gwh'].values
            if len(historical_same_month) > 0:
                base_prediction = np.mean(historical_same_month)
            else:
                base_prediction = np.mean(df['total_production_gwh'].values)
            
            # Apply trend factor
            prediction = base_prediction * (trend_factor ** i)
            
            # Adjust for summer peaks (July/August)
            if next_month_num in [7, 8]:
                prediction *= 1.10
            elif next_month_num in [6, 9]:
                prediction *= 1.05
            
            forecasts.append({
                'month': next_month_name,
                'month_num': next_month_num,
                'year': next_year,
                'predicted_gwh': round(float(prediction), 2),
                'confidence_lower': round(float(prediction) * 0.85, 2),
                'confidence_upper': round(float(prediction) * 1.15, 2)
            })
        
        return forecasts
    
    def _simple_forecast(self, df: pd.DataFrame) -> List[Dict]:
        """Simple forecast using moving average"""
        
        forecasts = []
        current_date = datetime.now()
        
        # Calculate weighted average (give more weight to recent data)
        n = len(df)
        if n > 0:
            values = df['total_production_gwh'].values
            if n >= 3:
                # Use last 3 months weighted average
                weights = np.array([0.5, 0.3, 0.2][:n])
                weights = weights / weights.sum()
                weighted_avg = np.average(values[-min(3, n):], weights=weights[-min(3, n):])
            else:
                weighted_avg = np.mean(values)
        else:
            weighted_avg = 1500  # Default fallback
        
        for i in range(1, 4):
            next_date = current_date.replace(day=1) + timedelta(days=32*i)
            next_month = next_date.strftime('%B')
            next_month_num = next_date.month
            
            # Adjust for seasonality
            seasonal_factor = 1.0
            if next_month_num in [7, 8]:  # Summer peak
                seasonal_factor = 1.15
            elif next_month_num in [12, 1, 2]:  # Winter
                seasonal_factor = 0.95
            
            prediction = weighted_avg * seasonal_factor
            
            forecasts.append({
                'month': next_month,
                'month_num': next_month_num,
                'year': next_date.year,
                'predicted_gwh': round(prediction, 2),
                'confidence_lower': round(prediction * 0.8, 2),
                'confidence_upper': round(prediction * 1.2, 2)
            })
        
        return forecasts
    
    def _detect_anomalies(self, df: pd.DataFrame) -> List[Dict]:
        """Detect anomalous production patterns"""
        
        anomalies = []
        
        if len(df) < 6:
            return anomalies
        
        # Calculate rolling averages using numeric values
        values = df['total_production_gwh'].values
        ma_3 = []
        std_3 = []
        
        for i in range(len(values)):
            start = max(0, i-2)
            window = values[start:i+1]
            ma_3.append(np.mean(window))
            std_3.append(np.std(window) if len(window) > 1 else 0)
        
        # Detect anomalies (values outside 2 standard deviations)
        for i in range(len(values)):
            if std_3[i] > 0:
                z_score = abs(values[i] - ma_3[i]) / std_3[i]
                if z_score > 2:
                    year_val = df.iloc[i]['year']
                    month_name = df.iloc[i].get('month_name', f"Month {df.iloc[i]['month']}")
                    anomalies.append({
                        'date': f"{year_val} {month_name}",
                        'production_gwh': float(values[i]),
                        'expected_gwh': round(float(ma_3[i]), 2),
                        'deviation_pct': round(((float(values[i]) - float(ma_3[i])) / float(ma_3[i])) * 100, 2)
                    })
        
        return anomalies
    
    def _calculate_trend(self, df: pd.DataFrame) -> str:
        """Calculate overall production trend"""
        
        if len(df) < 6:
            return "insufficient_data"
        
        # Use last 12 months or all if less
        values = df.tail(min(12, len(df)))['total_production_gwh'].values
        if len(values) > 1:
            x = np.arange(len(values))
            slope = np.polyfit(x, values, 1)[0]
            # Normalize slope by average value
            avg_val = np.mean(values)
            if avg_val > 0:
                relative_slope = (slope / avg_val) * 100
                if relative_slope > 2:
                    return "increasing"
                elif relative_slope < -2:
                    return "decreasing"
        
        return "stable"
    
    def _calculate_accuracy(self, X, y) -> Dict[str, float]:
        """Calculate model accuracy metrics"""
        
        if not SKLEARN_AVAILABLE or not self.model or not self.poly:
            return {'accuracy_pct': 70, 'note': 'Using simple forecasting'}
        
        try:
            # Predict on training data
            X_poly = self.poly.transform(X)
            predictions = self.model.predict(X_poly)
            
            mae = np.mean(np.abs(y - predictions))
            
            # Calculate MAPE carefully
            y_nonzero = y[y != 0]
            pred_nonzero = predictions[y != 0]
            if len(y_nonzero) > 0:
                mape = np.mean(np.abs((y_nonzero - pred_nonzero) / y_nonzero)) * 100
                accuracy_pct = max(0, min(100, 100 - mape))
            else:
                accuracy_pct = 70
            
            return {
                'mae_gwh': round(float(mae), 2),
                'accuracy_pct': round(float(accuracy_pct), 1),
                'mape_pct': round(float(mape), 1) if 'mape' in locals() else None
            }
        except Exception as e:
            logger.error(f"Accuracy calculation failed: {e}")
            return {'accuracy_pct': 70, 'note': 'Estimate based on historical data'}
    
    def _get_llm_explanation(self, forecasts: List, anomalies: List, trend: str, accuracy: Dict) -> str:
        """Use Groq to explain forecast"""
        
        context = f"""
Production Forecast:
- Next month ({forecasts[0]['month']} {forecasts[0]['year']}): {forecasts[0]['predicted_gwh']} GWh
- Month 2 ({forecasts[1]['month']} {forecasts[1]['year']}): {forecasts[1]['predicted_gwh']} GWh
- Month 3 ({forecasts[2]['month']} {forecasts[2]['year']}): {forecasts[2]['predicted_gwh']} GWh

Overall Trend: {trend}

Anomalies Detected: {len(anomalies)}
{chr(10).join([f"- {a['date']}: {a['deviation_pct']}% deviation" for a in anomalies[:3]]) if anomalies else 'No anomalies detected'}

Model Accuracy: {accuracy.get('accuracy_pct', 'N/A')}%
"""
        
        prompt = f"""
You are STEG's production forecasting analyst.

INSTRUCTIONS:
- Answer ONLY using the provided context.
- DO NOT use your prior knowledge.
- Explain the forecast briefly and note any concerns.
- Keep explanation to 2-3 sentences.

==================== CONTEXT ====================
{context}

==================== QUESTION ====================
What does the production forecast show for the next 3 months? Are there any concerns?

==================== ANSWER ====================
"""
        
        try:
            response = client.chat.completions.create(
                model=GROQ_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=512
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            logger.error(f"Groq API error: {e}")
            return self._get_fallback_explanation(forecasts, trend)
    
    def _get_fallback_explanation(self, forecasts: List, trend: str) -> str:
        """Fallback explanation without LLM"""
        if forecasts:
            summer_months = ['June', 'July', 'August', 'September']
            is_summer = any(f['month'] in summer_months for f in forecasts[:2])
            if is_summer:
                return f"Next month's production is forecasted at {forecasts[0]['predicted_gwh']} GWh. Summer peak expected - prepare for increased demand."
            return f"Next month's production is forecasted at {forecasts[0]['predicted_gwh']} GWh. The overall trend is {trend}."
        return f"Production trend is {trend}. Monitor for seasonal variations."
    
    def _fallback_forecast(self, df: pd.DataFrame) -> Dict[str, Any]:
        """Fallback forecast when insufficient data"""
        
        if len(df) > 0:
            # Clean the data first
            df_clean = self._clean_data(df)
            if len(df_clean) > 0:
                avg_production = float(df_clean['total_production_gwh'].mean())
                return {
                    'status': 'limited_data',
                    'forecasts': [
                        {
                            'month': datetime.now().strftime('%B'),
                            'year': datetime.now().year,
                            'predicted_gwh': round(avg_production, 2),
                            'confidence_lower': round(avg_production * 0.8, 2),
                            'confidence_upper': round(avg_production * 1.2, 2)
                        }
                    ],
                    'message': 'Limited historical data. Using average values.',
                    'trend': 'unknown',
                    'anomalies_detected': [],
                    'model_accuracy': {'accuracy_pct': 60, 'note': 'Limited data'}
                }
        
        return {
            'status': 'no_data',
            'message': 'No production data available for forecasting',
            'forecasts': [],
            'anomalies_detected': [],
            'trend': 'unknown'
        }