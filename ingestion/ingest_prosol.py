"""
ingestion/ingest_prosol.py
Ingest PROSOL data from multiple Excel files to Bronze layer
"""
import pandas as pd
from pathlib import Path
import sys
import re

sys.path.append(str(Path(__file__).parent.parent))
from utils.db import execute_batch
from utils.logger import get_logger
from config.settings import RAW_DATA_DIR

logger = get_logger(__name__)


def clean_column_name(col):
    """Clean column names to handle variations in spacing and special characters"""
    if not isinstance(col, str):
        return col
    
    # Normalize spaces and remove special characters
    cleaned = col.strip()
    # Replace multiple spaces with single space
    cleaned = re.sub(r'\s+', ' ', cleaned)
    # Remove any trailing/leading whitespace
    cleaned = cleaned.strip()
    
    return cleaned


def find_column(df, possible_names):
    """
    Find a column in DataFrame by trying multiple possible names.
    Returns the actual column name or None if not found.
    """
    for name in possible_names:
        # Try exact match
        if name in df.columns:
            return name
        
        # Try case-insensitive match
        for col in df.columns:
            if col.lower() == name.lower():
                return col
        
        # Try cleaned version
        for col in df.columns:
            if clean_column_name(col).lower() == name.lower():
                return col
    
    return None


def get_value_from_row(row, df, possible_names, default=0):
    """Safely get value from row using multiple possible column names"""
    col_name = find_column(df, possible_names)
    if col_name and col_name in row.index:
        val = row[col_name]
        if pd.notna(val):
            return float(val)
    return default


def ingest_prosol():
    """Read all PROSOL Excel files and insert into bronze.prosol"""
    
    prosol_dir = RAW_DATA_DIR / "prosol"
    
    if not prosol_dir.exists():
        logger.error(f"PROSOL directory not found: {prosol_dir}")
        return
    
    files = list(prosol_dir.glob("evolutionprosolresidentiel*.xls")) + \
            list(prosol_dir.glob("evolutionprosolresidentiel*.xlsx"))
    
    if not files:
        logger.error("No PROSOL files found")
        return
    
    records = []
    
    for file_path in files:
        # Extract region from filename
        region = file_path.stem.replace("evolutionprosolresidentiel", "").replace("_", "").lower()
        if not region:
            region = file_path.stem.split('_')[-1].lower()
        
        logger.info(f"Processing {region}: {file_path}")
        
        try:
            # Read the Excel file
            df = pd.read_excel(file_path)
            
            # Log column names for debugging
            logger.debug(f"Columns found in {file_path.name}: {list(df.columns)}")
            
            # Define possible column name variations
            year_names = ['Year', 'year', 'ANNEE', 'Annee']
            surface_names = ['Surface m2', 'Surface (m2)', 'surface m2', 'Surface', 'surface']
            installations_names = ['Nombre de CES installés', 'Nombre CES installés', 'CES installés', 
                                  'installations', 'Installations', 'Nombre']
            subsidy_names = ['Subvention en DT', 'Subvention (DT)', 'subvention', 'Subvention', 
                           'Subvention DT']
            self_finance_names = ['Autofinancement en DT', 'Autofinancement (DT)', 'autofinancement', 
                                 'Autofinancement', 'Auto financement']
            investment_names = ['Investissement globale en DT', 'Investissement global en DT', 
                              'Investissement (DT)', 'investissement', 'Investissement globale', 
                              'Investissement global', 'Total investment']
            avg_price_names = ['Moyen des prix en DT', 'Prix moyen en DT', 'Moyen prix (DT)', 
                              'prix moyen', 'Moyen des prix', 'Avg price']
            
            # Find actual column names
            year_col = find_column(df, year_names)
            surface_col = find_column(df, surface_names)
            installations_col = find_column(df, installations_names)
            subsidy_col = find_column(df, subsidy_names)
            self_finance_col = find_column(df, self_finance_names)
            investment_col = find_column(df, investment_names)
            avg_price_col = find_column(df, avg_price_names)
            
            if not year_col:
                logger.error(f"Year column not found in {file_path.name}. Available columns: {list(df.columns)}")
                continue
            
            for idx, row in df.iterrows():
                try:
                    # Get year
                    year_val = row[year_col] if year_col in row.index else None
                    if pd.isna(year_val):
                        continue
                    
                    year = int(float(year_val))
                    
                    # Get other values using flexible column finding
                    surface = get_value_from_row(row, df, surface_names, 0)
                    installations = get_value_from_row(row, df, installations_names, 0)
                    subsidy = get_value_from_row(row, df, subsidy_names, 0)
                    self_finance = get_value_from_row(row, df, self_finance_names, 0)
                    investment = get_value_from_row(row, df, investment_names, 0)
                    avg_price = get_value_from_row(row, df, avg_price_names, 0)
                    
                    records.append((
                        region,
                        year,
                        surface,
                        installations,
                        subsidy,
                        self_finance,
                        investment,
                        avg_price,
                        file_path.name
                    ))
                    
                except Exception as e:
                    logger.warning(f"Error processing row {idx} in {file_path.name}: {e}")
                    continue
                
        except Exception as e:
            logger.error(f"Error processing {file_path}: {e}")
            import traceback
            logger.error(traceback.format_exc())
            continue
    
    if records:
        sql = """
            INSERT INTO bronze.prosol 
            (region, year, surface_m2, installations_count, subsidy_dt, 
             self_financing_dt, total_investment_dt, avg_price_dt, source_file)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
        
        inserted = execute_batch(sql, records)
        logger.info(f"Inserted {inserted} records into bronze.prosol from {len(files)} files")
    else:
        logger.warning("No records to insert")


if __name__ == "__main__":
    ingest_prosol()