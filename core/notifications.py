"""
Notifications Module
====================
Sends daily absent report emails via Gmail SMTP.
Called by APScheduler (scheduler.py) at a configured time each day.
"""

from django.core.mail import send_mail, EmailMultiAlternatives
from django.template.loader import render_to_string
from django.conf import settings
from django.utils import timezone
from .models import Student, Attendance, AttendanceSettings
from .analytics import get_daily_report
import logging

logger = logging.getLogger(__name__)


def send_daily_absent_report(target_date=None):
    """
    Send an HTML email listing absent students for the given date.
    Called automatically by the scheduler, or manually from admin.

    Args:
        target_date: date object. Defaults to today.
    """
    config = AttendanceSettings.get()

    if not config.notify_on_absent:
        logger.info("Absent notifications are disabled. Skipping.")
        return False

    if not config.notification_email:
        logger.warning("No notification email configured. Skipping.")
        return False

    if not settings.EMAIL_HOST_USER:
        logger.error("EMAIL_HOST_USER not set in environment. Cannot send email.")
        return False

    if target_date is None:
        target_date = timezone.localdate()

    report = get_daily_report(target_date)

    if report['absent'] == 0:
        logger.info(f"No absent students on {target_date}. No email sent.")
        return True

    subject = f"[ABIT Attendance] Absent Students — {target_date.strftime('%B %d, %Y')}"

    # Plain text fallback
    absent_names = "\n".join(
        f"  - {s.name} ({s.student_class})"
        for s in report['absent_students']
    )
    text_body = (
        f"Daily Attendance Report — {target_date}\n\n"
        f"Total Students : {report['total_students']}\n"
        f"Present        : {report['present']}\n"
        f"Absent         : {report['absent']}\n"
        f"Late           : {report['late']}\n\n"
        f"Absent Students:\n{absent_names}\n"
    )

    # HTML email body
    html_body = _build_html_email(report, target_date)

    try:
        msg = EmailMultiAlternatives(
            subject=subject,
            body=text_body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[config.notification_email],
        )
        msg.attach_alternative(html_body, "text/html")
        msg.send()
        logger.info(f"Absent report sent to {config.notification_email} for {target_date}")
        return True

    except Exception as e:
        logger.error(f"Failed to send absent report: {e}")
        return False


def _build_html_email(report, target_date) -> str:
    """Build a clean HTML email body without requiring a template file."""
    rows = "".join(
        f"<tr><td style='padding:8px;border-bottom:1px solid #eee'>{s.student_id}</td>"
        f"<td style='padding:8px;border-bottom:1px solid #eee'>{s.name}</td>"
        f"<td style='padding:8px;border-bottom:1px solid #eee'>{s.student_class}</td></tr>"
        for s in report['absent_students']
    )

    percentage = report['attendance_percentage']
    bar_color = '#22c55e' if percentage >= 75 else '#f59e0b' if percentage >= 50 else '#ef4444'

    return f"""
    <!DOCTYPE html>
    <html>
    <body style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;padding:20px;color:#1f2937">
      <div style="background:#1e3a5f;padding:20px;border-radius:8px 8px 0 0">
        <h2 style="color:white;margin:0">ABIT Attendance Report</h2>
        <p style="color:#93c5fd;margin:4px 0 0">{target_date.strftime('%A, %B %d, %Y')}</p>
      </div>

      <div style="background:#f8fafc;padding:20px;border:1px solid #e2e8f0">
        <!-- Summary cards -->
        <div style="display:flex;gap:12px;margin-bottom:20px">
          <div style="flex:1;background:white;padding:16px;border-radius:8px;border-left:4px solid #22c55e;text-align:center">
            <div style="font-size:28px;font-weight:bold;color:#22c55e">{report['present']}</div>
            <div style="color:#6b7280;font-size:13px">Present</div>
          </div>
          <div style="flex:1;background:white;padding:16px;border-radius:8px;border-left:4px solid #ef4444;text-align:center">
            <div style="font-size:28px;font-weight:bold;color:#ef4444">{report['absent']}</div>
            <div style="color:#6b7280;font-size:13px">Absent</div>
          </div>
          <div style="flex:1;background:white;padding:16px;border-radius:8px;border-left:4px solid #f59e0b;text-align:center">
            <div style="font-size:28px;font-weight:bold;color:#f59e0b">{report['late']}</div>
            <div style="color:#6b7280;font-size:13px">Late</div>
          </div>
        </div>

        <!-- Progress bar -->
        <div style="background:white;padding:16px;border-radius:8px;margin-bottom:20px">
          <div style="display:flex;justify-content:space-between;margin-bottom:8px">
            <span style="font-weight:600">Attendance Rate</span>
            <span style="color:{bar_color};font-weight:bold">{percentage}%</span>
          </div>
          <div style="background:#e5e7eb;border-radius:999px;height:10px">
            <div style="background:{bar_color};width:{percentage}%;height:10px;border-radius:999px"></div>
          </div>
        </div>

        <!-- Absent table -->
        <div style="background:white;border-radius:8px;overflow:hidden">
          <div style="background:#fee2e2;padding:12px 16px;font-weight:600;color:#991b1b">
            Absent Students ({report['absent']})
          </div>
          <table style="width:100%;border-collapse:collapse">
            <thead>
              <tr style="background:#fef2f2">
                <th style="padding:10px;text-align:left;color:#6b7280;font-size:12px">ID</th>
                <th style="padding:10px;text-align:left;color:#6b7280;font-size:12px">NAME</th>
                <th style="padding:10px;text-align:left;color:#6b7280;font-size:12px">CLASS</th>
              </tr>
            </thead>
            <tbody>{rows}</tbody>
          </table>
        </div>
      </div>

      <div style="background:#f1f5f9;padding:12px 20px;border-radius:0 0 8px 8px;text-align:center">
        <p style="color:#94a3b8;font-size:12px;margin:0">
          ABIT Student Attendance System — Auto-generated report
        </p>
      </div>
    </body>
    </html>
    """