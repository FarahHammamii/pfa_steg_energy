import pandas as pd
from utils.db import get_cursor
from utils.logger import get_logger

logger = get_logger(__name__)

class DataLoader:
    """Loads data from STEG lakehouse for agents"""
    
    def __init__(self):
        pass
    
    def load_solar_data(self) -> pd.DataFrame:
        """Load solar installation data per region from gold.solar_impact"""
        query = """
        SELECT 
            region,
            year,
            total_installations,
            total_surface_m2,
            co2_saved_tons,
            total_investment_dt,
            avg_subsidy_pct
        FROM gold.solar_impact
        ORDER BY region, year
        """
        return self._query_to_df(query)
    
    def load_production_data(self) -> pd.DataFrame:
        """Load monthly production data from gold.monthly_production"""
        query = """
        SELECT 
            year,
            month,
            month_name,
            total_production_gwh,
            avg_renewable_share_pct,
            growth_rate_pct
        FROM gold.monthly_production
        ORDER BY year, month
        """
        return self._query_to_df(query)
    
    def load_incidents_data(self) -> pd.DataFrame:
        """Load grid stability incidents from gold.grid_stability"""
        query = """
        SELECT 
            year,
            quarter,
            incident_count,
            power_loss_mw,
            recovery_time_avg_minutes,
            total_duration_minutes
        FROM gold.grid_stability
        ORDER BY year, quarter
        """
        return self._query_to_df(query)
    
    def load_regional_summary(self) -> pd.DataFrame:
        """Load regional solar summary from gold.solar_impact"""
        query = """
        SELECT 
            region,
            SUM(total_installations) as total_installations,
            SUM(co2_saved_tons) as total_co2_saved,
            AVG(avg_subsidy_pct) as avg_subsidy,
            SUM(total_investment_dt) as total_investment_dt
        FROM gold.solar_impact
        GROUP BY region
        ORDER BY total_installations DESC
        """
        return self._query_to_df(query)
    
    def _query_to_df(self, query: str) -> pd.DataFrame:
        """Execute query and return DataFrame using existing db utilities"""
        try:
            with get_cursor(dict_cursor=True) as cur:
                cur.execute(query)
                rows = cur.fetchall()
                if rows:
                    return pd.DataFrame(rows)
                return pd.DataFrame()
        except Exception as e:
            logger.error(f"Query failed: {str(e)}")
            return pd.DataFrame()