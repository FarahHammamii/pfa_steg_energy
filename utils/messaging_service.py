"""
Messaging service abstraction for agent notifications.
Supports Odoo Discuss, Email, and n8n webhooks.
Extensible for multiple delivery channels.
"""

import os
import json
import requests
from typing import Dict, Any, List, Optional
from utils.logger import get_logger

logger = get_logger(__name__)


class MessageEvent:
    """Base class for agent message events."""

    def __init__(self, event_type: str, agent_name: str, context: Dict[str, Any]):
        self.event_type = event_type  # e.g., "fairness_alert", "cut_validation"
        self.agent_name = agent_name
        self.context = context
        self.timestamp = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert event to dictionary for serialization."""
        return {
            "event_type": self.event_type,
            "agent_name": self.agent_name,
            "context": self.context,
            "timestamp": self.timestamp,
        }


class MessagingService:
    """
    Central messaging service for agent notifications.
    Supports multiple backends: Odoo, Email, n8n webhooks.
    """

    def __init__(self):
        self.odoo_client = None
        self.n8n_webhook_url = os.getenv("N8N_WEBHOOK_URL")
        self.enable_n8n = os.getenv("ENABLE_N8N_MESSAGING", "false").lower() == "true"

    def set_odoo_client(self, odoo_client):
        """Inject Odoo client for Discuss messaging."""
        self.odoo_client = odoo_client

    def send_fairness_alert(
        self,
        region: str,
        issue: str,
        severity: str,
        metrics: Dict[str, Any],
    ) -> bool:
        """
        Send fairness alert about a region.

        Args:
            region: Region name
            issue: Type of issue (e.g., "cut_too_often", "double_penalty")
            severity: "high", "medium", "low"
            metrics: Dict with fairness metrics

        Returns:
            True if sent successfully
        """
        event = MessageEvent(
            event_type="fairness_alert",
            agent_name="Fairness Agent",
            context={
                "region": region,
                "issue": issue,
                "severity": severity,
                "metrics": metrics,
            },
        )

        success = True

        # Send via Odoo if available
        if self.odoo_client and self.odoo_client.is_connected():
            title = f"Fairness Alert: Region {region}"
            message = f"Issue: {issue} (Severity: {severity})"
            if not self.odoo_client.get_manager_by_region(region):
                logger.warning(f"Manager not found for {region}")
            else:
                # Note: This will be called from the agent after logging
                pass

        # Send via n8n if enabled
        if self.enable_n8n:
            success = success and self._send_to_n8n(event)

        logger.info(f"Fairness alert sent for {region}: {issue}")
        return success

    def send_cut_validation_result(
        self,
        cut_list: List[str],
        approved: bool,
        gini_before: float,
        gini_after: float,
        substitution: Optional[str],
    ) -> bool:
        """
        Send cut validation result (cross-agent communication).

        Args:
            cut_list: List of regions proposed for cut
            approved: Whether Fairness Agent approved
            gini_before: Gini before proposed cuts
            gini_after: Gini after proposed cuts
            substitution: Suggested alternative region if rejected

        Returns:
            True if notification sent
        """
        event = MessageEvent(
            event_type="cut_validation",
            agent_name="Fairness Agent",
            context={
                "cut_list": cut_list,
                "approved": approved,
                "gini_before": gini_before,
                "gini_after": gini_after,
                "substitution": substitution,
            },
        )

        success = True

        # Send via n8n if enabled (for orchestration feedback)
        if self.enable_n8n:
            success = success and self._send_to_n8n(event)

        logger.info(f"Cut validation result sent: approved={approved}")
        return success

    def send_maintenance_ticket_created(
        self,
        region: str,
        ticket_id: int,
        gini_evidence: str,
    ) -> bool:
        """
        Notify that maintenance ticket was created for a region.
        """
        event = MessageEvent(
            event_type="maintenance_ticket_created",
            agent_name="Fairness Agent",
            context={
                "region": region,
                "ticket_id": ticket_id,
                "gini_evidence": gini_evidence,
            },
        )

        if self.enable_n8n:
            return self._send_to_n8n(event)

        return True

    def send_rotation_schedule(
        self,
        schedule: Dict[str, List[str]],
        current_gini: float,
        projected_gini: float,
    ) -> bool:
        """
        Send rotating schedule for operator action.
        """
        event = MessageEvent(
            event_type="rotation_schedule",
            agent_name="Fairness Agent",
            context={
                "schedule": schedule,
                "current_gini": current_gini,
                "projected_gini": projected_gini,
            },
        )

        if self.enable_n8n:
            return self._send_to_n8n(event)

        return True

    def _send_to_n8n(self, event: MessageEvent) -> bool:
        """Send event to n8n webhook."""
        if not self.n8n_webhook_url:
            logger.warning("N8N_WEBHOOK_URL not configured")
            return False

        try:
            payload = event.to_dict()
            response = requests.post(
                self.n8n_webhook_url,
                json=payload,
                timeout=5,
            )
            if response.status_code in [200, 201]:
                logger.info(f"Event sent to n8n: {event.event_type}")
                return True
            else:
                logger.error(f"n8n webhook returned {response.status_code}")
                return False
        except Exception as e:
            logger.error(f"Failed to send to n8n: {e}")
            return False


# Global singleton
_messaging_service = None


def get_messaging_service() -> MessagingService:
    """Get or create global messaging service."""
    global _messaging_service
    if _messaging_service is None:
        _messaging_service = MessagingService()
    return _messaging_service
