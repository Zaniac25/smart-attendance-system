"""
Models for the Student Attendance System.

Design decisions:
- Student is decoupled from Django's User model (students aren't system users)
- Attendance has a unique_together constraint to prevent duplicate daily entries at DB level
- AttendanceSettings is a singleton — one row configures system-wide behaviour
- is_late is computed and stored on save (denormalized for fast querying/reporting)
"""

from django.db import models
from django.conf import settings
from django.utils import timezone
from datetime import time
from django.contrib.auth.models import User
from datetime import date, timedelta


class Student(models.Model):
    student_id    = models.CharField(max_length=20, unique=True, db_index=True)
    name = models.CharField(max_length=100)
    course = models.CharField(max_length=50, blank=True, default='')   # e.g. Btech
    branch = models.CharField(max_length=50, blank=True, default='')   # e.g. CSE
    section = models.CharField(max_length=20, blank=True, default='')   # e.g. A
    student_class = models.CharField(max_length=100)                          
    email = models.EmailField(blank=True, null=True)
    face_enrolled = models.BooleanField(default=False)
    qr_generated  = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    admission_year = models.PositiveIntegerField(null=True, blank=True, help_text="e.g. 2022 for the 2022-26 batch")


    class Meta:
        ordering = ['course', 'branch', 'section', 'name']
        verbose_name = 'Student'
        verbose_name_plural = 'Students'

    def __str__(self):
        return f"{self.student_id} — {self.name} ({self.student_class})"

    def save(self, *args, **kwargs):
        """Auto-generate student_class from course + branch + section."""
        parts = [p.strip() for p in [self.course, self.branch] if p.strip()]
        if self.section.strip():
            parts.append(f"Sec {self.section.strip()}")
        if parts:
            self.student_class = ' '.join(parts)
        super().save(*args, **kwargs)

    @property
    def attendance_percentage(self):
        from .models_phase2 import AcademicSession
        from datetime import date
 
        session = AcademicSession.objects.filter(course=self.course, is_active=True).first()
 
        total_present = Attendance.objects.filter(student=self).count()
 
        if session:
            working_days = session.get_working_days(up_to_date=date.today())
        else:
            working_days = Attendance.objects.values('date').distinct().count()
 
        if working_days == 0:
            return 0.0
        return round((total_present / working_days) * 100, 1)
    
    @property
    def batch(self):
        from .models_phase2 import get_batch_string
        return get_batch_string(self.admission_year, self.course)
 
    @property
    def current_year_label(self):
        from .models_phase2 import compute_student_year
        return compute_student_year(self.admission_year, self.course)


class Attendance(models.Model):
    student = models.ForeignKey(
        Student,
        on_delete=models.CASCADE,
        related_name='attendance_records'
    )
    date = models.DateField(db_index=True)
    time = models.TimeField()
    is_late = models.BooleanField(default=False)
    is_manual = models.BooleanField(default=False)  
    marked_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('student', 'date')
        ordering = ['-date', 'time']
        verbose_name = 'Attendance Record'
        verbose_name_plural = 'Attendance Records'

    def __str__(self):
        status = " [LATE]" if self.is_late else ""
        source = " [MANUAL]" if self.is_manual else ""
        return f"{self.student.name} — {self.date}{status}{source}"

    def save(self, *args, **kwargs):
        try:
            config = AttendanceSettings.objects.filter(pk=1).first()
            cutoff = config.late_cutoff_time if config else time(9, 30)
            self.is_late = self.time > cutoff
        except Exception:
            self.is_late = False
        super().save(*args, **kwargs)


class AttendanceSettings(models.Model):
    """
    Singleton model for system-wide configuration.
    Only one row should ever exist — enforced in save().
    """
    # Attendance window — scanner only marks within this range
    attendance_start_time = models.TimeField(
        default=time(8, 0),
        help_text="Attendance marking opens at this time"
    )
    attendance_end_time = models.TimeField(
        default=time(11, 0),
        help_text="Attendance marking closes at this time"
    )
    late_cutoff_time = models.TimeField(
        default=time(9, 30),
        help_text="Attendance marked after this time (but before end) is flagged as late"
    )
    notification_email = models.EmailField(
        blank=True,
        help_text="Send daily absent report to this email"
    )
    notify_on_absent = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'System Settings'
        verbose_name_plural = 'System Settings'

    def __str__(self):
        return f"Settings (cutoff: {self.late_cutoff_time})"

    def save(self, *args, **kwargs):
        self.pk = 1  # Enforce singleton
        super().save(*args, **kwargs)

    @classmethod
    def get(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj
    

class TeacherProfile(models.Model):
    user = models.OneToOneField('auth.User', on_delete=models.CASCADE, related_name='teacher_profile')
    assigned_classes = models.JSONField(default=list)

    class Meta:
        verbose_name = 'Teacher Profile'
        verbose_name_plural = 'Teacher Profiles'
    
    def __str__(self):
        return f"Teacher: {self.user.username}"
    
    def get_students(self):
        return Student.objects.filter(student_class__in=self.assigned_classes)
    
    def get_student_ids(self):
        return list(self.get_students().values_list('student_id', flat=True))
    

class StudentProfile(models.Model):
    user = models.OneToOneField('auth.User', on_delete=models.CASCADE, related_name='student_profile')
    student = models.OneToOneField(Student, on_delete=models.CASCADE, related_name='user_profile')

    class Meta:
        verbose_name = 'Student Profile'
        verbose_name_plural = 'Student Profiles'
    
    def __str__(self):
        return f"Student: {self.student.name}"
    
    
class ChangeRequest(models.Model):
    STATUS_CHOICES = [
        ('pending',  'Pending'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
    ]
    TYPE_CHOICES = [
        ('attendance',   'Attendance Change'),
        ('student_info', 'Student Info Change'),
        ('other',        'Other'),
    ]
    
    requested_by  = models.ForeignKey(
        'auth.User', on_delete=models.CASCADE, related_name='change_requests'
    )
    student_id = models.CharField(max_length=20, db_index=True)
    request_type  = models.CharField(max_length=20, choices=TYPE_CHOICES)
    description = models.TextField(help_text='Describe exactly what needs to change')
    date_affected = models.DateField(null=True, blank=True, help_text='Relevant date if attendance change')
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='pending', db_index=True)
    admin_note = models.TextField(blank=True, help_text='Admin response / reason')
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    
    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Change Request'
        verbose_name_plural = 'Change Requests'
    
    def __str__(self):
        return f"{self.requested_by.username} → {self.student_id} [{self.status}]"
    
    @property
    def is_pending(self):
        return self.status == 'pending'
    




class AcademicSession(models.Model):
    """
    One row per course per academic year.
    e.g. Btech | 2024-08-01 | 2025-05-31 | active=True

    Attendance % denominator = working days within this session
    (Sundays + holidays excluded).
    """
    course     = models.CharField(max_length=50, help_text="e.g. Btech, BCA, BCS")
    name       = models.CharField(max_length=100, help_text="e.g. 2024-25")
    start_date = models.DateField()
    end_date   = models.DateField()
    is_active  = models.BooleanField(
        default=False,
        help_text="Only one session per course should be active at a time"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-start_date']
        verbose_name = 'Academic Session'
        verbose_name_plural = 'Academic Sessions'
        unique_together = ('course', 'name')

    def __str__(self):
        status = " [ACTIVE]" if self.is_active else ""
        return f"{self.course} — {self.name} ({self.start_date} → {self.end_date}){status}"

    def save(self, *args, **kwargs):
        """Enforce: only one active session per course."""
        if self.is_active:
            AcademicSession.objects.filter(
                course=self.course, is_active=True
            ).exclude(pk=self.pk).update(is_active=False)
        super().save(*args, **kwargs)

    def get_holidays(self):
        """QuerySet of Holiday dates within this session."""
        return Holiday.objects.filter(
            models.Q(session=self) | models.Q(session__isnull=True),
            date__range=(self.start_date, self.end_date)
        )

    def get_holiday_dates(self):
        """Set of holiday date objects for fast lookup."""
        return set(self.get_holidays().values_list('date', flat=True))

    def get_working_days(self, up_to_date=None):
        """
        Count working days from session start to up_to_date (or today/end_date).
        Excludes: Sundays (weekday==6) and holidays.
        """
        end   = min(up_to_date or date.today(), self.end_date)
        start = self.start_date

        if end < start:
            return 0

        holiday_dates = self.get_holiday_dates()
        count   = 0
        current = start
        while current <= end:
            if current.weekday() != 6 and current not in holiday_dates:
                count += 1
            current += timedelta(days=1)
        return count

    def is_working_day(self, check_date):
        """Returns True if the given date is a working day in this session."""
        if check_date < self.start_date or check_date > self.end_date:
            return False
        if check_date.weekday() == 6:   # Sunday
            return False
        return not Holiday.objects.filter(
            models.Q(session=self) | models.Q(session__isnull=True),
            date=check_date
        ).exists()

    @property
    def start_year(self):
        return self.start_date.year



class Holiday(models.Model):
    """
    A single holiday date. session=None means it applies to ALL sessions
    (e.g. national holidays). session=X means it's specific to that session.
    """
    date    = models.DateField(db_index=True)
    name    = models.CharField(max_length=100)
    session = models.ForeignKey(
        AcademicSession,
        on_delete=models.CASCADE,
        null=True, blank=True,
        related_name='holidays',
        help_text="Leave blank to apply to all sessions (national holidays)"
    )

    class Meta:
        ordering = ['date']
        unique_together = ('date', 'session')
        verbose_name = 'Holiday'
        verbose_name_plural = 'Holidays'

    def __str__(self):
        scope = self.session.name if self.session else "All Sessions"
        return f"{self.date} — {self.name} ({scope})"



class Timetable(models.Model):
    """
    Stores a teacher's timetable.
    Supports two modes:
      1. File upload (PDF/image/Excel) — admin uploads, teacher downloads
      2. JSON grid  — admin fills the grid form in the dashboard
    Both can coexist; teacher sees whichever is available.
    """
    teacher   = models.OneToOneField(
        TeacherProfile,
        on_delete=models.CASCADE,
        related_name='timetable'
    )
    session   = models.ForeignKey(
        AcademicSession,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        help_text="Which session this timetable applies to"
    )
    # Mode 1: file upload
    file      = models.FileField(
        upload_to='timetables/',
        null=True, blank=True,
        help_text="PDF, image, or Excel file"
    )
    # Mode 2: JSON grid
    # Structure: {"Monday": {"1": "Math 10A", "2": "Physics 10B"}, ...}
    grid_data = models.JSONField(
        null=True, blank=True,
        help_text="Structured timetable data from the grid form"
    )
    notes     = models.TextField(blank=True, help_text="Any additional info for the teacher")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Timetable'
        verbose_name_plural = 'Timetables'

    def __str__(self):
        return f"Timetable: {self.teacher.user.username}"

    @property
    def has_file(self):
        return bool(self.file)

    @property
    def has_grid(self):
        return bool(self.grid_data)

    def get_file_extension(self):
        if self.file:
            return self.file.name.split('.')[-1].lower()
        return None



COURSE_DURATION = {
    'Btech': 4,
    'BCA':   3,
    'BCS':   3,
    'MCA':   2,
    'MBA':   2,
    'MSc':   2,
    'BSc':   3,
    'BCom':  3,
    'BA':    3,
}

YEAR_LABELS = {1: '1st Year', 2: '2nd Year', 3: '3rd Year', 4: '4th Year'}


def get_batch_string(admission_year, course):
    """Returns '2022-26' style string."""
    if not admission_year:
        return ''
    duration = COURSE_DURATION.get(course, 4)
    end_year  = admission_year + duration
    return f"{admission_year}-{str(end_year)[2:]}"   # e.g. "2022-26"


def get_current_academic_year(course):
    """
    Returns the start year of the current active academic session for this course,
    or falls back to today's year if no session exists.
    """
    session = AcademicSession.objects.filter(course=course, is_active=True).first()
    if session:
        return session.start_year
    today = date.today()
    # Academic year starts in July/August — if before July, current year-1
    return today.year if today.month >= 7 else today.year - 1


def compute_student_year(admission_year, course):
    """
    Returns e.g. '2nd Year' based on how many years since admission.
    Uses the active session's start year if available.
    """
    if not admission_year:
        return ''
    session_year = get_current_academic_year(course)
    year_num     = session_year - admission_year + 1
    duration     = COURSE_DURATION.get(course, 4)
    if year_num < 1 or year_num > duration:
        return ''
    return YEAR_LABELS.get(year_num, f'Year {year_num}')