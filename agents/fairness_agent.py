import pandas as pd
import numpy as np
from typing import Dict, Any, List
from agents.base_agent import BaseAgent
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

groq_key = os.getenv("GROQ_API_KEY")
if GROQ_AVAILABLE and groq_key:
    client = Groq(api_key=groq_key)
    GROQ_MODEL = "llama-3.3-70b-versatile"
    logger.info("Groq client ready for Fairness Agent")
else:
    client = None


class FairnessAgent(BaseAgent):
    """Ensures cuts are distributed fairly across regions"""
    
    def __init__(self):
        super().__init__("Fairness Agent")
        self.data_loader = DataLoader()
        
    def analyze(self) -> Dict[str, Any]:
        """Check fairness of power cuts"""
        
        # Load historical cuts from database
        cut_history = self._load_cut_history()
        
        logger.info(f"Loaded cut history: {len(cut_history)} records")
        
        if len(cut_history) == 0:
            return {
                'status': 'no_data',
                'message': 'No cut history available. Run synthetic data generator first.',
                'fairness_score': 0,
                'most_cut_regions': [],
                'least_cut_regions': [],
                'unfair_regions': [],
                'recommendations': ['Generate synthetic cut history to enable fairness analysis'],
                'needs_rebalancing': False
            }
        
        # Calculate fairness metrics
        fairness_metrics = self._calculate_fairness_metrics(cut_history)
        
        # Identify unfairly treated regions
        unfair_regions = self._identify_unfair_regions(cut_history)
        
        # Generate recommendations
        recommendations = self._generate_fairness_recommendations(unfair_regions, fairness_metrics)
        
        # Get LLM explanation if available
        if client and unfair_regions:
            explanation = self._get_llm_explanation(fairness_metrics, unfair_regions, recommendations)
        else:
            explanation = self._get_fallback_explanation(fairness_metrics, unfair_regions)
        
        return {
            'status': 'success',
            'fairness_score': fairness_metrics['gini_coefficient'],
            'most_cut_regions': fairness_metrics['most_cut'],
            'least_cut_regions': fairness_metrics['least_cut'],
            'unfair_regions': unfair_regions,
            'recommendations': recommendations,
            'needs_rebalancing': fairness_metrics['gini_coefficient'] > 0.4,
            'total_cuts_analyzed': fairness_metrics['total_cuts'],
            'explanation': explanation
        }
    
    def _load_cut_history(self) -> pd.DataFrame:
        """Load historical cut data from silver.cut_history"""
        query = """
        SELECT region, cut_date, duration_minutes, season
        FROM silver.cut_history
        ORDER BY cut_date DESC
        """
        
        with get_cursor(dict_cursor=True) as cur:
            cur.execute(query)
            rows = cur.fetchall()
            if rows:
                return pd.DataFrame(rows)
            return pd.DataFrame()
    
    def _calculate_fairness_metrics(self, cut_history: pd.DataFrame) -> Dict[str, Any]:
        """Calculate fairness metrics (Gini coefficient)"""
        
        if len(cut_history) == 0:
            return {
                'gini_coefficient': 0,
                'most_cut': [],
                'least_cut': [],
                'total_cuts': 0
            }
        
        # Count cuts per region
        cut_counts = cut_history.groupby('region').size().reset_index(name='cut_count')
        
        # Calculate Gini coefficient
        counts = cut_counts['cut_count'].values
        if len(counts) > 0 and len(counts) > 1:
            counts_sorted = np.sort(counts)
            n = len(counts_sorted)
            index = np.arange(1, n + 1)
            gini = (2 * np.sum(index * counts_sorted) - (n + 1) * np.sum(counts_sorted)) / (n * np.sum(counts_sorted))
        else:
            gini = 0
        
        # Find most and least cut regions
        most_cut = cut_counts.nlargest(3, 'cut_count')[['region', 'cut_count']].to_dict('records')
        least_cut = cut_counts.nsmallest(3, 'cut_count')[['region', 'cut_count']].to_dict('records')
        
        return {
            'gini_coefficient': round(float(gini), 3),
            'most_cut': most_cut,
            'least_cut': least_cut,
            'total_cuts': len(cut_history)
        }
    
    def _identify_unfair_regions(self, cut_history: pd.DataFrame) -> List[Dict]:
        """Identify regions that are unfairly treated"""
        
        if len(cut_history) == 0:
            return []
        
        cut_counts = cut_history.groupby('region').size().reset_index(name='cut_count')
        
        avg_cuts = cut_counts['cut_count'].mean()
        std_cuts = cut_counts['cut_count'].std()
        
        unfair_regions = []
        for _, row in cut_counts.iterrows():
            if row['cut_count'] > avg_cuts + std_cuts:
                unfair_regions.append({
                    'region': row['region'],
                    'issue': 'cut_too_often',
                    'cut_count': int(row['cut_count']),
                    'above_average_by': int(row['cut_count'] - avg_cuts)
                })
            elif row['cut_count'] < avg_cuts - std_cuts:
                unfair_regions.append({
                    'region': row['region'],
                    'issue': 'cut_too_rarely',
                    'cut_count': int(row['cut_count']),
                    'below_average_by': int(avg_cuts - row['cut_count'])
                })
        
        return unfair_regions
    
    def _generate_fairness_recommendations(self, unfair_regions: List[Dict], metrics: Dict) -> List[str]:
        """Generate recommendations to improve fairness"""
        
        recommendations = []
        
        for region in unfair_regions:
            if region['issue'] == 'cut_too_often':
                recommendations.append(f"Reduce cut frequency in {region['region']} - cut {region['cut_count']} times vs average")
            else:
                recommendations.append(f"Increase cut priority for {region['region']} - only cut {region['cut_count']} times")
        
        if metrics['gini_coefficient'] > 0.4:
            recommendations.append("High inequality detected. Implement rotating cut schedule across all regions.")
        
        if not recommendations:
            recommendations.append("Cut distribution is fair. No adjustments needed.")
        
        return recommendations
    
    def _get_llm_explanation(self, metrics: Dict, unfair_regions: List[Dict], recommendations: List[str]) -> str:
        """Use Groq to explain fairness analysis"""
        
        context = f"""
Fairness Metrics:
- Gini coefficient: {metrics['gini_coefficient']} (0=perfect equality, 1=perfect inequality)
- Total cuts analyzed: {metrics['total_cuts']}
- Most cut region: {metrics['most_cut'][0]['region'] if metrics['most_cut'] else 'N/A'} ({metrics['most_cut'][0]['cut_count'] if metrics['most_cut'] else 0} times)
- Least cut region: {metrics['least_cut'][0]['region'] if metrics['least_cut'] else 'N/A'} ({metrics['least_cut'][0]['cut_count'] if metrics['least_cut'] else 0} times)

Unfair Regions:
{', '.join([f"{r['region']} ({r['issue']})" for r in unfair_regions])}

Recommendations:
{chr(10).join(recommendations)}
"""
        
        prompt = f"""
You are STEG's fairness auditor for electricity grid management.

INSTRUCTIONS:
- Answer ONLY using the provided context.
- DO NOT use your prior knowledge.
- Explain what the fairness metrics mean.
- Keep explanation to 2-3 sentences.

==================== CONTEXT ====================
{context}

==================== QUESTION ====================
Is the current distribution of power cuts fair across regions? What does the Gini coefficient tell us?

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
            return self._get_fallback_explanation(metrics, unfair_regions)
    
    def _get_fallback_explanation(self, metrics: Dict, unfair_regions: List[Dict]) -> str:
        """Fallback explanation without LLM"""
        if metrics['gini_coefficient'] > 0.4:
            return f"The Gini coefficient of {metrics['gini_coefficient']} indicates significant inequality in cut distribution. Some regions are being cut much more frequently than others."
        else:
            return f"The Gini coefficient of {metrics['gini_coefficient']} indicates fair distribution of power cuts across regions."