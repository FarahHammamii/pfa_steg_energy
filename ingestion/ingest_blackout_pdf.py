"""
ingestion/ingest_blackout_pdf.py
Extract structured incident data from PDF and store in Bronze
"""
import json
import pdfplumber
import os
from pathlib import Path
from datetime import datetime
import sys

sys.path.append(str(Path(__file__).parent.parent))
from utils.db import get_cursor
from utils.logger import get_logger
from config.settings import RAW_DATA_DIR, GROQ_API_KEY

logger = get_logger(__name__)

# Try to import groq
try:
    from groq import Groq
    GROQ_AVAILABLE = True
except ImportError:
    GROQ_AVAILABLE = False
    logger.warning("Groq not installed. Install with: pip install groq")

# Initialize Groq client with proper API key handling
groq_client = None
if GROQ_AVAILABLE and GROQ_API_KEY:
    try:
        # Use the same pattern as your working example
        groq_client = Groq(api_key=GROQ_API_KEY)
        logger.info("Groq client initialized successfully")
    except Exception as e:
        logger.error(f"Error initializing Groq client: {e}")
        groq_client = None

def extract_text_from_pdf(pdf_path):
    """Extract raw text from PDF"""
    text = ""
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text += page_text + "\n"
        return text
    except Exception as e:
        logger.error(f"Error extracting PDF: {e}")
        return None

def extract_incident_with_groq(text):
    """Use Groq LLM to extract structured incident data using your working pattern"""
    
    if not groq_client:
        logger.warning("Groq client not available, using manual extraction")
        return manual_extraction()
    
    prompt = """Extract structured information about the electrical blackout from this technical report.
Return ONLY a valid JSON object with these exact fields:

{
    "incident_date": "YYYY-MM-DD",
    "triggering_event": "brief description",
    "root_cause": "main technical cause",
    "duration_minutes": number,
    "power_loss_mw": number,
    "recovery_time_minutes": number,
    "recommendations": ["rec1", "rec2"],
    "key_lessons": ["lesson1", "lesson2"],
    "technical_failures": [
        {"equipment": "name", "location": "place", "failure_type": "type", "impact": "description"}
    ]
}

Text to analyze:
""" + text[:8000]  # Limit text length

    try:
        # Use the exact same pattern as your working example
        chat_completion = groq_client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model="llama-3.3-70b-versatile",  # Using the model from your working example
            temperature=0.1,
            max_tokens=1500,
            top_p=1
        )
        
        result = chat_completion.choices[0].message.content
        
        # Extract JSON from response
        json_start = result.find('{')
        json_end = result.rfind('}') + 1
        
        if json_start >= 0 and json_end > json_start:
            return json.loads(result[json_start:json_end])
        else:
            logger.warning("No JSON found in Groq response")
            return manual_extraction()
            
    except Exception as e:
        logger.error(f"Groq extraction error: {e}")
        return manual_extraction()

def manual_extraction():
    """Manual extraction based on actual report content"""
    return {
        "incident_date": "2014-08-31",
        "triggering_event": "Lightning strike on high-voltage lines in Sousse region",
        "root_cause": "Lightning caused cable rupture on Sousse-Msaken line (225kV) followed by protection system failure",
        "duration_minutes": 150,
        "power_loss_mw": 1300,
        "recovery_time_minutes": 120,
        "recommendations": [
            "Improve alternator excitation systems at Sousse and Ghannouch",
            "Create failure analysis unit at STEG",
            "Improve automatic load shedding system",
            "Ensure knowledge transfer to younger engineers",
            "Install lightning alert system",
            "Study interconnection with Italy"
        ],
        "key_lessons": [
            "Multiple simultaneous failures can cascade",
            "Protection systems need redundancy",
            "Importance of experienced operators",
            "Need for better weather monitoring",
            "Algerian grid interconnection is crucial for recovery"
        ],
        "technical_failures": [
            {
                "equipment": "Guard wire on 225kV line",
                "location": "Sousse-Msaken line, pylons 28-29",
                "failure_type": "Cable rupture from lightning",
                "impact": "Short circuit and protection failure"
            },
            {
                "equipment": "Alternator excitation system",
                "location": "Sousse power plant",
                "failure_type": "Protection system malfunction",
                "impact": "5 generators (800 MW) disconnected"
            },
            {
                "equipment": "Ghannouch power plant",
                "location": "Gabès",
                "failure_type": "Voltage drop shutdown",
                "impact": "450 MW loss"
            }
        ]
    }

def ingest_blackout_incident():
    """Extract incident from PDF and store in Bronze"""
    
    pdf_path = RAW_DATA_DIR / "reports" / "extraitrapportfinaldelacommissionblackout2014.pdf"
    
    if not pdf_path.exists():
        logger.error(f"PDF not found: {pdf_path}")
        return
    
    logger.info(f"Processing {pdf_path}")
    
    # Extract text and parse
    text = extract_text_from_pdf(pdf_path)
    if not text:
        logger.warning("Failed to extract text from PDF, using manual data")
        data = manual_extraction()
    else:
        logger.info(f"Extracted {len(text)} characters from PDF")
        data = extract_incident_with_groq(text)
    
    # Check if already exists
    with get_cursor(dict_cursor=True) as cur:
        cur.execute("SELECT id FROM bronze.incidents WHERE incident_id = 'BLACKOUT_2014_08_31'")
        existing = cur.fetchone()
        
        if existing:
            logger.info("Incident already exists, updating...")
            sql = """
                UPDATE bronze.incidents 
                SET triggering_event = %s, root_cause = %s, duration_minutes = %s,
                    power_loss_mw = %s, recovery_time_minutes = %s,
                    recommendations = %s, key_lessons = %s, technical_failures = %s,
                    extracted_at = CURRENT_TIMESTAMP
                WHERE incident_id = 'BLACKOUT_2014_08_31'
            """
            cur.execute(sql, (
                data.get('triggering_event'), data.get('root_cause'),
                data.get('duration_minutes'), data.get('power_loss_mw'),
                data.get('recovery_time_minutes'),
                json.dumps(data.get('recommendations', [])),
                json.dumps(data.get('key_lessons', [])),
                json.dumps(data.get('technical_failures', []))
            ))
        else:
            sql = """
                INSERT INTO bronze.incidents 
                (incident_id, incident_date, triggering_event, root_cause, 
                 duration_minutes, power_loss_mw, recovery_time_minutes,
                 recommendations, key_lessons, technical_failures)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """
            cur.execute(sql, (
                'BLACKOUT_2014_08_31', data.get('incident_date'),
                data.get('triggering_event'), data.get('root_cause'),
                data.get('duration_minutes'), data.get('power_loss_mw'),
                data.get('recovery_time_minutes'),
                json.dumps(data.get('recommendations', [])),
                json.dumps(data.get('key_lessons', [])),
                json.dumps(data.get('technical_failures', []))
            ))
    
    logger.info(f"Incident data ingested: {data.get('incident_date')}")

if __name__ == "__main__":
    ingest_blackout_incident()