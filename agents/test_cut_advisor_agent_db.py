"""
Updated test script for Cut Advisor Agent with actual schema
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.cut_advisor_agent import CutAdvisorAgent
from utils.db import get_cursor
import pandas as pd
from datetime import datetime


def check_schema_tables():
    """Check tables according to your actual schema"""
    print("\n" + "="*60)
    print("CHECKING DATABASE TABLES (GOLD LAYER)")
    print("="*60)
    
    tables = [
        'gold.monthly_production',
        'gold.solar_impact',
        'gold.grid_stability',
        'gold.regional_consumption'
    ]
    
    with get_cursor(dict_cursor=True) as cur:
        for table in tables:
            try:
                cur.execute(f"SELECT COUNT(*) as count FROM {table}")
                result = cur.fetchone()
                count = result['count'] if result else 0
                status = "✅" if count > 0 else "⚠️"
                print(f"{status} {table}: {count} rows")
            except Exception as e:
                print(f"❌ {table}: Error - {str(e)[:80]}")


def show_solar_impact():
    """Show solar impact data summary"""
    print("\n" + "="*60)
    print("SOLAR IMPACT SUMMARY")
    print("="*60)
    
    query = """
    SELECT 
        region,
        year,
        total_installations,
        total_investment_dt,
        co2_saved_tons,
        total_surface_m2
    FROM gold.solar_impact
    WHERE year = (SELECT MAX(year) FROM gold.solar_impact)
    ORDER BY total_installations DESC
    """
    
    with get_cursor(dict_cursor=True) as cur:
        cur.execute(query)
        data = cur.fetchall()
        
        if data:
            df = pd.DataFrame(data)
            print(f"\nYear: {df['year'].iloc[0]}")
            print("-" * 80)
            print(f"{'Region':<20} {'Installations':>12} {'Investment (DT)':>15} {'CO2 (tons)':>12} {'Surface (m²)':>12}")
            print("-" * 80)
            
            for _, row in df.iterrows():
                print(f"{row['region']:<20} {row['total_installations']:>12,} "
                      f"{row['total_investment_dt']:>15,.0f} {row['co2_saved_tons']:>12,.0f} "
                      f"{row['total_surface_m2']:>12,}")


def show_current_month_production():
    """Show current month production vs historical"""
    print("\n" + "="*60)
    print("CURRENT PRODUCTION STATUS")
    print("="*60)
    
    query = """
    WITH current_month AS (
        SELECT 
            year,
            month,
            month_name,
            total_production_gwh,
            avg_renewable_share_pct
        FROM gold.monthly_production
        WHERE year = EXTRACT(YEAR FROM CURRENT_DATE)
          AND month = EXTRACT(MONTH FROM CURRENT_DATE)
    ),
    historical_avg AS (
        SELECT 
            AVG(total_production_gwh) as avg_production,
            AVG(avg_renewable_share_pct) as avg_renewable
        FROM gold.monthly_production
        WHERE month = EXTRACT(MONTH FROM CURRENT_DATE)
          AND year >= EXTRACT(YEAR FROM CURRENT_DATE) - 2
          AND NOT (year = EXTRACT(YEAR FROM CURRENT_DATE) 
                   AND month = EXTRACT(MONTH FROM CURRENT_DATE))
    )
    SELECT 
        cm.year,
        cm.month_name,
        cm.total_production_gwh as current_production,
        ha.avg_production as historical_avg,
        cm.total_production_gwh - ha.avg_production as difference_gwh,
        ((cm.total_production_gwh - ha.avg_production) / ha.avg_production * 100) as deficit_pct,
        cm.avg_renewable_share_pct as current_renewable_pct,
        ha.avg_renewable as historical_renewable_pct
    FROM current_month cm
    CROSS JOIN historical_avg ha
    """
    
    with get_cursor(dict_cursor=True) as cur:
        cur.execute(query)
        result = cur.fetchone()
        
        if result:
            print(f"\n📊 {result['month_name']} {result['year']}")
            print(f"   Current Production: {result['current_production']:.2f} GWh")
            print(f"   Historical Average: {result['historical_avg']:.2f} GWh")
            print(f"   Difference: {result['difference_gwh']:.2f} GWh ({result['deficit_pct']:.1f}%)")
            print(f"   Renewable Share: {result['current_renewable_pct']:.1f}% (vs {result['historical_renewable_pct']:.1f}% avg)")


def run_agent_with_schema():
    """Run the agent with your actual schema"""
    print("\n" + "="*60)
    print("RUNNING CUT ADVISOR AGENT")
    print("="*60)
    
    agent = CutAdvisorAgent()
    result = agent.analyze()

    run_id = result.get("run_id")
    if run_id:
        print(f"\n✅ Analysis Complete - Run ID: {run_id}")
    else:
        print("\n✅ Analysis Complete")

    # Show recommendations
    cut_first = result.get("cut_priority_list", [])
    protected = result.get("protected_regions", [])
    print("\n📋 Recommendations:")
    print(f"   🔴 CUT FIRST: {cut_first}")
    print(f"   � PROTECT: {protected}")

    # Show reasoning
    reasoning = result.get("reasoning", "")
    if reasoning:
        print(f"\n💡 Reasoning:\n   {reasoning}")
    else:
        print("\n💡 Reasoning: (none provided)")
    
    return result


if __name__ == "__main__":
    print("CUT ADVISOR AGENT - SCHEMA VALIDATION TEST")
    print(f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    # Check tables
    check_schema_tables()
    
    # Show data summaries
    show_current_month_production()
    show_solar_impact()
    
    # Run agent
    result = run_agent_with_schema()