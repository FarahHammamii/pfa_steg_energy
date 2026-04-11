import json
from typing import Dict, Any, List
from datetime import datetime
from .cut_advisor_agent import CutAdvisorAgent
from .fairness_agent import FairnessAgent
from .forecast_agent import ForecastAgent
from .synthetic_data_generator import SyntheticDataGenerator
from utils.db_utils import DatabaseManager
import logging

logger = logging.getLogger(__name__)

class AgentOrchestrator:
    """Runs all agents and coordinates their actions"""
    
    def __init__(self):
        self.db = DatabaseManager()
        self.cut_advisor = CutAdvisorAgent()
        self.fairness = FairnessAgent()
        self.forecast = ForecastAgent()
        self.synthetic_gen = SyntheticDataGenerator()
        
    def run_all_agents(self, generate_synthetic: bool = False) -> Dict[str, Any]:
        """Run all agents and collect results"""
        
        results = {
            'timestamp': datetime.now().isoformat(),
            'agents': {}
        }
        
        # Generate synthetic data if needed
        if generate_synthetic:
            logger.info("Generating synthetic data...")
            results['synthetic_data'] = self.synthetic_gen.generate_all()
        
        # Run Cut Advisor
        logger.info("Running Cut Advisor Agent...")
        results['agents']['cut_advisor'] = self.cut_advisor.run()
        
        # Run Fairness Agent
        logger.info("Running Fairness Agent...")
        results['agents']['fairness'] = self.fairness.run()
        
        # Run Forecast Agent
        logger.info("Running Forecast Agent...")
        results['agents']['forecast'] = self.forecast.run()
        
        # Determine overall alert level
        results['alert_level'] = self._determine_alert_level(results)
        
        # Generate executive summary
        results['executive_summary'] = self._generate_summary(results)
        
        return results
    
    def _determine_alert_level(self, results: Dict) -> str:
        """Determine overall alert level based on all agents"""
        
        alert_level = "GREEN"  # No issues
        
        # Check cut advisor
        if results['agents']['cut_advisor'].get('current_production_status', {}).get('need_cuts'):
            alert_level = "RED"
        
        # Check fairness
        if results['agents']['fairness'].get('needs_rebalancing'):
            alert_level = "ORANGE" if alert_level == "GREEN" else "RED"
        
        # Check forecast for anomalies
        if len(results['agents']['forecast'].get('anomalies_detected', [])) > 0:
            alert_level = "YELLOW" if alert_level == "GREEN" else alert_level
        
        return alert_level
    
    def _generate_summary(self, results: Dict) -> str:
        """Generate human-readable executive summary"""
        
        cut_status = results['agents']['cut_advisor'].get('current_production_status', {})
        fairness_score = results['agents']['fairness'].get('fairness_score', 0)
        forecasts = results['agents']['forecast'].get('forecasts', [])
        
        summary = f"""
        STEG Observatory Report - {datetime.now().strftime('%Y-%m-%d %H:%M')}
        
        ALERT LEVEL: {results['alert_level']}
        
        PRODUCTION STATUS:
        {'⚠️ Production deficit detected: ' + str(cut_status.get('deficit_percentage', 0)) + '% below normal' if cut_status.get('need_cuts') else '✅ Production is normal'}
        
        FAIRNESS STATUS:
        {'⚠️ High inequality detected (Gini: ' + str(fairness_score) + ')' if fairness_score > 0.4 else '✅ Cut distribution is fair'}
        
        FORECAST:
        Next month prediction: {forecasts[0]['predicted_gwh'] if forecasts else 'N/A'} GWh
        
        RECOMMENDATIONS:
        """
        
        # Add top recommendations
        if cut_status.get('need_cuts'):
            cuts = results['agents']['cut_advisor'].get('cut_priority_list', [])
            summary += f"\n- Prepare to cut: {', '.join(cuts[:3])} if needed"
        
        fairness_recs = results['agents']['fairness'].get('recommendations', [])
        if fairness_recs:
            summary += f"\n- {fairness_recs[0]}"
        
        anomalies = results['agents']['forecast'].get('anomalies_detected', [])
        if anomalies:
            summary += f"\n- Investigate production anomaly in {anomalies[0]['date']}"
        
        return summary
    
    def run_for_n8n(self) -> str:
        """Run agents and return JSON for n8n webhook"""
        
        results = self.run_all_agents(generate_synthetic=False)
        
        # Format for n8n
        n8n_output = {
            'timestamp': results['timestamp'],
            'alert_level': results['alert_level'],
            'needs_action': results['alert_level'] != 'GREEN',
            'summary': results['executive_summary'],
            'cut_recommendations': results['agents']['cut_advisor'].get('cut_priority_list', []),
            'fairness_issues': results['agents']['fairness'].get('unfair_regions', []),
            'forecast_alerts': len(results['agents']['forecast'].get('anomalies_detected', []))
        }
        
        return json.dumps(n8n_output, indent=2)

# For n8n webhook endpoint
def webhook_handler(request):
    """Handle n8n webhook requests"""
    orchestrator = AgentOrchestrator()
    
    # Check if synthetic data should be generated
    generate_synthetic = request.get('generate_synthetic', False)
    
    results = orchestrator.run_all_agents(generate_synthetic=generate_synthetic)
    
    return {
        'status': 'success',
        'data': results
    }