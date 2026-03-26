"""
agents/data_quality_agent.py
Agent that validates data integrity, detects anomalies,
and generates quality reports
"""
from datetime import datetime
from pathlib import Path
import json
import sys
from decimal import Decimal

sys.path.append(str(Path(__file__).parent.parent))
from utils.db import get_cursor
from utils.logger import get_logger

logger = get_logger(__name__)


class DecimalEncoder(json.JSONEncoder):
    """Custom JSON encoder for Decimal objects"""
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        return super().default(obj)


class DataQualityAgent:
    """
    Agent that monitors data quality across all layers
    """
    
    def __init__(self):
        self.quality_report = {
            'timestamp': datetime.now().isoformat(),
            'checks': [],
            'warnings': [],
            'errors': []
        }
    
    def _convert_decimal(self, obj):
        """Recursively convert Decimal to float in dict/list"""
        if isinstance(obj, dict):
            return {k: self._convert_decimal(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [self._convert_decimal(item) for item in obj]
        elif isinstance(obj, Decimal):
            return float(obj)
        return obj
    
    def check_bronze_layer(self):
        """Validate Bronze layer data completeness"""
        
        logger.info("🔍 Checking Bronze layer...")
        
        with get_cursor(dict_cursor=True) as cur:
            # Check production data
            cur.execute("SELECT COUNT(*) as count FROM bronze.production")
            prod_count = cur.fetchone()['count']
            
            cur.execute("SELECT MIN(year) as min_year, MAX(year) as max_year FROM bronze.production")
            prod_years = cur.fetchone()
            
            self.quality_report['checks'].append({
                'layer': 'bronze',
                'table': 'production',
                'record_count': prod_count,
                'date_range': f"{prod_years['min_year']}-{prod_years['max_year']}",
                'status': 'OK' if prod_count > 0 else 'WARNING'
            })
            
            # Check PROSOL data
            cur.execute("SELECT COUNT(*) as count FROM bronze.prosol")
            prosol_count = cur.fetchone()['count']
            
            cur.execute("SELECT COUNT(DISTINCT region) as regions FROM bronze.prosol")
            regions = cur.fetchone()
            
            self.quality_report['checks'].append({
                'layer': 'bronze',
                'table': 'prosol',
                'record_count': prosol_count,
                'regions': regions['regions'] if regions['regions'] else 0,
                'status': 'OK' if prosol_count > 0 else 'WARNING'
            })
            
            # Check districts data
            cur.execute("SELECT COUNT(*) as count FROM bronze.districts")
            districts_count = cur.fetchone()['count']
            
            self.quality_report['checks'].append({
                'layer': 'bronze',
                'table': 'districts',
                'record_count': districts_count,
                'status': 'OK' if districts_count > 0 else 'WARNING'
            })
            
            # Check incidents data
            cur.execute("SELECT COUNT(*) as count FROM bronze.incidents")
            incidents_count = cur.fetchone()['count']
            
            self.quality_report['checks'].append({
                'layer': 'bronze',
                'table': 'incidents',
                'record_count': incidents_count,
                'status': 'OK' if incidents_count > 0 else 'WARNING'
            })
            
            if prod_count == 0:
                self.quality_report['errors'].append("No production data found in Bronze layer")
            if prosol_count == 0:
                self.quality_report['warnings'].append("No PROSOL data found in Bronze layer")
    
    def check_silver_layer(self):
        """Validate Silver layer transformations"""
        
        logger.info("🔍 Checking Silver layer...")
        
        with get_cursor(dict_cursor=True) as cur:
            # Check production silver
            cur.execute("""
                SELECT COUNT(*) as count,
                       COUNT(CASE WHEN steg_share_pct > 100 THEN 1 END) as invalid_shares,
                       COUNT(CASE WHEN growth_rate_pct IS NOT NULL AND growth_rate_pct > 50 THEN 1 END) as high_growth
                FROM silver.production
            """)
            prod_stats = cur.fetchone()
            
            # Convert Decimal to float
            count = int(prod_stats['count']) if prod_stats['count'] else 0
            invalid_shares = int(prod_stats['invalid_shares']) if prod_stats['invalid_shares'] else 0
            high_growth = int(prod_stats['high_growth']) if prod_stats['high_growth'] else 0
            
            self.quality_report['checks'].append({
                'layer': 'silver',
                'table': 'production',
                'record_count': count,
                'invalid_shares': invalid_shares,
                'high_growth_records': high_growth,
                'status': 'OK' if invalid_shares == 0 else 'WARNING'
            })
            
            if invalid_shares > 0:
                self.quality_report['warnings'].append(f"Found {invalid_shares} records with invalid STEG shares (>100%)")
            
            # Check PROSOL silver
            cur.execute("""
                SELECT COUNT(*) as count,
                       AVG(avg_price_per_m2) as avg_price,
                       AVG(subsidy_percentage) as avg_subsidy
                FROM silver.prosol
                WHERE surface_m2 > 0
            """)
            prosol_stats = cur.fetchone()
            
            count = int(prosol_stats['count']) if prosol_stats['count'] else 0
            avg_price = float(prosol_stats['avg_price']) if prosol_stats['avg_price'] else 0
            avg_subsidy = float(prosol_stats['avg_subsidy']) if prosol_stats['avg_subsidy'] else 0
            
            self.quality_report['checks'].append({
                'layer': 'silver',
                'table': 'prosol',
                'record_count': count,
                'avg_price_per_m2': avg_price,
                'avg_subsidy_pct': avg_subsidy,
                'status': 'OK'
            })
    
    def check_gold_layer(self):
        """Validate Gold layer aggregations"""
        
        logger.info("🔍 Checking Gold layer...")
        
        with get_cursor(dict_cursor=True) as cur:
            # Check monthly production
            cur.execute("""
                SELECT COUNT(*) as count,
                       MIN(year) as min_year,
                       MAX(year) as max_year
                FROM gold.monthly_production
            """)
            prod_gold = cur.fetchone()
            
            count = int(prod_gold['count']) if prod_gold['count'] else 0
            min_year = int(prod_gold['min_year']) if prod_gold['min_year'] else 0
            max_year = int(prod_gold['max_year']) if prod_gold['max_year'] else 0
            
            self.quality_report['checks'].append({
                'layer': 'gold',
                'table': 'monthly_production',
                'record_count': count,
                'year_range': f"{min_year}-{max_year}",
                'status': 'OK' if count > 0 else 'WARNING'
            })
            
            # Check solar impact
            cur.execute("""
                SELECT COUNT(*) as count,
                       COUNT(DISTINCT region) as regions
                FROM gold.solar_impact
            """)
            solar_gold = cur.fetchone()
            
            solar_count = int(solar_gold['count']) if solar_gold['count'] else 0
            regions = int(solar_gold['regions']) if solar_gold['regions'] else 0
            
            self.quality_report['checks'].append({
                'layer': 'gold',
                'table': 'solar_impact',
                'record_count': solar_count,
                'regions_covered': regions,
                'status': 'OK' if solar_count > 0 else 'WARNING'
            })
            
            # Check grid stability
            cur.execute("SELECT COUNT(*) as count FROM gold.grid_stability")
            stability_count = cur.fetchone()['count']
            stability_count = int(stability_count) if stability_count else 0
            
            self.quality_report['checks'].append({
                'layer': 'gold',
                'table': 'grid_stability',
                'record_count': stability_count,
                'status': 'OK' if stability_count > 0 else 'WARNING'
            })
            
            if count == 0:
                self.quality_report['errors'].append("No data in gold.monthly_production")
    
    def check_anomalies(self):
        """Detect anomalies in the data"""
        
        logger.info("🔍 Detecting anomalies...")
        
        with get_cursor(dict_cursor=True) as cur:
            # Check for sudden production drops (year-over-year)
            cur.execute("""
                SELECT year, month, total_production_gwh, growth_rate_pct
                FROM silver.production
                WHERE growth_rate_pct IS NOT NULL
                  AND growth_rate_pct < -15
                ORDER BY growth_rate_pct
                LIMIT 5
            """)
            drops = cur.fetchall()
            
            if drops:
                self.quality_report['warnings'].append(f"Found {len(drops)} significant production drops (>15%)")
                for drop in drops:
                    self.quality_report['checks'].append({
                        'type': 'anomaly',
                        'description': f"Production drop of {float(drop['growth_rate_pct']):.1f}% in {drop['year']}-{drop['month']}",
                        'value': float(drop['total_production_gwh']) if drop['total_production_gwh'] else 0
                    })
            
            # Check for missing PROSOL data years
            cur.execute("""
                SELECT region, COUNT(DISTINCT year) as year_count,
                       MIN(year) as first_year, MAX(year) as last_year
                FROM silver.prosol
                GROUP BY region
                ORDER BY year_count
            """)
            regions = cur.fetchall()
            
            for region in regions:
                year_count = int(region['year_count']) if region['year_count'] else 0
                first_year = int(region['first_year']) if region['first_year'] else 0
                last_year = int(region['last_year']) if region['last_year'] else 0
                
                if year_count < 8:  # Expecting 10 years of data (2005-2014)
                    self.quality_report['warnings'].append(
                        f"Region {region['region']} has only {year_count} years of PROSOL data "
                        f"({first_year}-{last_year})"
                    )
    
    def generate_report(self):
        """Generate and save quality report"""
        
        logger.info("📝 Generating quality report...")
        
        # Save report to file
        report_file = Path(__file__).parent.parent / "data" / "quality_report.json"
        report_file.parent.mkdir(parents=True, exist_ok=True)
        
        # Calculate overall status
        errors_count = len(self.quality_report['errors'])
        warnings_count = len(self.quality_report['warnings'])
        
        if errors_count > 0:
            overall_status = 'CRITICAL'
        elif warnings_count > 0:
            overall_status = 'WARNING'
        else:
            overall_status = 'HEALTHY'
        
        self.quality_report['overall_status'] = overall_status
        self.quality_report['summary'] = {
            'errors': errors_count,
            'warnings': warnings_count,
            'checks_passed': len([c for c in self.quality_report['checks'] if isinstance(c.get('status'), str) and c.get('status') == 'OK']),
            'total_checks': len(self.quality_report['checks'])
        }
        
        # Convert any Decimal objects to float before JSON serialization
        report_to_save = self._convert_decimal(self.quality_report)
        
        with open(report_file, 'w', encoding='utf-8') as f:
            json.dump(report_to_save, f, indent=2, ensure_ascii=False, cls=DecimalEncoder)
        
        logger.info(f"Report saved to {report_file}")
        logger.info(f"Overall Status: {overall_status}")
        logger.info(f"  Errors: {errors_count}")
        logger.info(f"  Warnings: {warnings_count}")
        
        return self.quality_report
    
    def run(self):
        """Run complete data quality check"""
        
        logger.info("=" * 60)
        logger.info("Starting Data Quality Agent")
        logger.info("=" * 60)
        
        # Run all checks
        self.check_bronze_layer()
        self.check_silver_layer()
        self.check_gold_layer()
        self.check_anomalies()
        
        # Generate report
        report = self.generate_report()
        
        logger.info("\n" + "=" * 60)
        logger.info("Data Quality Agent Complete!")
        logger.info("=" * 60)
        
        return report


def main():
    agent = DataQualityAgent()
    agent.run()


if __name__ == "__main__":
    main()