"""Email notification module for sending reports and alerts via SMTP."""

import logging
import smtplib
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)


class EmailNotifier:
    """Sends email notifications with HTML content and file attachments."""

    def __init__(self, smtp_config: dict):
        self.host = smtp_config["host"]
        self.port = smtp_config["port"]
        self.user = smtp_config["user"]
        self.password = smtp_config["password"]
        self.use_tls = smtp_config.get("use_tls", True)
        self.from_addr = smtp_config.get("from_addr", self.user)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    def send_report(
        self,
        subject: str,
        html_body: str,
        recipients: list[str],
        attachments: list[Path] | None = None,
    ):
        """Send an HTML email with optional file attachments.

        Args:
            subject: Email subject line.
            html_body: HTML content for the email body.
            recipients: List of recipient email addresses.
            attachments: Optional list of file paths to attach.
        """
        if not recipients:
            logger.warning("No email recipients configured, skipping send")
            return

        msg = MIMEMultipart("mixed")
        msg["From"] = self.from_addr
        msg["To"] = ", ".join(recipients)
        msg["Subject"] = subject

        # HTML body
        msg.attach(MIMEText(html_body, "html"))

        # Attachments
        if attachments:
            for file_path in attachments:
                if file_path.exists():
                    with open(file_path, "rb") as f:
                        attachment = MIMEApplication(f.read(), Name=file_path.name)
                    attachment["Content-Disposition"] = f'attachment; filename="{file_path.name}"'
                    msg.attach(attachment)
                    logger.debug("Attached file: %s", file_path.name)

        # Send
        logger.info("Sending email to %s: %s", recipients, subject)
        with smtplib.SMTP(self.host, self.port) as server:
            if self.use_tls:
                server.starttls()
            if self.user and self.password:
                server.login(self.user, self.password)
            server.send_message(msg)
        logger.info("Email sent successfully")

    def send_immediate_alert(
        self,
        subject: str,
        message: str,
        recipients: list[str],
    ):
        """Send a simple text alert email (for immediate quality failure notifications).

        Args:
            subject: Email subject line.
            message: Plain text message body.
            recipients: List of recipient email addresses.
        """
        html = f"""
        <html>
        <body style="font-family: Arial, sans-serif; padding: 20px;">
            <h2 style="color: #e74c3c;">Quality Alert</h2>
            <p>{message}</p>
            <hr>
            <p style="color: #999; font-size: 0.85em;">
                This is an automated alert from the GoCanvas Production Verification App.
            </p>
        </body>
        </html>
        """
        self.send_report(subject, html, recipients)
