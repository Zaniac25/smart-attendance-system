from django.core.mail import EmailMultiAlternatives
from django.conf import settings
from core.models import *


def _get_low_attendance_students(threshold=75):
    """
    Returns list of Student objects whose attendance_percentage < threshold
    AND who have an email address set.
    Also returns students without email separately (so admin can see them).
    """
    all_students = Student.objects.all()
    below_threshold = []
    no_email = []

    for student in all_students:
        if student.attendance_percentage < threshold:
            if student.email:
                below_threshold.append(student)
            else:
                no_email.append(student)

    return below_threshold, no_email


def _build_alert_email(student):
    """
    Builds the HTML + plain text email body for a single student.
    Returns (subject, text_body, html_body).
    """
    pct = student.attendance_percentage
    subject = f"Attendance Alert — {student.name} ({student.student_id})"

    text_body = (
        f"Dear {student.name},\n\n"
        f"This is a reminder from ABIT Attendance System.\n\n"
        f"Your current attendance is {pct}%, which is below the required 75%.\n\n"
        f"Please attend classes regularly to avoid any academic penalty.\n\n"
        f"If you have any concerns, please contact your class teacher.\n\n"
        f"Regards,\n"
        f"ABIT Attendance System"
    )

    # Colour for the percentage badge
    if pct >= 60:
        badge_color = '#f59e0b'   # amber — low but not critical
    else:
        badge_color = '#ef4444'   # red — critical

    html_body = f"""
<!DOCTYPE html>
<html>
<body style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;padding:20px;color:#1f2937;background:#f8fafc">
 
  <!-- Header -->
  <div style="background:#1e3a5f;padding:24px 28px;border-radius:12px 12px 0 0">
    <h2 style="color:white;margin:0;font-size:20px">ABIT Attendance System</h2>
    <p style="color:#93c5fd;margin:4px 0 0;font-size:13px">Attendance Alert Notification</p>
  </div>
 
  <!-- Body -->
  <div style="background:white;padding:28px;border:1px solid #e2e8f0;border-top:none">
 
    <p style="font-size:15px;color:#374151">Dear <strong>{student.name}</strong>,</p>
 
    <p style="font-size:14px;color:#6b7280;line-height:1.6">
      We are writing to inform you that your current attendance is below the minimum
      required threshold of <strong>75%</strong>.
    </p>
 
    <!-- Attendance badge -->
    <div style="text-align:center;margin:28px 0">
      <div style="display:inline-block;background:{badge_color};border-radius:50%;
                  width:100px;height:100px;line-height:100px;
                  font-size:28px;font-weight:bold;color:white;text-align:center">
        {pct}%
      </div>
      <p style="margin:10px 0 0;color:#6b7280;font-size:13px">Current Attendance</p>
    </div>
 
    <!-- Student info table -->
    <table style="width:100%;border-collapse:collapse;margin-bottom:20px;font-size:14px">
      <tr style="background:#f8fafc">
        <td style="padding:10px 14px;border:1px solid #e2e8f0;font-weight:600;color:#374151;width:40%">Student ID</td>
        <td style="padding:10px 14px;border:1px solid #e2e8f0;color:#6b7280">{student.student_id}</td>
      </tr>
      <tr>
        <td style="padding:10px 14px;border:1px solid #e2e8f0;font-weight:600;color:#374151">Name</td>
        <td style="padding:10px 14px;border:1px solid #e2e8f0;color:#6b7280">{student.name}</td>
      </tr>
      <tr style="background:#f8fafc">
        <td style="padding:10px 14px;border:1px solid #e2e8f0;font-weight:600;color:#374151">Class</td>
        <td style="padding:10px 14px;border:1px solid #e2e8f0;color:#6b7280">{student.student_class}</td>
      </tr>
      <tr>
        <td style="padding:10px 14px;border:1px solid #e2e8f0;font-weight:600;color:#374151">Attendance</td>
        <td style="padding:10px 14px;border:1px solid #e2e8f0;color:{badge_color};font-weight:bold">{pct}%</td>
      </tr>
    </table>
 
    <!-- Warning box -->
    <div style="background:#fef2f2;border:1px solid #fecaca;border-left:4px solid #ef4444;
                border-radius:8px;padding:14px 16px;margin-bottom:20px">
      <p style="margin:0;font-size:14px;color:#991b1b;font-weight:600">⚠ Action Required</p>
      <p style="margin:6px 0 0;font-size:13px;color:#b91c1c;line-height:1.5">
        Please attend all upcoming classes regularly. Students with attendance
        below 75% may face academic penalties as per institute regulations.
      </p>
    </div>
 
    <p style="font-size:13px;color:#6b7280;line-height:1.6">
      If you have any concerns or valid reasons for your absences, please
      contact your class teacher or the academic office immediately.
    </p>
 
    <p style="font-size:14px;color:#374151;margin-top:24px">
      Regards,<br>
      <strong>ABIT Attendance System</strong><br>
      <span style="font-size:12px;color:#9ca3af">Ajay Binay Institute of Technology</span>
    </p>
  </div>
 
  <!-- Footer -->
  <div style="background:#f1f5f9;padding:12px 20px;border-radius:0 0 12px 12px;text-align:center">
    <p style="color:#94a3b8;font-size:11px;margin:0">
      This is an automated message from ABIT Attendance System. Please do not reply.
    </p>
  </div>
 
</body>
</html>
"""
    return subject, text_body, html_body


def _send_alert_to_student(student):
    """
    Sends the attendance alert email to a single student.
    Returns (success: bool, error_message: str)
    """
    if not student.email:
        return False, "No email address on file"

    if not settings.EMAIL_HOST_USER:
        return False, "EMAIL_HOST_USER not configured in environment"

    try:
        subject, text_body, html_body = _build_alert_email(student)

        msg = EmailMultiAlternatives(
            subject=subject,
            body=text_body,
            from_email=settings.DEFAULT_FROM_EMAIL or settings.EMAIL_HOST_USER,
            to=[student.email],
        )
        msg.attach_alternative(html_body, "text/html")
        msg.send()
        return True, ""

    except Exception as e:
        return False, str(e)
