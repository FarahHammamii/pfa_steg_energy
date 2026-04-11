import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, Any
import random
from utils.db import get_cursor, execute_batch
from utils.logger import get_logger

logger = get_logger(__name__)

class SyntheticDataGenerator:
    """Generates synthetic data for missing STEG information"""
    
    def __init__(self):
        pass
        
    def generate_recovery_times(self) -> pd.DataFrame:
        """Generate realistic recovery times per region"""
        
        regions = ['beja', 'bizerte', 'jendouba', 'kairouan', 'kef', 'mahdia', 
                   'mednine', 'monastir', 'naeul', 'sfax', 'sidibouzid', 
                   'siliana', 'sousse', 'tataouine', 'zaghouan']
        
        # Urban vs rural classification
        urban_regions = ['sfax', 'sousse', 'monastir', 'bizerte', 'kairouan']
        
        data = []
        for region in regions:
            if region in urban_regions:
                recovery_time = random.randint(30, 60)  # 30-60 minutes
                population_density = 'high'
            else:
                recovery_time = random.randint(120, 240)  # 2-4 hours
                population_density = 'low'
            
            data.append({
                'region': region,
                'avg_recovery_minutes': recovery_time,
                'population_density': population_density,
                'grid_quality': 'good' if region in urban_regions else 'fair'
            })
        
        df = pd.DataFrame(data)
        
        # Insert into silver schema
        with get_cursor() as cur:
            # First, create table if not exists
            cur.execute("""
                CREATE TABLE IF NOT EXISTS silver.recovery_times (
                    region TEXT PRIMARY KEY,
                    avg_recovery_minutes INT,
                    population_density TEXT,
                    grid_quality TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Upsert data
            for _, row in df.iterrows():
                cur.execute("""
                    INSERT INTO silver.recovery_times (region, avg_recovery_minutes, population_density, grid_quality)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (region) DO UPDATE SET
                        avg_recovery_minutes = EXCLUDED.avg_recovery_minutes,
                        population_density = EXCLUDED.population_density,
                        grid_quality = EXCLUDED.grid_quality
                """, (row['region'], row['avg_recovery_minutes'], row['population_density'], row['grid_quality']))
        
        logger.info(f"Generated {len(df)} recovery time records")
        return df
    
    def generate_daily_production(self, year: int = 2024) -> pd.DataFrame:
        """Generate daily production from monthly totals"""
        
        # Get monthly totals from gold layer
        query = f"""
        SELECT year, month, total_production_gwh
        FROM gold.monthly_production
        WHERE year = {year}
        ORDER BY month
        """
        
        with get_cursor(dict_cursor=True) as cur:
            cur.execute(query)
            monthly_data = cur.fetchall()
        
        if len(monthly_data) == 0:
            # Fallback: generate synthetic monthly data
            monthly_data = [{'month': m, 'total_production_gwh': np.random.uniform(1200, 2500)} 
                           for m in range(1, 13)]
        
        daily_records = []
        
        for row in monthly_data:
            month = int(row['month'])
            monthly_total = float(row['total_production_gwh'])
            
            # Days in month
            if month == 12:
                days_in_month = 31
            else:
                next_month = datetime(year, month + 1, 1)
                days_in_month = (next_month - timedelta(days=1)).day
            
            # Create daily distribution (peak in summer)
            daily_factors = []
            for day in range(1, days_in_month + 1):
                # Summer peak factor (July/August)
                if month in [7, 8]:
                    factor = np.random.uniform(0.9, 1.1)
                elif month in [6, 9]:
                    factor = np.random.uniform(0.85, 1.05)
                else:
                    factor = np.random.uniform(0.8, 1.0)
                
                daily_factors.append(factor)
            
            # Normalize factors to sum to monthly total
            factor_sum = sum(daily_factors)
            daily_values = [monthly_total * (f / factor_sum) for f in daily_factors]
            
            for day, value in enumerate(daily_values, 1):
                daily_records.append({
                    'date': datetime(year, month, day),
                    'year': year,
                    'month': month,
                    'day': day,
                    'production_gwh': round(value, 2),
                    'is_synthetic': True
                })
        
        df = pd.DataFrame(daily_records)
        
        # Create table and insert data
        with get_cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS silver.daily_production (
                    date DATE PRIMARY KEY,
                    year INT,
                    month INT,
                    day INT,
                    production_gwh DECIMAL(10,2),
                    is_synthetic BOOLEAN DEFAULT TRUE
                )
            """)
            
            for _, row in df.iterrows():
                cur.execute("""
                    INSERT INTO silver.daily_production (date, year, month, day, production_gwh, is_synthetic)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (date) DO UPDATE SET
                        production_gwh = EXCLUDED.production_gwh
                """, (row['date'], row['year'], row['month'], row['day'], 
                      row['production_gwh'], row['is_synthetic']))
        
        logger.info(f"Generated {len(df)} daily production records for {year}")
        return df
    
    def generate_cut_history(self, years: int = 3) -> pd.DataFrame:
        """Generate synthetic cut history for fairness agent"""
        
        regions = ['beja', 'bizerte', 'jendouba', 'kairouan', 'kef', 'mahdia', 
                   'mednine', 'monastir', 'naeul', 'sfax', 'sidibouzid', 
                   'siliana', 'sousse', 'tataouine', 'zaghouan']
        
        cuts = []
        start_date = datetime.now() - timedelta(days=years*365)
        
        for region in regions:
            # Some regions get cut more often (rural areas)
            if region in ['kef', 'siliana', 'tataouine', 'jendouba']:
                cut_probability = 0.3
            elif region in ['sfax', 'sousse', 'monastir']:
                cut_probability = 0.05
            else:
                cut_probability = 0.15
            
            current_date = start_date
            while current_date < datetime.now():
                if random.random() < cut_probability:
                    cuts.append({
                        'region': region,
                        'cut_date': current_date,
                        'duration_minutes': random.choice([30, 60, 90, 120]),
                        'reason': 'grid_overload',
                        'season': self._get_season(current_date)
                    })
                current_date += timedelta(days=random.randint(7, 30))
        
        df = pd.DataFrame(cuts)
        
        # Create table and insert data
        with get_cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS silver.cut_history (
                    id SERIAL PRIMARY KEY,
                    region TEXT,
                    cut_date TIMESTAMP,
                    duration_minutes INT,
                    reason TEXT,
                    season TEXT
                )
            """)
            
            # Insert all cuts
            for _, row in df.iterrows():
                cur.execute("""
                    INSERT INTO silver.cut_history (region, cut_date, duration_minutes, reason, season)
                    VALUES (%s, %s, %s, %s, %s)
                """, (row['region'], row['cut_date'], row['duration_minutes'], 
                      row['reason'], row['season']))
        
        logger.info(f"Generated {len(df)} cut history records")
        return df
    
    def _get_season(self, date: datetime) -> str:
        """Get season from date"""
        month = date.month
        if month in [12, 1, 2]:
            return 'winter'
        elif month in [3, 4, 5]:
            return 'spring'
        elif month in [6, 7, 8]:
            return 'summer'
        else:
            return 'fall'
    
    def generate_all(self):
        """Generate all synthetic data"""
        print("Generating synthetic recovery times...")
        recovery_df = self.generate_recovery_times()
        
        print("Generating daily production data...")
        daily_df = self.generate_daily_production()
        
        print("Generating cut history...")
        cuts_df = self.generate_cut_history()
        
        print(f"✅ Generated {len(recovery_df)} recovery records")
        print(f"✅ Generated {len(daily_df)} daily production records")
        print(f"✅ Generated {len(cuts_df)} cut history records")
        
        return {
            'recovery_times': len(recovery_df),
            'daily_production': len(daily_df),
            'cut_history': len(cuts_df)
        }