"""
Models — ABIT Attendance System (v2)

Key changes over v1:
  - Student.roll_number  : structured regd/roll no; auto-parsed to admission_year
  - Student.session      : FK to AcademicSession (which batch the student is in)
  - Student.batch        : property derived from admission_year + course duration
  - AcademicSession helpers: is_today_holiday(), is_today_working_day()
  - Holiday unique_together now properly enforced with session=None for globals
"""

from django.db import models
from django.conf import settings
from django.utils import timezone
from datetime import time, date, timedelta
from django.contrib.auth.models import User
import re
from django.db.models import Q

 
#  COURSE METADATA
 

COURSE_DURATION = {
    'Btech':  4,
    'BTech':  4,
    'btech':  4,
    'BCA':    3,
    'BCS':    3,
    'MCA':    2,
    'MBA':    2,
    'MSc':    2,
    'BSc':    3,
    'BCom':   3,
    'BA':     3,
    'Mtech':  2,
    'MTech':  2,
    'PhD':    5,
}

YEAR_LABELS = {1: '1st Year', 2: '2nd Year', 3: '3rd Year', 4: '4th Year', 5: '5th Year'}

 
#  ROLL NUMBER FORMATS SUPPORTED
#  Format A (most common):  YY + COURSE_CODE + BRANCH + SEC + NUM
#    e.g. 22BTCSEA001, 21BCSAIB001, 23BCAA01
#  Format B:  YYYY prefix
#    e.g. 2022BTCSEA001
#  Format C:  YY + 3-digit number (legacy, no structured info)
#    e.g. 22001
#
#  The parser extracts the 2-digit year and maps:
#    22 → 2022, 21 → 2021, 20 → 2020 ... (2000 + yy if yy <= 50)
 

ROLL_PATTERNS = [
    # e.g. 22BTCSEA001 or 22BCSAIA001
    re.compile(r'^(?P<yy>\d{2})(?P<rest>[A-Z].*)$', re.IGNORECASE),
    # e.g. 2022BTCSEA001
    re.compile(r'^(?P<yyyy>20\d{2})(?P<rest>[A-Z].*)$', re.IGNORECASE),
    # e.g. 22001 (legacy — year only extractable)
    re.compile(r'^(?P<yy>\d{2})\d{3,}$'),
]


def parse_roll_number(roll: str):
    """
    Extract admission_year from a roll/registration number.
    Returns (admission_year: int | None, structured: bool)
    """
    if not roll:
        return None, False
    roll = roll.strip()
    # Format B: 4-digit year prefix
    m = re.match(r'^(20\d{2})', roll)
    if m:
        return int(m.group(1)), True
    # Format A/C: 2-digit year prefix
    m = re.match(r'^(\d{2})', roll)
    if m:
        yy = int(m.group(1))
        year = 2000 + yy if yy <= 50 else 1900 + yy
        if 2000 <= year <= 2099:
            return year, True
    return None, False


def get_batch_string(admission_year, course):
    """Returns '2022-26' style string."""
    if not admission_year:
        return ''
    duration = COURSE_DURATION.get(course, 4)
    end_year = admission_year + duration
    return f"{admission_year}-{str(end_year)[2:]}"


def get_current_academic_year(course=None):
    """
    Start year of the current academic session for this course.
    Falls back to calendar year logic if no session configured.
    """
    session = AcademicSession.objects.filter(is_active=True)
    if course:
        session = session.filter(course__iexact=course)
    s = session.first()
    if s:
        return s.start_date.year
    today = date.today()
    return today.year if today.month >= 7 else today.year - 1


def compute_student_year(admission_year, course):
    """Returns e.g. '2nd Year' based on academic calendar."""
    if not admission_year:
        return ''
    session_year = get_current_academic_year(course)
    year_num = session_year - admission_year + 1
    duration = COURSE_DURATION.get(course, 4)
    if year_num < 1 or year_num > duration:
        return ''
    return YEAR_LABELS.get(year_num, f'Year {year_num}')


 
#  ACADEMIC SESSION
 
class AcademicSession(models.Model):
    course = models.CharField(max_length=50, help_text="e.g. Btech, BCA, BCS")
    name = models.CharField(max_length=100, help_text="e.g. 2024-25")
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
        if self.is_active:
            AcademicSession.objects.filter(
                course__iexact=self.course, is_active=True
            ).exclude(pk=self.pk).update(is_active=False)
        super().save(*args, **kwargs)

    # Holiday helpers 

    def get_holiday_dates(self):
        """Set of date objects for all holidays in this session (incl. globals)."""
        return set(
            Holiday.objects.filter(
                Q(session=self) | Q(session__isnull=True),
                date__range=(self.start_date, self.end_date)
            ).values_list('date', flat=True)
        )

    def get_holidays(self):
        return Holiday.objects.filter(
            Q(session=self) | Q(session__isnull=True),
            date__range=(self.start_date, self.end_date)
        ).order_by('date')

    def is_working_day(self, check_date=None):
        """True if check_date is within session, not a Sunday, and not a holiday."""
        if check_date is None:
            check_date = date.today()
        if check_date < self.start_date or check_date > self.end_date:
            return False
        if check_date.weekday() == 6:   # Sunday
            return False
        return check_date not in self.get_holiday_dates()

    def get_working_days(self, up_to_date=None):
        end = min(up_to_date or date.today(), self.end_date)
        start   = self.start_date
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

    def get_holiday_name(self, check_date):
        h = Holiday.objects.filter(
            Q(session=self) | Q(session__isnull=True),
            date=check_date
        ).first()
        return h.name if h else None

    @property
    def start_year(self):
        return self.start_date.year

    @property
    def admission_year(self):
        """The year students enrolled in this session started their course."""
        return self.start_date.year


 
#  HOLIDAY
 
class Holiday(models.Model):
    date = models.DateField(db_index=True)
    name = models.CharField(max_length=100)
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


 

class Student(models.Model):
    student_id = models.CharField(max_length=20, unique=True, db_index=True)
    name = models.CharField(max_length=100)
    course = models.CharField(max_length=50,  blank=True, default='')
    branch = models.CharField(max_length=50,  blank=True, default='')
    section = models.CharField(max_length=20,  blank=True, default='')
    student_class = models.CharField(max_length=100)
    email = models.EmailField(blank=True, null=True)
    face_enrolled = models.BooleanField(default=False)
    qr_generated  = models.BooleanField(default=False)

    # ── Batch / session fields 
    admission_year = models.PositiveIntegerField(
        null=True, blank=True,
        help_text="e.g. 2022 for the 2022-26 batch"
    )
    roll_number = models.CharField(
        max_length=30, blank=True, default='', db_index=True,
        help_text="Structured roll/regd no — e.g. 22BTCSEA001. Auto-parsed to fill admission_year.",
    )
    session = models.ForeignKey(
        AcademicSession,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='students',
        help_text="Academic session this student is enrolled in",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['course', 'branch', 'section', 'name']
        verbose_name = 'Student'
        verbose_name_plural = 'Students'

    def __str__(self):
        return f"{self.student_id} — {self.name} ({self.student_class})"

    def save(self, *args, **kwargs):
        # Auto-generate student_class label from course+branch+section
        parts = [p.strip() for p in [self.course, self.branch] if p.strip()]
        if self.section.strip():
            parts.append(f"Sec {self.section.strip()}")
        if parts:
            self.student_class = ' '.join(parts)

        # Auto-parse admission_year from roll_number if not set
        if self.roll_number and not self.admission_year:
            year, ok = parse_roll_number(self.roll_number)
            if ok and year:
                self.admission_year = year

        # Auto-assign session if not set but admission_year + course are known
        if not self.session_id and self.admission_year and self.course:
            session = AcademicSession.objects.filter(
                course__iexact=self.course,
                start_date__year=self.admission_year,
            ).first()
            if session:
                self.session = session

        if not self.pk:  # Only for new students (not existing ones)
            self.qr_generated = True

        super().save(*args, **kwargs)


    @property
    def batch(self):
        return get_batch_string(self.admission_year, self.course)

    @property
    def current_year_label(self):
        return compute_student_year(self.admission_year, self.course)

    @property
    def attendance_percentage(self):
        total_present = Attendance.objects.filter(student=self).count()

        # Prefer the student's own session; fall back to any active session for the course
        session = self.session
        if not session:
            session = AcademicSession.objects.filter(
                course__iexact=self.course, is_active=True
            ).first()

        if session:
            working_days = session.get_working_days(up_to_date=date.today())
        else:
            working_days = Attendance.objects.values('date').distinct().count()

        if working_days == 0:
            return 0.0
        return round((total_present / working_days) * 100, 1)


 
#  ATTENDANCE
 

class Attendance(models.Model):
    student   = models.ForeignKey(
        Student, on_delete=models.CASCADE, related_name='attendance_records'
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
        status = " [LATE]"   if self.is_late   else ""
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


 
#  ATTENDANCE SETTINGS (singleton)
 

class AttendanceSettings(models.Model):
    attendance_start_time = models.TimeField(
        default=time(8, 0), help_text="Attendance marking opens at this time"
    )
    attendance_end_time = models.TimeField(
        default=time(11, 0), help_text="Attendance marking closes at this time"
    )
    late_cutoff_time = models.TimeField(
        default=time(9, 30),
        help_text="Attendance marked after this time (but before end) is flagged as late"
    )
    notification_email = models.EmailField(
        blank=True, help_text="Send daily absent report to this email"
    )
    notify_on_absent = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'System Settings'
        verbose_name_plural = 'System Settings'

    def __str__(self):
        return f"Settings (cutoff: {self.late_cutoff_time})"

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def get(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


 
#  ROLE PROFILES
 

class TeacherProfile(models.Model):
    user = models.OneToOneField(
        'auth.User', on_delete=models.CASCADE, related_name='teacher_profile'
    )
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
    user    = models.OneToOneField(
        'auth.User', on_delete=models.CASCADE, related_name='student_profile'
    )
    student = models.OneToOneField(
        Student, on_delete=models.CASCADE, related_name='user_profile'
    )

    class Meta:
        verbose_name = 'Student Profile'
        verbose_name_plural = 'Student Profiles'

    def __str__(self):
        return f"Student: {self.student.name}"


 
#  CHANGE REQUEST
 

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
    description   = models.TextField(help_text='Describe exactly what needs to change')
    date_affected = models.DateField(null=True, blank=True, help_text='Relevant date if attendance change')
    status = models.CharField(
        max_length=10, choices=STATUS_CHOICES, default='pending', db_index=True
    )
    admin_note = models.TextField(blank=True, help_text='Admin response / reason')
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at   = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Change Request'
        verbose_name_plural = 'Change Requests'

    def __str__(self):
        return f"{self.requested_by.username} → {self.student_id} [{self.status}]"

    @property
    def is_pending(self):
        return self.status == 'pending'


 
#  TIMETABLE
 

class Timetable(models.Model):
    teacher = models.OneToOneField(
        TeacherProfile, on_delete=models.CASCADE, related_name='timetable'
    )
    session = models.ForeignKey(
        AcademicSession, on_delete=models.SET_NULL, null=True, blank=True,
        help_text="Which session this timetable applies to"
    )
    file      = models.FileField(
        upload_to='timetables/', null=True, blank=True,
        help_text="PDF, image, or Excel file"
    )
    grid_data = models.JSONField(
        null=True, blank=True,
        help_text="Structured timetable data from the grid form"
    )
    notes = models.TextField(blank=True)
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


 
#  GLOBAL HELPERS
 

def is_today_a_working_day():
    """
    Returns (is_working: bool, reason: str | None)
    Called by ProcessFrameView and the scanner before marking attendance.
    """
    today = date.today()

    # Sunday check
    if today.weekday() == 6:
        return False, "Today is Sunday — attendance not recorded."

    # Global holiday check (session=None)
    global_holiday = Holiday.objects.filter(date=today, session__isnull=True).first()
    if global_holiday:
        return False, f"Today is a holiday: {global_holiday.name}"

    # Session-specific holiday check
    session_holiday = Holiday.objects.filter(
        date=today, session__isnull=False,
        session__start_date__lte=today, session__end_date__gte=today,
    ).first()
    if session_holiday:
        return False, f"Today is a holiday: {session_holiday.name}"

    return True, None