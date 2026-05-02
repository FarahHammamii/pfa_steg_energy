import pandas as pd
import numpy as np
from typing import Dict, Any, List
from datetime import datetime
from agents.base_agent import BaseAgent
from agents.fairness_agent import FairnessAgent
from agents.data_loader import DataLoader
from utils.db import get_cursor
from utils.logger import get_logger
import os

# Groq setup
try:
    from groq import Groq
    GROQ_AVAILABLE = True
except ImportError:
    GROQ_AVAILABLE = False

logger = get_logger(__name__)

# Initialize Groq client if available
groq_key = os.getenv("GROQ_API_KEY")
if GROQ_AVAILABLE and groq_key:
    client = Groq(api_key=groq_key)
    GROQ_MODEL = "llama-3.3-70b-versatile"
    logger.info("Groq client ready for Cut Advisor Agent")
else:
    client = None
    GROQ_MODEL = None
    logger.warning("Groq not available. LLM explanations disabled.")


class CutAdvisorAgent(BaseAgent):
    """Recommends optimal regions to cut power during shortages"""
    
    def __init__(self):
        super().__init__("Cut Advisor Agent")
        self.data_loader = DataLoader()
        
    def analyze(self, apply_fairness: bool = True) -> Dict[str, Any]:
        """Main analysis - recommend cut order based on multiple factors"""
        
        # Load data
        solar_df = self.data_loader.load_solar_data()
        incidents_df = self.data_loader.load_incidents_data()
        regional_df = self.data_loader.load_regional_summary()
        
        logger.info(f"Loaded solar data: {len(solar_df)} rows")
        logger.info(f"Loaded incidents data: {len(incidents_df)} rows")
        
        # Get current production status
        current_production = self._get_current_production_status()
        
        # Calculate region scores (higher score = cut first)
        region_scores = self._calculate_cut_priority(solar_df, incidents_df, regional_df)
        
        # Generate recommendations
        recommendations = self._generate_recommendations(region_scores, current_production)
        
        fairness_review = None
        if apply_fairness and recommendations.get('cut_first'):
            fairness_review = self._review_with_fairness(recommendations['cut_first'])

            if fairness_review and not fairness_review.get('approved'):
                substitution = fairness_review.get('substitution')
                if substitution and recommendations['cut_first']:
                    replaced = recommendations['cut_first'][-1]
                    recommendations['cut_first'] = recommendations['cut_first'][:-1] + [substitution]
                    fairness_review['applied_substitution'] = {
                        'replaced': replaced,
                        'added': substitution,
                    }

        # Use LLM to explain reasoning if available
        if client and recommendations.get('cut_first'):
            explanation = self._get_llm_explanation(recommendations, current_production)
        else:
            explanation = self._get_fallback_explanation(recommendations, current_production)

        if fairness_review and fairness_review.get('message'):
            explanation = f"{explanation} Fairness review: {fairness_review['message']}"
        
        return {
            'status': 'success',
            'current_production_status': current_production,
            'cut_priority_list': recommendations['cut_first'],
            'protected_regions': recommendations['protect'],
            'reasoning': explanation,
            'fairness_review': fairness_review,
            'region_scores': region_scores.to_dict('records') if len(region_scores) > 0 else []
        }

    def _review_with_fairness(self, proposed_regions: List[str]) -> Dict[str, Any]:
        """Ask FairnessAgent to validate the proposed cut list."""
        fairness_agent = FairnessAgent()
        return fairness_agent.validate_cut_list(proposed_regions)
    
    def _get_current_production_status(self) -> Dict[str, Any]:
        """Check if production is below normal"""
        query = """
        WITH monthly_avg AS (
            SELECT 
                EXTRACT(MONTH FROM CURRENT_DATE) as current_month,
                AVG(total_production_gwh) as avg_production
            FROM gold.monthly_production
            WHERE month = EXTRACT(MONTH FROM CURRENT_DATE)
            AND year >= EXTRACT(YEAR FROM CURRENT_DATE) - 2
        )
        SELECT 
            current_month,
            avg_production,
            (
                SELECT total_production_gwh 
                FROM gold.monthly_production 
                WHERE year = EXTRACT(YEAR FROM CURRENT_DATE)
                AND month = EXTRACT(MONTH FROM CURRENT_DATE)
            ) as current_production
        FROM monthly_avg
        """
        
        with get_cursor(dict_cursor=True) as cur:
            cur.execute(query)
            result = cur.fetchone()
        
        if result and result.get('current_production') and result.get('avg_production'):
            current = float(result['current_production'])
            avg = float(result['avg_production'])
            deficit_pct = ((avg - current) / avg) * 100 if avg > 0 else 0
            
            return {
                'need_cuts': deficit_pct > 10,
                'deficit_percentage': round(deficit_pct, 2),
                'current_production_gwh': current,
                'normal_production_gwh': avg
            }
        else:
            # If no current data, return normal status
            return {'need_cuts': False, 'deficit_percentage': 0}
    
    def _calculate_cut_priority(self, solar_df, incidents_df, regional_df) -> pd.DataFrame:
        """Calculate priority score for each region (higher = cut first)"""
        
        if len(solar_df) == 0:
            logger.warning("No solar data available")
            return pd.DataFrame()
        
        # Get latest year data (most recent year available)
        latest_year = solar_df['year'].max()
        latest_solar = solar_df[solar_df['year'] == latest_year].copy()
        
        logger.info(f"Calculating priority for {len(latest_solar)} regions for year {latest_year}")
        
        # Calculate scores
        scores = []
        for _, row in latest_solar.iterrows():
            region = row['region']
            
            # Higher score = more likely to be cut
            score = 50  # Base score
            
            # Factor 1: Few solar installations = cut first (weight: 30%)
            installations = row['total_installations'] if pd.notna(row['total_installations']) else 0
            if installations < 500:
                score += 30
            elif installations < 1000:
                score += 15
            elif installations > 5000:
                score -= 20
            elif installations > 2000:
                score -= 10
            
            # Factor 2: Low CO2 savings = cut first (weight: 30%)
            co2 = row['co2_saved_tons'] if pd.notna(row['co2_saved_tons']) else 0
            if co2 < 50:
                score += 30
            elif co2 < 200:
                score += 15
            elif co2 > 1000:
                score -= 20
            elif co2 > 500:
                score -= 10
            
            # Factor 3: Low investment = cut first (weight: 20%)
            investment = row['total_investment_dt'] if pd.notna(row['total_investment_dt']) else 0
            if investment < 500000:
                score += 20
            elif investment < 1000000:
                score += 10
            elif investment > 2000000:
                score -= 15
            
            scores.append({
                'region': region,
                'cut_priority_score': score,
                'total_installations': int(installations),
                'co2_saved_tons': float(co2),
                'total_investment_dt': float(investment)
            })
        
        priority_df = pd.DataFrame(scores)
        priority_df = priority_df.sort_values('cut_priority_score', ascending=False)
        
        return priority_df
    
    def _generate_recommendations(self, priority_df: pd.DataFrame, production_status: Dict) -> Dict:
        """Generate cut order based on scores"""
        
        if len(priority_df) == 0:
            return {
                'cut_first': [],
                'protect': [],
                'message': "No data available for recommendations"
            }
        
        if not production_status['need_cuts']:
            return {
                'cut_first': [],
                'protect': priority_df.head(3)['region'].tolist(),
                'message': "No cuts needed at this time"
            }
        
        # Cut first: regions with highest score (highest priority to cut)
        cut_first = priority_df.head(5)['region'].tolist()
        
        # Protect: regions with lowest score (highest priority to keep)
        protect = priority_df.tail(3)['region'].tolist()
        
        return {
            'cut_first': cut_first,
            'protect': protect,
            'message': f"Production is {production_status['deficit_percentage']}% below normal"
        }
    
    def _get_llm_explanation(self, recommendations: Dict, production_status: Dict) -> str:
        """Use Groq to generate human-readable explanation"""
        
        context = f"""
Current Situation:
- Production deficit: {production_status['deficit_percentage']}% below normal
- Current production: {production_status['current_production_gwh']} GWh
- Normal production: {production_status['normal_production_gwh']} GWh

Recommended cut order (if needed):
{', '.join(recommendations['cut_first'])}

Regions to protect:
{', '.join(recommendations['protect'])}
"""
        
        prompt = f"""
You are STEG's power cut advisor for Tunisia's electricity grid.

INSTRUCTIONS:
- Answer ONLY using the provided context.
- DO NOT use your prior knowledge.
- Explain why certain regions are prioritized for cuts vs protection.
- Focus on: solar investment levels, environmental impact (CO2 savings).
- Keep explanation to 2-3 sentences.
- Be professional and clear.

==================== CONTEXT ====================
{context}

==================== QUESTION ====================
Why were these specific regions chosen for potential power cuts, and why should others be protected?

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
            return self._get_fallback_explanation(recommendations, production_status)
    
    def _get_fallback_explanation(self, recommendations: Dict, production_status: Dict) -> str:
        """Fallback explanation without LLM"""
        if not production_status['need_cuts']:
            return "Production is normal. No cuts recommended at this time."
        
        return f"Regions with low solar investment and CO₂ savings ({', '.join(recommendations['cut_first'][:3])}) are prioritized for cuts, while high-investment regions ({', '.join(recommendations['protect'])}) are protected to maximize return on investment."