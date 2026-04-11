from abc import ABC, abstractmethod
from datetime import datetime
from typing import Dict, Any
from utils.logger import get_logger

logger = get_logger(__name__)

class BaseAgent(ABC):
    """Base class for all STEG observatory agents"""
    
    def __init__(self, name: str):
        self.name = name
        self.last_run = None
        self.run_count = 0
        
    @abstractmethod
    def analyze(self) -> Dict[str, Any]:
        """Main analysis method to be implemented by each agent"""
        pass
    
    def run(self) -> Dict[str, Any]:
        """Execute the agent and log results"""
        logger.info(f"Running agent: {self.name}")
        self.last_run = datetime.now()
        self.run_count += 1
        
        try:
            result = self.analyze()
            result['agent_name'] = self.name
            result['timestamp'] = self.last_run.isoformat()
            result['run_count'] = self.run_count
            logger.info(f"Agent {self.name} completed successfully")
            return result
        except Exception as e:
            logger.error(f"Agent {self.name} failed: {str(e)}")
            return {
                'agent_name': self.name,
                'status': 'error',
                'error': str(e),
                'timestamp': self.last_run.isoformat()
            }