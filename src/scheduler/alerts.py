"""Email alerts via SendGrid for scheduler notifications."""

import logging
import os
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


class AlertSeverity(Enum):
    """Alert severity levels."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    SUCCESS = "success"


@dataclass
class AlertConfig:
    """Configuration for email alerts."""

    enabled: bool = True
    sendgrid_api_key: Optional[str] = None
    from_address: str = "dfs-alerts@yourdomain.com"
    to_address: str = ""

    @classmethod
    def from_env(cls) -> "AlertConfig":
        """Create config from environment variables."""
        return cls(
            enabled=os.getenv("DFS_ALERTS_ENABLED", "true").lower() == "true",
            sendgrid_api_key=os.getenv("DFS_SENDGRID_API_KEY"),
            from_address=os.getenv("DFS_ALERT_FROM", "dfs-alerts@yourdomain.com"),
            to_address=os.getenv("DFS_ALERT_TO", "jungyeom0213@gmail.com"),
        )

    @classmethod
    def from_config_file(cls) -> "AlertConfig":
        """Create config from settings.yaml file."""
        try:
            import yaml
            from pathlib import Path

            config_path = Path(__file__).parent.parent.parent / "config" / "settings.yaml"
            if config_path.exists():
                with open(config_path) as f:
                    settings = yaml.safe_load(f)

                email_config = settings.get("email", {})
                return cls(
                    enabled=email_config.get("enabled", True),
                    sendgrid_api_key=email_config.get("sendgrid_api_key"),
                    from_address=email_config.get("from_address", ""),
                    to_address=email_config.get("to_address", ""),
                )
        except Exception as e:
            logger.warning(f"Failed to load config from file: {e}")

        # Fall back to environment variables
        return cls.from_env()


class EmailAlerter:
    """Sends email alerts via SendGrid.

    Usage:
        alerter = EmailAlerter()
        alerter.send_alert(
            subject="Lineups Submitted",
            body="Successfully submitted 150 lineups to contest 12345",
            severity=AlertSeverity.SUCCESS,
        )
    """

    def __init__(self, config: Optional[AlertConfig] = None):
        """Initialize email alerter.

        Args:
            config: Alert configuration. Uses config file, then env vars if not provided.
        """
        self.config = config or AlertConfig.from_config_file()
        self._sg_client = None

    @property
    def sg_client(self):
        """Lazy-load SendGrid client."""
        if self._sg_client is None and self.config.sendgrid_api_key:
            try:
                from sendgrid import SendGridAPIClient
                self._sg_client = SendGridAPIClient(self.config.sendgrid_api_key)
            except ImportError:
                logger.warning("SendGrid package not installed. Run: pip install sendgrid")
            except Exception as e:
                logger.error(f"Failed to initialize SendGrid client: {e}")
        return self._sg_client

    def send_alert(
        self,
        subject: str,
        body: str,
        severity: AlertSeverity = AlertSeverity.INFO,
    ) -> bool:
        """Send an email alert.

        Args:
            subject: Email subject
            body: Email body (plain text)
            severity: Alert severity level

        Returns:
            True if sent successfully, False otherwise
        """
        if not self.config.enabled:
            logger.debug(f"Alerts disabled, skipping: {subject}")
            return False

        if not self.config.sendgrid_api_key:
            logger.warning(f"SendGrid API key not configured, skipping alert: {subject}")
            return False

        if not self.config.to_address:
            logger.warning(f"No recipient configured, skipping alert: {subject}")
            return False

        # Build subject with severity prefix
        severity_prefix = {
            AlertSeverity.INFO: "[INFO]",
            AlertSeverity.WARNING: "[WARNING]",
            AlertSeverity.ERROR: "[ERROR]",
            AlertSeverity.SUCCESS: "[SUCCESS]",
        }
        full_subject = f"{severity_prefix.get(severity, '')} DFS: {subject}"

        # Build HTML body
        html_body = self._build_html_body(body, severity)

        try:
            from sendgrid.helpers.mail import Mail, Email, To, Content

            message = Mail(
                from_email=Email(self.config.from_address),
                to_emails=To(self.config.to_address),
                subject=full_subject,
                plain_text_content=Content("text/plain", body),
                html_content=Content("text/html", html_body),
            )

            response = self.sg_client.send(message)

            if response.status_code in (200, 201, 202):
                logger.info(f"Alert sent: {subject}")
                return True
            else:
                logger.error(f"Failed to send alert: {response.status_code} - {response.body}")
                return False

        except ImportError:
            logger.error("SendGrid package not installed")
            return False
        except Exception as e:
            logger.error(f"Failed to send alert: {e}")
            return False

    def _build_html_body(self, body: str, severity: AlertSeverity) -> str:
        """Build HTML email body with styling.

        Args:
            body: Plain text body
            severity: Alert severity

        Returns:
            HTML formatted body
        """
        # Severity colors
        colors = {
            AlertSeverity.INFO: "#2196F3",
            AlertSeverity.WARNING: "#FF9800",
            AlertSeverity.ERROR: "#F44336",
            AlertSeverity.SUCCESS: "#4CAF50",
        }
        color = colors.get(severity, "#2196F3")

        # Convert newlines to <br>
        html_body = body.replace("\n", "<br>")

        return f"""
        <html>
        <body style="font-family: Arial, sans-serif; padding: 20px;">
            <div style="border-left: 4px solid {color}; padding-left: 15px;">
                <h2 style="color: {color}; margin-top: 0;">
                    {severity.value.upper()}
                </h2>
                <p style="font-size: 14px; color: #333;">
                    {html_body}
                </p>
                <hr style="border: none; border-top: 1px solid #eee; margin: 20px 0;">
                <p style="font-size: 12px; color: #666;">
                    Sent by Daily Fantasy Automation at {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
                </p>
            </div>
        </body>
        </html>
        """

    # Convenience methods for common alert types

    def alert_submission_success(
        self,
        contest_id: str,
        lineup_count: int,
        fill_rate: float,
    ) -> bool:
        """Send alert for successful lineup submission.

        Args:
            contest_id: Contest ID
            lineup_count: Number of lineups submitted
            fill_rate: Fill rate at submission time

        Returns:
            True if sent successfully
        """
        return self.send_alert(
            subject=f"Lineups Submitted - Contest {contest_id}",
            body=(
                f"Successfully submitted {lineup_count} lineups to contest {contest_id}.\n\n"
                f"Fill rate at submission: {fill_rate:.1%}"
            ),
            severity=AlertSeverity.SUCCESS,
        )

    def alert_submission_failure(
        self,
        contest_id: str,
        error: str,
    ) -> bool:
        """Send alert for failed lineup submission.

        Args:
            contest_id: Contest ID
            error: Error message

        Returns:
            True if sent successfully
        """
        return self.send_alert(
            subject=f"Submission Failed - Contest {contest_id}",
            body=(
                f"Failed to submit lineups to contest {contest_id}.\n\n"
                f"Error: {error}"
            ),
            severity=AlertSeverity.ERROR,
        )

    def alert_swap_performed(
        self,
        contest_id: str,
        swaps: list,
    ) -> bool:
        """Send alert for player swaps.

        Args:
            contest_id: Contest ID
            swaps: List of SwapResult objects

        Returns:
            True if sent successfully
        """
        successful = [s for s in swaps if s.success]
        failed = [s for s in swaps if not s.success]

        body_lines = [f"Processed {len(swaps)} player swaps for contest {contest_id}.\n"]

        if successful:
            body_lines.append("Successful swaps:")
            for s in successful:
                body_lines.append(f"  • {s.original_player_name} → {s.replacement_player_name} ({s.reason})")

        if failed:
            body_lines.append("\nFailed swaps:")
            for s in failed:
                body_lines.append(f"  • {s.original_player_name}: {s.error_message}")

        severity = AlertSeverity.WARNING if failed else AlertSeverity.SUCCESS

        return self.send_alert(
            subject=f"Player Swaps - Contest {contest_id}",
            body="\n".join(body_lines),
            severity=severity,
        )

    def alert_scheduler_error(
        self,
        job_name: str,
        error: str,
    ) -> bool:
        """Send alert for scheduler job error.

        Args:
            job_name: Name of the job that failed
            error: Error message

        Returns:
            True if sent successfully
        """
        return self.send_alert(
            subject=f"Scheduler Error - {job_name}",
            body=(
                f"The scheduler job '{job_name}' encountered an error.\n\n"
                f"Error: {error}\n\n"
                f"Please check the logs for more details."
            ),
            severity=AlertSeverity.ERROR,
        )

    def alert_scheduler_started(
        self,
        sports: list[str],
        dry_run: bool = False,
    ) -> bool:
        """Send alert when scheduler starts.

        Args:
            sports: List of sports being tracked
            dry_run: Whether running in dry run mode

        Returns:
            True if sent successfully
        """
        mode = "DRY RUN" if dry_run else "PRODUCTION"
        return self.send_alert(
            subject=f"Scheduler Started ({mode})",
            body=(
                f"The DFS scheduler has started.\n\n"
                f"Mode: {mode}\n"
                f"Sports: {', '.join(s.upper() for s in sports)}\n"
                f"Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            ),
            severity=AlertSeverity.INFO,
        )

    def alert_scheduler_stopped(
        self,
        reason: str = "Normal shutdown",
    ) -> bool:
        """Send alert when scheduler stops.

        Args:
            reason: Reason for stopping

        Returns:
            True if sent successfully
        """
        return self.send_alert(
            subject="Scheduler Stopped",
            body=(
                f"The DFS scheduler has stopped.\n\n"
                f"Reason: {reason}\n"
                f"Stopped at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            ),
            severity=AlertSeverity.WARNING,
        )

    def alert_contest_synced(
        self,
        sport: str,
        total_contests: int,
        eligible_contests: int,
        new_contests: int,
    ) -> bool:
        """Send alert when new contests are synced.

        Args:
            sport: Sport code
            total_contests: Total contests from API
            eligible_contests: Contests matching filters
            new_contests: New contests added to tracking

        Returns:
            True if sent successfully
        """
        if new_contests == 0:
            return False  # Don't alert if no new contests

        return self.send_alert(
            subject=f"New Contests Found - {sport.upper()}",
            body=(
                f"Found {new_contests} new eligible contest(s) for {sport.upper()}.\n\n"
                f"Total from API: {total_contests}\n"
                f"Eligible: {eligible_contests}\n"
                f"New added: {new_contests}"
            ),
            severity=AlertSeverity.INFO,
        )

    def alert_daily_summary(
        self,
        contests_entered: int,
        lineups_submitted: int,
        swaps_made: int,
        errors: int,
    ) -> bool:
        """Send daily summary alert.

        Args:
            contests_entered: Number of contests entered
            lineups_submitted: Total lineups submitted
            swaps_made: Number of player swaps
            errors: Number of errors encountered

        Returns:
            True if sent successfully
        """
        severity = AlertSeverity.WARNING if errors > 0 else AlertSeverity.INFO

        return self.send_alert(
            subject="Daily Summary",
            body=(
                f"Daily Fantasy Automation Summary\n\n"
                f"Contests entered: {contests_entered}\n"
                f"Lineups submitted: {lineups_submitted}\n"
                f"Player swaps: {swaps_made}\n"
                f"Errors: {errors}"
            ),
            severity=severity,
        )


# Module-level alerter instance
_alerter: Optional[EmailAlerter] = None


def get_alerter() -> EmailAlerter:
    """Get or create the module-level alerter instance."""
    global _alerter
    if _alerter is None:
        _alerter = EmailAlerter()
    return _alerter


def send_alert(
    subject: str,
    body: str,
    severity: AlertSeverity = AlertSeverity.INFO,
) -> bool:
    """Convenience function to send an alert.

    Args:
        subject: Email subject
        body: Email body
        severity: Alert severity

    Returns:
        True if sent successfully
    """
    return get_alerter().send_alert(subject, body, severity)
