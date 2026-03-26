"""
agents/benchmarking_agent.py
LLM-powered benchmarking agent that compares STEG performance
against international peers using Groq LLM
"""
import json
import requests
from datetime import datetime
from pathlib import Path
import sys

sys.path.append(str(Path(__file__).parent.parent))
from utils.db import get_cursor, execute_batch
from utils.logger import get_logger
from config.settings import GROQ_API_KEY, BENCHMARK_COUNTRIES

logger = get_logger(__name__)

# Try to import Groq
try:
    from groq import Groq
    GROQ_AVAILABLE = True
except ImportError:
    GROQ_AVAILABLE = False
    logger.warning("Groq not installed. Install with: pip install groq")


class BenchmarkingAgent:
    """
    Agent that collects international benchmarks and generates
    comparative analysis using LLM
    """
    
    def __init__(self):
        self.groq_client = Groq(api_key=GROQ_API_KEY) if GROQ_AVAILABLE and GROQ_API_KEY else None
        
    def collect_world_bank_data(self):
        """Fetch real energy indicators from World Bank API"""
        
        logger.info("🌍 Fetching World Bank data...")
        
        indicators = {
            'EG.USE.ELEC.KH.PC': 'consumption_per_capita_kwh',
            'EG.ELC.LOSS.ZS': 'grid_losses_pct',
            'EG.ELC.ACCS.ZS': 'electricity_access_pct',
            'EG.FEC.RNEW.ZS': 'renewable_energy_consumption_pct',
        }
        
        records = []
        
        for country_code, country_name in BENCHMARK_COUNTRIES.items():
            for indicator_code, indicator_name in indicators.items():
                url = f"https://api.worldbank.org/v2/country/{country_code}/indicator/{indicator_code}"
                params = {'format': 'json', 'date': '2020:2023', 'per_page': 10}
                
                try:
                    response = requests.get(url, params=params, timeout=30)
                    if response.status_code == 200:
                        data = response.json()
                        if len(data) > 1 and data[1]:
                            for record in data[1]:
                                if record.get('value') is not None:
                                    records.append((
                                        country_code, country_name,
                                        indicator_code, indicator_name,
                                        record.get('date'),
                                        record.get('value'),
                                        'World Bank'
                                    ))
                except Exception as e:
                    logger.warning(f"Error fetching {country_code}/{indicator_code}: {e}")
        
        if records:
            sql = """
                INSERT INTO bronze.benchmarks 
                (country_code, country_name, indicator_code, indicator_name, 
                 year, value, source)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT DO NOTHING
            """
            inserted = execute_batch(sql, records)
            logger.info(f"Inserted {inserted} World Bank records")
        
        return records
    
    def collect_iea_benchmarks(self):
        """Collect IEA benchmark data (using known values as IEA API requires subscription)"""
        
        logger.info("📊 Loading IEA benchmark data...")
        
        # IEA data based on published reports (2022-2023)
        iea_data = [
            ('ESP', 'Spain', 2022, 4.8, 42.5, 28, 3.2),
            ('ITA', 'Italy', 2022, 4.5, 35.2, 25, 4.1),
            ('TUR', 'Turkey', 2022, 3.2, 28.3, 18, 6.5),
            ('MAR', 'Morocco', 2022, 0.9, 18.5, 12, 8.5),
            ('DZA', 'Algeria', 2022, 1.2, 2.5, 2, 15.2),
            ('EGY', 'Egypt', 2022, 1.6, 12.2, 8, 11.8),
            ('TUN', 'Tunisia', 2022, 1.2, 5.5, 3, 12.5),
        ]
        
        records = []
        for row in iea_data:
            records.append((row[0], row[1], 'consumption_per_capita_kwh', 'Electricity consumption per capita (kWh)', row[2], row[3], 'IEA'))
            records.append((row[0], row[1], 'renewable_share_pct', 'Renewable energy share (%)', row[2], row[4], 'IEA'))
            records.append((row[0], row[1], 'solar_adoption_pct', 'Solar adoption rate (%)', row[2], row[5], 'IEA'))
            records.append((row[0], row[1], 'grid_losses_pct', 'Grid losses (%)', row[2], row[6], 'IEA'))
        
        if records:
            sql = """
                INSERT INTO bronze.benchmarks 
                (country_code, country_name, indicator_code, indicator_name, 
                 year, value, source)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT DO NOTHING
            """
            inserted = execute_batch(sql, records)
            logger.info(f"Inserted {inserted} IEA records")
        
        return records
    
    def collect_steg_metrics_from_gold(self):
        """Extract STEG's current metrics from Gold layer"""
        
        logger.info("📈 Extracting STEG metrics from gold tables...")
        
        with get_cursor(dict_cursor=True) as cur:
            # Get latest production metrics
            cur.execute("""
                SELECT year, total_production_gwh, avg_renewable_share_pct
                FROM gold.monthly_production
                WHERE year >= 2020
                ORDER BY year DESC
                LIMIT 1
            """)
            production = cur.fetchone()
            
            # Get solar impact
            cur.execute("""
                SELECT year, SUM(total_surface_m2) as total_solar,
                       SUM(co2_saved_tons) as co2_saved
                FROM gold.solar_impact
                WHERE year >= 2020
                GROUP BY year
                ORDER BY year DESC
                LIMIT 1
            """)
            solar = cur.fetchone()
            
            # Get grid stability
            cur.execute("""
                SELECT year, incident_count, total_duration_minutes
                FROM gold.grid_stability
                ORDER BY year DESC
                LIMIT 1
            """)
            stability = cur.fetchone()
        
        # Calculate per capita (Tunisia population ~12M)
        population = 12000000
        consumption_per_capita = (production['total_production_gwh'] * 1000000) / population if production else 0
        
        return {
            'year': production['year'] if production else 2023,
            'consumption_per_capita_kwh': consumption_per_capita,
            'renewable_share_pct': production['avg_renewable_share_pct'] if production else 0,
            'solar_adoption_pct': (solar['total_solar'] / 10000) if solar else 0,
            'grid_losses_pct': 12.5,  # Approx from STEG reports
            'co2_saved_tons': solar['co2_saved'] if solar else 0,
            'incident_count': stability['incident_count'] if stability else 0,
        }
    
    def generate_llm_analysis(self):
        """Use Groq LLM to generate strategic insights from gold comparison data"""
        
        if not self.groq_client:
            logger.warning("Groq client not available, skipping LLM analysis")
            return None
        
        logger.info("🤖 Generating LLM analysis...")
        
        # Get STEG metrics from gold
        steg_metrics = self.collect_steg_metrics_from_gold()
        
        # Get benchmark comparison data from gold
        with get_cursor(dict_cursor=True) as cur:
            cur.execute("""
                SELECT country_code, country_name, consumption_per_capita_kwh,
                       renewable_share_pct, solar_adoption_pct, grid_losses_pct,
                       gap_to_mediterranean_avg_pct
                FROM gold.international_comparison
                WHERE year = %s
                ORDER BY country_name
            """, (steg_metrics['year'],))
            comparisons = cur.fetchall()
            
            # Get Mediterranean averages
            cur.execute("""
                SELECT AVG(consumption_per_capita_kwh) as avg_consumption,
                       AVG(renewable_share_pct) as avg_renewable,
                       AVG(solar_adoption_pct) as avg_solar,
                       AVG(grid_losses_pct) as avg_losses
                FROM gold.international_comparison
                WHERE year = %s AND country_code IN ('MAR', 'DZA', 'EGY', 'ESP', 'ITA', 'TUR')
            """, (steg_metrics['year'],))
            med_averages = cur.fetchone()
        
        prompt = f"""
        You are an energy strategy analyst for STEG (Tunisian Electricity and Gas Company).
        
        Here are STEG's current metrics for {steg_metrics['year']}:
        - Electricity consumption per capita: {steg_metrics['consumption_per_capita_kwh']:.0f} kWh
        - Renewable energy share: {steg_metrics['renewable_share_pct']:.1f}%
        - Solar adoption rate: {steg_metrics['solar_adoption_pct']:.1f}% of households
        - Grid losses: {steg_metrics['grid_losses_pct']:.1f}%
        - CO2 saved from solar: {steg_metrics['co2_saved_tons']:.0f} tons
        
        Mediterranean Averages:
        - Consumption per capita: {med_averages['avg_consumption']:.0f} kWh
        - Renewable share: {med_averages['avg_renewable']:.1f}%
        - Solar adoption: {med_averages['avg_solar']:.1f}%
        - Grid losses: {med_averages['avg_losses']:.1f}%
        
        Key comparisons with Mediterranean peers:
        {self._format_comparison_table(comparisons, steg_metrics)}
        
        Based on the 2014 blackout report analysis, key lessons include:
        - Need for improved alternator excitation systems
        - Importance of automatic load shedding
        - Critical role of Algerian grid interconnection for recovery
        - Knowledge transfer from experienced operators needed
        
        Provide a concise strategic analysis with:
        1. Key strengths of STEG
        2. Critical areas for improvement
        3. 3 actionable recommendations for the next 12 months
        4. Benchmark comparison with Mediterranean peers
        
        Keep response under 500 words and focus on actionable insights.
        """
        
        try:
            response = self.groq_client.chat.completions.create(
                messages=[
                    {"role": "system", "content": "You are an expert energy analyst for North African utilities."},
                    {"role": "user", "content": prompt}
                ],
                model="llama3-70b-8192",
                temperature=0.3,
                max_tokens=1000
            )
            
            return response.choices[0].message.content
            
        except Exception as e:
            logger.error(f"LLM analysis error: {e}")
            return None
    
    def _format_comparison_table(self, comparisons, steg_metrics):
        """Format comparison data for LLM prompt"""
        
        table = "\n"
        for comp in comparisons:
            table += f"\n{comp['country_name']}:\n"
            table += f"  - Consumption: {comp['consumption_per_capita_kwh']:.0f} kWh\n"
            table += f"  - Renewable share: {comp['renewable_share_pct']:.1f}%\n"
            table += f"  - Solar adoption: {comp['solar_adoption_pct']:.1f}%\n"
            table += f"  - Grid losses: {comp['grid_losses_pct']:.1f}%\n"
            if comp['gap_to_mediterranean_avg_pct']:
                table += f"  - Gap to Mediterranean avg: {comp['gap_to_mediterranean_avg_pct']:.1f}%\n"
        
        return table
    
    def save_analysis(self, analysis, steg_metrics, med_averages):
        """Save analysis to JSON file for dashboard"""
        
        analysis_file = Path(__file__).parent.parent / "data" / "benchmark_analysis.json"
        analysis_file.parent.mkdir(parents=True, exist_ok=True)
        
        with open(analysis_file, 'w', encoding='utf-8') as f:
            json.dump({
                'generated_at': datetime.now().isoformat(),
                'steg_metrics': steg_metrics,
                'mediterranean_averages': med_averages,
                'analysis': analysis
            }, f, indent=2, ensure_ascii=False)
        
        logger.info(f"Analysis saved to {analysis_file}")
    
    def run(self):
        """Run complete benchmarking pipeline"""
        
        logger.info("=" * 60)
        logger.info("Starting Benchmarking Agent")
        logger.info("=" * 60)
        
        # Step 1: Collect external benchmarks to bronze
        logger.info("\n📥 Step 1: Collecting external benchmarks to bronze")
        logger.info("-" * 40)
        
        wb_records = self.collect_world_bank_data()
        iea_records = self.collect_iea_benchmarks()
        
        logger.info(f"  World Bank: {len(wb_records)} records")
        logger.info(f"  IEA: {len(iea_records)} records")
        
        # Step 2: Generate LLM analysis (gold data is already there)
        logger.info("\n🤖 Step 2: Generating LLM analysis")
        logger.info("-" * 40)
        
        steg_metrics = self.collect_steg_metrics_from_gold()
        analysis = self.generate_llm_analysis()
        
        # Step 3: Save analysis
        if analysis:
            # Get Mediterranean averages
            with get_cursor(dict_cursor=True) as cur:
                cur.execute("""
                    SELECT AVG(consumption_per_capita_kwh) as avg_consumption,
                           AVG(renewable_share_pct) as avg_renewable,
                           AVG(solar_adoption_pct) as avg_solar,
                           AVG(grid_losses_pct) as avg_losses
                    FROM gold.international_comparison
                    WHERE year = %s AND country_code IN ('MAR', 'DZA', 'EGY', 'ESP', 'ITA', 'TUR')
                """, (steg_metrics['year'],))
                med_averages = cur.fetchone()
            
            self.save_analysis(analysis, steg_metrics, med_averages)
        
        logger.info("\n" + "=" * 60)
        logger.info("Benchmarking Agent Complete!")
        logger.info(f"  STEG Year: {steg_metrics['year']}")
        logger.info(f"  Consumption per capita: {steg_metrics['consumption_per_capita_kwh']:.0f} kWh")
        logger.info(f"  Renewable share: {steg_metrics['renewable_share_pct']:.1f}%")
        logger.info("=" * 60)


def main():
    agent = BenchmarkingAgent()
    agent.run()


if __name__ == "__main__":
    main()