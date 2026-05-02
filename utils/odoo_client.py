"""
Messaging service abstraction for agent notifications.
Supports n8n webhooks and a generic HTTP/REST backend.
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


class N8nBackend:
    """Delivers events to an n8n webhook endpoint."""

    def __init__(self, webhook_url: str):
        self.webhook_url = webhook_url

    def send(self, event: MessageEvent) -> bool:
        try:
            response = requests.post(
                self.webhook_url,
                json=event.to_dict(),
                timeout=5,
            )
            if response.status_code in [200, 201]:
                logger.info(f"[n8n] Event sent: {event.event_type}")
                return True
            logger.error(f"[n8n] Webhook returned {response.status_code}")
            return False
        except Exception as e:
            logger.error(f"[n8n] Failed to send event: {e}")
            return False


class HttpBackend:
    """
    Delivers events to a generic REST endpoint (your own Python service).
    Reads base URL and optional Bearer token from environment variables:
      HTTP_BACKEND_URL   — e.g. https://your-service.com/api/events
      HTTP_BACKEND_TOKEN — optional Bearer token
    """

    def __init__(self, base_url: str, token: Optional[str] = None):
        self.base_url = base_url.rstrip("/")
        self.headers = {"Content-Type": "application/json"}
        if token:
            self.headers["Authorization"] = f"Bearer {token}"

    def send(self, event: MessageEvent) -> bool:
        try:
            response = requests.post(
                f"{self.base_url}/events",
                json=event.to_dict(),
                headers=self.headers,
                timeout=5,
            )
            if response.status_code in [200, 201, 202]:
                logger.info(f"[http] Event sent: {event.event_type}")
                return True
            logger.error(f"[http] Backend returned {response.status_code}: {response.text}")
            return False
        except Exception as e:
            logger.error(f"[http] Failed to send event: {e}")
            return False


def _build_backends() -> list:
    """
    Instantiate active backends from environment variables.
    Add or remove backends here without touching business logic.
    """
    backends = []

    n8n_url = os.getenv("N8N_WEBHOOK_URL")
    if os.getenv("ENABLE_N8N_MESSAGING", "false").lower() == "true" and n8n_url:
        backends.append(N8nBackend(n8n_url))
        logger.info("n8n backend enabled.")

    http_url = os.getenv("HTTP_BACKEND_URL")
    if http_url:
        token = os.getenv("HTTP_BACKEND_TOKEN")
        backends.append(HttpBackend(http_url, token))
        logger.info("HTTP backend enabled.")

    if not backends:
        logger.warning("No messaging backends configured. Events will be logged only.")

    return backends


class MessagingService:
    """
    Central messaging service for agent notifications.
    Dispatches events to all configured backends (n8n, HTTP REST, …).
    """

    def __init__(self):
        self.backends = _build_backends()

    def _dispatch(self, event: MessageEvent) -> bool:
        """Send event to every active backend; returns True if all succeed."""
        if not self.backends:
            logger.info(f"[no-op] Event not sent (no backends): {event.to_dict()}")
            return True
        return all(backend.send(event) for backend in self.backends)

    # ------------------------------------------------------------------ #
    #  Public API — one method per domain event                           #
    # ------------------------------------------------------------------ #

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
            issue:    Type of issue (e.g. "cut_too_often", "double_penalty")
            severity: "high" | "medium" | "low"
            metrics:  Dict with fairness metrics
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
        logger.info(f"Sending fairness alert for {region}: {issue} [{severity}]")
        return self._dispatch(event)

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
            cut_list:     Regions proposed for cut
            approved:     Whether Fairness Agent approved
            gini_before:  Gini before proposed cuts
            gini_after:   Gini after proposed cuts
            substitution: Suggested alternative region if rejected
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
        logger.info(f"Sending cut validation result: approved={approved}")
        return self._dispatch(event)

    def send_maintenance_ticket_created(
        self,
        region: str,
        ticket_id: int,
        gini_evidence: str,
    ) -> bool:
        """Notify that a maintenance ticket was created for a region."""
        event = MessageEvent(
            event_type="maintenance_ticket_created",
            agent_name="Fairness Agent",
            context={
                "region": region,
                "ticket_id": ticket_id,
                "gini_evidence": gini_evidence,
            },
        )
        return self._dispatch(event)

    def send_rotation_schedule(
        self,
        schedule: Dict[str, List[str]],
        current_gini: float,
        projected_gini: float,
    ) -> bool:
        """Send rotating schedule for operator action."""
        event = MessageEvent(
            event_type="rotation_schedule",
            agent_name="Fairness Agent",
            context={
                "schedule": schedule,
                "current_gini": current_gini,
                "projected_gini": projected_gini,
            },
        )
        return self._dispatch(event)


# ------------------------------------------------------------------ #
#  Global singleton                                                    #
# ------------------------------------------------------------------ #

_messaging_service: Optional[MessagingService] = None


def get_messaging_service() -> MessagingService:
    """Get or create the global messaging service."""
    global _messaging_service
    if _messaging_service is None:
        _messaging_service = MessagingService()
    return _messaging_service