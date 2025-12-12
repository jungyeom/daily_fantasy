"""Email notification system for alerts and status updates."""
import logging
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

from .config import get_config, EmailConfig
from .exceptions import EmailSendError

logger = logging.getLogger(__name__)


class EmailNotifier:
    """Send email notifications for various events."""

    def __init__(self, config: Optional[EmailConfig] = None):
        """Initialize email notifier.

        Args:
            config: Email configuration. Uses global config if not provided.
        """
        if config is None:
            config = get_config().email
        self.config = config

    def _send_email(self, subject: str, body_html: str, body_text: str) -> bool:
        """Send an email.

        Args:
            subject: Email subject line
            body_html: HTML body content
            body_text: Plain text body content

        Returns:
            True if sent successfully

        Raises:
            EmailSendError: If sending fails
        """
        if not self.config.enabled:
            logger.debug("Email notifications disabled, skipping")
            return False

        if not self.config.to_addresses:
            logger.warning("No recipient addresses configured")
            return False

        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = f"[Daily Fantasy] {subject}"
            msg["From"] = self.config.from_address
            msg["To"] = ", ".join(self.config.to_addresses)

            # Attach both plain text and HTML versions
            msg.attach(MIMEText(body_text, "plain"))
            msg.attach(MIMEText(body_html, "html"))

            with smtplib.SMTP(self.config.smtp_host, self.config.smtp_port) as server:
                server.starttls()
                server.login(self.config.username, self.config.password)
                server.sendmail(
                    self.config.from_address,
                    self.config.to_addresses,
                    msg.as_string(),
                )

            logger.info(f"Email sent: {subject}")
            return True

        except smtplib.SMTPException as e:
            logger.error(f"Failed to send email: {e}")
            raise EmailSendError(f"SMTP error: {e}") from e
        except Exception as e:
            logger.error(f"Failed to send email: {e}")
            raise EmailSendError(str(e)) from e

    def notify_lineups_submitted(
        self,
        sport: str,
        contest_name: str,
        num_lineups: int,
        total_projected: float,
    ) -> bool:
        """Send notification when lineups are submitted.

        Args:
            sport: Sport name (NFL, NBA, etc.)
            contest_name: Name of the contest
            num_lineups: Number of lineups submitted
            total_projected: Average projected points

        Returns:
            True if sent successfully
        """
        if not self.config.notify_on_submission:
            return False

        subject = f"✅ {num_lineups} {sport} Lineups Submitted"

        body_text = f"""
Lineups Submitted Successfully

Sport: {sport}
Contest: {contest_name}
Lineups: {num_lineups}
Avg Projected Points: {total_projected:.1f}
Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""

        body_html = f"""
<html>
<body style="font-family: Arial, sans-serif;">
    <h2 style="color: #28a745;">✅ Lineups Submitted Successfully</h2>
    <table style="border-collapse: collapse; margin: 20px 0;">
        <tr>
            <td style="padding: 8px; border: 1px solid #ddd;"><strong>Sport</strong></td>
            <td style="padding: 8px; border: 1px solid #ddd;">{sport}</td>
        </tr>
        <tr>
            <td style="padding: 8px; border: 1px solid #ddd;"><strong>Contest</strong></td>
            <td style="padding: 8px; border: 1px solid #ddd;">{contest_name}</td>
        </tr>
        <tr>
            <td style="padding: 8px; border: 1px solid #ddd;"><strong>Lineups</strong></td>
            <td style="padding: 8px; border: 1px solid #ddd;">{num_lineups}</td>
        </tr>
        <tr>
            <td style="padding: 8px; border: 1px solid #ddd;"><strong>Avg Projected</strong></td>
            <td style="padding: 8px; border: 1px solid #ddd;">{total_projected:.1f} pts</td>
        </tr>
        <tr>
            <td style="padding: 8px; border: 1px solid #ddd;"><strong>Time</strong></td>
            <td style="padding: 8px; border: 1px solid #ddd;">{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</td>
        </tr>
    </table>
</body>
</html>
"""

        return self._send_email(subject, body_html, body_text)

    def notify_late_swap(
        self,
        sport: str,
        contest_name: str,
        lineup_id: int,
        old_player: str,
        new_player: str,
        reason: str,
    ) -> bool:
        """Send notification when a late swap is executed.

        Args:
            sport: Sport name
            contest_name: Contest name
            lineup_id: ID of affected lineup
            old_player: Player being swapped out
            new_player: Player being swapped in
            reason: Reason for swap (projection_drop, injury, etc.)

        Returns:
            True if sent successfully
        """
        if not self.config.notify_on_late_swap:
            return False

        subject = f"🔄 Late Swap: {old_player} → {new_player}"

        body_text = f"""
Late Swap Executed

Sport: {sport}
Contest: {contest_name}
Lineup ID: {lineup_id}
Swapped Out: {old_player}
Swapped In: {new_player}
Reason: {reason}
Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""

        body_html = f"""
<html>
<body style="font-family: Arial, sans-serif;">
    <h2 style="color: #17a2b8;">🔄 Late Swap Executed</h2>
    <table style="border-collapse: collapse; margin: 20px 0;">
        <tr>
            <td style="padding: 8px; border: 1px solid #ddd;"><strong>Sport</strong></td>
            <td style="padding: 8px; border: 1px solid #ddd;">{sport}</td>
        </tr>
        <tr>
            <td style="padding: 8px; border: 1px solid #ddd;"><strong>Contest</strong></td>
            <td style="padding: 8px; border: 1px solid #ddd;">{contest_name}</td>
        </tr>
        <tr>
            <td style="padding: 8px; border: 1px solid #ddd;"><strong>Lineup ID</strong></td>
            <td style="padding: 8px; border: 1px solid #ddd;">{lineup_id}</td>
        </tr>
        <tr>
            <td style="padding: 8px; border: 1px solid #ddd;"><strong>Swapped Out</strong></td>
            <td style="padding: 8px; border: 1px solid #ddd; color: #dc3545;">{old_player}</td>
        </tr>
        <tr>
            <td style="padding: 8px; border: 1px solid #ddd;"><strong>Swapped In</strong></td>
            <td style="padding: 8px; border: 1px solid #ddd; color: #28a745;">{new_player}</td>
        </tr>
        <tr>
            <td style="padding: 8px; border: 1px solid #ddd;"><strong>Reason</strong></td>
            <td style="padding: 8px; border: 1px solid #ddd;">{reason}</td>
        </tr>
    </table>
</body>
</html>
"""

        return self._send_email(subject, body_html, body_text)

    def notify_contest_results(
        self,
        sport: str,
        contest_name: str,
        entry_fee: float,
        num_entries: int,
        total_winnings: float,
        best_finish: int,
        total_entries: int,
    ) -> bool:
        """Send notification with contest results.

        Args:
            sport: Sport name
            contest_name: Contest name
            entry_fee: Entry fee per lineup
            num_entries: Number of lineups entered
            total_winnings: Total amount won
            best_finish: Best finishing position
            total_entries: Total entries in contest

        Returns:
            True if sent successfully
        """
        if not self.config.notify_on_results:
            return False

        total_fees = entry_fee * num_entries
        profit = total_winnings - total_fees
        roi = (profit / total_fees * 100) if total_fees > 0 else 0

        profit_color = "#28a745" if profit >= 0 else "#dc3545"
        profit_symbol = "+" if profit >= 0 else ""

        subject = f"📊 Results: {sport} - ${profit_symbol}{profit:.2f}"

        body_text = f"""
Contest Results

Sport: {sport}
Contest: {contest_name}
Entries: {num_entries}
Total Fees: ${total_fees:.2f}
Total Winnings: ${total_winnings:.2f}
Profit: ${profit_symbol}{profit:.2f}
ROI: {roi:.1f}%
Best Finish: {best_finish} / {total_entries}
"""

        body_html = f"""
<html>
<body style="font-family: Arial, sans-serif;">
    <h2>📊 Contest Results</h2>
    <table style="border-collapse: collapse; margin: 20px 0;">
        <tr>
            <td style="padding: 8px; border: 1px solid #ddd;"><strong>Sport</strong></td>
            <td style="padding: 8px; border: 1px solid #ddd;">{sport}</td>
        </tr>
        <tr>
            <td style="padding: 8px; border: 1px solid #ddd;"><strong>Contest</strong></td>
            <td style="padding: 8px; border: 1px solid #ddd;">{contest_name}</td>
        </tr>
        <tr>
            <td style="padding: 8px; border: 1px solid #ddd;"><strong>Entries</strong></td>
            <td style="padding: 8px; border: 1px solid #ddd;">{num_entries}</td>
        </tr>
        <tr>
            <td style="padding: 8px; border: 1px solid #ddd;"><strong>Total Fees</strong></td>
            <td style="padding: 8px; border: 1px solid #ddd;">${total_fees:.2f}</td>
        </tr>
        <tr>
            <td style="padding: 8px; border: 1px solid #ddd;"><strong>Winnings</strong></td>
            <td style="padding: 8px; border: 1px solid #ddd;">${total_winnings:.2f}</td>
        </tr>
        <tr>
            <td style="padding: 8px; border: 1px solid #ddd;"><strong>Profit</strong></td>
            <td style="padding: 8px; border: 1px solid #ddd; color: {profit_color}; font-weight: bold;">
                {profit_symbol}${abs(profit):.2f}
            </td>
        </tr>
        <tr>
            <td style="padding: 8px; border: 1px solid #ddd;"><strong>ROI</strong></td>
            <td style="padding: 8px; border: 1px solid #ddd; color: {profit_color};">{roi:.1f}%</td>
        </tr>
        <tr>
            <td style="padding: 8px; border: 1px solid #ddd;"><strong>Best Finish</strong></td>
            <td style="padding: 8px; border: 1px solid #ddd;">{best_finish:,} / {total_entries:,}</td>
        </tr>
    </table>
</body>
</html>
"""

        return self._send_email(subject, body_html, body_text)

    def notify_error(
        self,
        error_type: str,
        error_message: str,
        context: Optional[dict] = None,
    ) -> bool:
        """Send notification when an error occurs.

        Args:
            error_type: Type of error (e.g., 'YahooAuthError', 'OptimizerError')
            error_message: Error message
            context: Additional context dict (sport, contest, etc.)

        Returns:
            True if sent successfully
        """
        if not self.config.notify_on_error:
            return False

        context = context or {}
        context_str = "\n".join(f"{k}: {v}" for k, v in context.items())
        context_html = "".join(
            f'<tr><td style="padding: 8px; border: 1px solid #ddd;"><strong>{k}</strong></td>'
            f'<td style="padding: 8px; border: 1px solid #ddd;">{v}</td></tr>'
            for k, v in context.items()
        )

        subject = f"⚠️ Error: {error_type}"

        body_text = f"""
Error Occurred

Type: {error_type}
Message: {error_message}
Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

Context:
{context_str}
"""

        body_html = f"""
<html>
<body style="font-family: Arial, sans-serif;">
    <h2 style="color: #dc3545;">⚠️ Error Occurred</h2>
    <table style="border-collapse: collapse; margin: 20px 0;">
        <tr>
            <td style="padding: 8px; border: 1px solid #ddd;"><strong>Type</strong></td>
            <td style="padding: 8px; border: 1px solid #ddd;">{error_type}</td>
        </tr>
        <tr>
            <td style="padding: 8px; border: 1px solid #ddd;"><strong>Message</strong></td>
            <td style="padding: 8px; border: 1px solid #ddd; color: #dc3545;">{error_message}</td>
        </tr>
        <tr>
            <td style="padding: 8px; border: 1px solid #ddd;"><strong>Time</strong></td>
            <td style="padding: 8px; border: 1px solid #ddd;">{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</td>
        </tr>
        {context_html}
    </table>
</body>
</html>
"""

        return self._send_email(subject, body_html, body_text)


# Singleton instance
_notifier_instance: Optional[EmailNotifier] = None


def get_notifier() -> EmailNotifier:
    """Get the email notifier singleton."""
    global _notifier_instance
    if _notifier_instance is None:
        _notifier_instance = EmailNotifier()
    return _notifier_instance
