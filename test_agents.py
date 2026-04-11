from agents.agent_orchestrator import AgentOrchestrator
from agents.synthetic_data_generator import SyntheticDataGenerator
import json
from utils.logger import get_logger

logger = get_logger(__name__)

def test_synthetic_data():
    """Test synthetic data generation"""
    print("\n" + "="*50)
    print("Testing Synthetic Data Generation")
    print("="*50)
    
    generator = SyntheticDataGenerator()
    results = generator.generate_all()
    print(f"\n✅ Generated: {results}")
    return results

def test_agents():
    """Test all agents"""
    print("\n" + "="*50)
    print("Testing Agents")
    print("="*50)
    
    orchestrator = AgentOrchestrator()
    
    # Run with synthetic data first time
    print("\nGenerating synthetic data for first run...")
    results = orchestrator.run_all_agents(generate_synthetic=True)
    
    print(f"\n📊 Alert Level: {results['alert_level']}")
    print(f"\n📋 Executive Summary:\n{results['executive_summary']}")
    
    print("\n📈 Detailed Results:")
    print(f"\nCut Advisor:")
    print(f"  - Need cuts: {results['agents']['cut_advisor'].get('current_production_status', {}).get('need_cuts', False)}")
    print(f"  - Cut priority: {results['agents']['cut_advisor'].get('cut_priority_list', [])[:3]}")
    print(f"  - Reasoning: {results['agents']['cut_advisor'].get('reasoning', 'N/A')[:200]}...")
    
    print(f"\nFairness Agent:")
    print(f"  - Gini coefficient: {results['agents']['fairness'].get('fairness_score', 0)}")
    print(f"  - Needs rebalancing: {results['agents']['fairness'].get('needs_rebalancing', False)}")
    
    print(f"\nForecast Agent:")
    print(f"  - Next month: {results['agents']['forecast'].get('next_month_prediction', 'N/A')} GWh")
    print(f"  - Trend: {results['agents']['forecast'].get('trend', 'N/A')}")
    
    return results

def test_n8n_output():
    """Test n8n webhook output"""
    print("\n" + "="*50)
    print("Testing n8n Output Format")
    print("="*50)
    
    orchestrator = AgentOrchestrator()
    n8n_output = orchestrator.run_for_n8n()
    print(f"\n📤 n8n Output:\n{n8n_output}")
    
    # Parse to verify JSON
    parsed = json.loads(n8n_output)
    print(f"\n✅ Valid JSON output")
    print(f"   - Needs action: {parsed['needs_action']}")
    print(f"   - Alert level: {parsed['alert_level']}")

def run_all_tests():
    """Run all tests"""
    print("\n" + "🚀"*25)
    print("STEG Observatory Agent Tests")
    print("🚀"*25)
    
    try:
        # Test 1: Synthetic Data
        test_synthetic_data()
        
        # Test 2: Agents
        test_agents()
        
        # Test 3: n8n Integration
        #test_n8n_output() 
        #print("\n" + "✅"*25)
        #print("ALL TESTS COMPLETED SUCCESSFULLY!")
        #print("✅"*25)
        
    except Exception as e:
        logger.error(f"Test failed: {str(e)}")
        print(f"\n❌ Test failed: {str(e)}")

if __name__ == "__main__":
    run_all_tests()