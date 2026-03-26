"""
ingestion/ingest_production.py
Ingest monthly production data from CSV to Bronze layer
"""
import pandas as pd
import re
from pathlib import Path
import sys

sys.path.append(str(Path(__file__).parent.parent))
from utils.db import execute_batch
from utils.logger import get_logger
from config.settings import RAW_DATA_DIR

logger = get_logger(__name__)

def parse_date(date_str: str):
    """Parse date like '1 / 2005' or 'janv-22'"""
    date_str = str(date_str).strip()
    
    # Format: "1 / 2005"
    if '/' in date_str:
        parts = date_str.split('/')
        month = int(parts[0].strip())
        year = int(parts[1].strip())
        return month, year
    
    # Format: "janv-22"
    month_map = {
        'janv': 1, 'févr': 2, 'fév': 2, 'mars': 3, 'avr': 4, 'mai': 5,
        'juin': 6, 'juil': 7, 'août': 8, 'aoû': 8, 'sept': 9, 'oct': 10, 
        'nov': 11, 'déc': 12
    }
    
    match = re.match(r'([a-zéû]+)-(\d{2,4})', date_str.lower())
    if match:
        month_name = match.group(1)
        year = int(match.group(2))
        if year < 100:
            year += 2000
        month = month_map.get(month_name[:4], 1)
        return month, year
    
    return None, None

def ingest_production():
    """Read production CSV and insert into bronze.production"""
    
    file_path = RAW_DATA_DIR / "production" / "opendataproductionmensuelleelectriciteparproducteurs.csv"
    
    if not file_path.exists():
        logger.error(f"Production file not found: {file_path}")
        return
    
    logger.info(f"Reading production data from {file_path}")
    
    # Try multiple encodings
    encodings = ['latin-1', 'utf-8', 'iso-8859-1', 'cp1252', 'windows-1252']
    
    df = None
    for enc in encodings:
        try:
            df = pd.read_csv(file_path, sep=';', encoding=enc)
            logger.info(f"Successfully read with encoding: {enc}")
            break
        except Exception:
            continue
    
    if df is None:
        logger.error("Could not read CSV with any encoding")
        return
    
    logger.info(f"Found {len(df)} records")
    logger.info(f"Columns: {list(df.columns)}")
    
    # Prepare data for insertion
    records = []
    for _, row in df.iterrows():
        # Get the first column which contains date
        date_col = df.columns[0]
        date_str = str(row[date_col]).strip()
        
        # Parse date
        month, year = parse_date(date_str)
        
        if month is None:
            continue
        
        # Get values using correct column names
        steg = float(row['Production STEG. en GWh']) if pd.notna(row['Production STEG. en GWh']) else 0
        ipp = float(row['Production IPP en GWh']) if pd.notna(row['Production IPP en GWh']) else 0
        solar = float(row['IPP Solaire']) if pd.notna(row['IPP Solaire']) else 0
        autoprod = float(row['Production Auto-producteurs. en GWh']) if pd.notna(row['Production Auto-producteurs. en GWh']) else 0
        total = float(row['Production totale (GWh)']) if pd.notna(row['Production totale (GWh)']) else steg + ipp + autoprod
        
        records.append((
            month, year, date_str,
            steg, ipp, solar, autoprod, total,
            file_path.name
        ))
    
    if records:
        # Remove ON CONFLICT clause - just insert directly
        sql = """
            INSERT INTO bronze.production 
            (month, year, date_raw, steg_production_gwh, ipp_production_gwh, 
             ipp_solar_gwh, autoproducer_gwh, total_production_gwh, source_file)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
        
        inserted = execute_batch(sql, records)
        logger.info(f"Inserted {inserted} records into bronze.production")
    else:
        logger.warning("No records to insert")

if __name__ == "__main__":
    ingest_production()