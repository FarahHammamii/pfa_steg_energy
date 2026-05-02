"""
Messaging service abstraction for agent notifications.
Supports n8n webhooks and a generic alert webhook (Slack, Teams, Discord, etc.).
Extensible for multiple delivery channels.
"""

import os
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
    Supports multiple backends: n8n webhooks, generic alert webhook.

    Environment variables:
        N8N_WEBHOOK_URL      - n8n webhook endpoint
        ENABLE_N8N_MESSAGING - set to "true" to enable n8n delivery
        ALERT_WEBHOOK_URL    - generic webhook (Slack, Teams, Discord, etc.)
    """

    def __init__(self):
        self.n8n_webhook_url = os.getenv("N8N_WEBHOOK_URL")
        self.enable_n8n = os.getenv("ENABLE_N8N_MESSAGING", "false").lower() == "true"
        self.alert_webhook_url = os.getenv("ALERT_WEBHOOK_URL")

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
            region:   Region name
            issue:    Type of issue (e.g., "cut_too_often", "double_penalty")
            severity: "high", "medium", "low"
            metrics:  Dict with fairness metrics

        Returns:
            True if sent successfully via at least one channel
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

        success = self._dispatch(event)
        logger.info(f"Fairness alert sent for {region}: {issue} (success={success})")
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
            cut_list:     List of regions proposed for cut
            approved:     Whether Fairness Agent approved
            gini_before:  Gini before proposed cuts
            gini_after:   Gini after proposed cuts
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

        success = self._dispatch(event)
        logger.info(f"Cut validation result sent: approved={approved} (success={success})")
        return success

    def send_maintenance_ticket_created(
        self,
        region: str,
        ticket_id: int,
        gini_evidence: str,
    ) -> bool:
        """
        Notify that a maintenance ticket was created for a region.

        Args:
            region:        Region name
            ticket_id:     Created ticket ID
            gini_evidence: Summary of Gini evidence that triggered the ticket

        Returns:
            True if notification sent
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

        success = self._dispatch(event)
        logger.info(f"Maintenance ticket notification sent for {region} (success={success})")
        return success

    def send_rotation_schedule(
        self,
        schedule: Dict[str, List[str]],
        current_gini: float,
        projected_gini: float,
    ) -> bool:
        """
        Send rotating schedule for operator action.

        Args:
            schedule:       Dict mapping time slots to region lists
            current_gini:   Current Gini coefficient
            projected_gini: Projected Gini after applying the schedule

        Returns:
            True if notification sent
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

        success = self._dispatch(event)
        logger.info(f"Rotation schedule sent (success={success})")
        return success

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _dispatch(self, event: MessageEvent) -> bool:
        """
        Dispatch an event to all enabled channels.
        Returns True if at least one channel succeeded.
        """
        results = []

        if self.enable_n8n:
            results.append(self._send_to_n8n(event))

        if self.alert_webhook_url:
            results.append(self._send_to_alert_webhook(event))

        if not results:
            logger.warning(
                f"No messaging channels configured for event '{event.event_type}'. "
                "Set N8N_WEBHOOK_URL/ENABLE_N8N_MESSAGING or ALERT_WEBHOOK_URL."
            )
            return False

        return any(results)

    def _send_to_n8n(self, event: MessageEvent) -> bool:
        """Send event to the n8n webhook endpoint."""
        if not self.n8n_webhook_url:
            logger.warning("N8N_WEBHOOK_URL is not configured")
            return False

        return self._post_webhook(
            url=self.n8n_webhook_url,
            payload=event.to_dict(),
            channel="n8n",
        )

    def _send_to_alert_webhook(self, event: MessageEvent) -> bool:
        """
        Send event to the generic alert webhook.
        Works with Slack incoming webhooks, Microsoft Teams, Discord, or any
        service that accepts a JSON POST (set ALERT_WEBHOOK_URL accordingly).
        """
        return self._post_webhook(
            url=self.alert_webhook_url,
            payload=event.to_dict(),
            channel="alert_webhook",
        )

    def _post_webhook(self, url: str, payload: dict, channel: str) -> bool:
        """Shared POST logic for all webhook-based channels."""
        try:
            response = requests.post(url, json=payload, timeout=5)
            if response.status_code in [200, 201]:
                logger.info(f"Event '{payload.get('event_type')}' delivered via {channel}")
                return True
            else:
                logger.error(
                    f"[{channel}] webhook returned HTTP {response.status_code}: "
                    f"{response.text[:200]}"
                )
                return False
        except requests.exceptions.Timeout:
            logger.error(f"[{channel}] webhook timed out")
            return False
        except Exception as e:
            logger.error(f"[{channel}] failed to send event: {e}")
            return False


# ------------------------------------------------------------------
# Global singleton
# ------------------------------------------------------------------

_messaging_service: Optional[MessagingService] = None


def get_messaging_service() -> MessagingService:
    """Get or create the global messaging service singleton."""
    global _messaging_service
    if _messaging_service is None:
        _messaging_service = MessagingService()
    return _messaging_service
