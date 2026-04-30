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


class Student(models.Model):
    student_id = models.CharField(max_length=20, unique=True, db_index=True)
    name = models.CharField(max_length=100)
    student_class = models.CharField(max_length=50)
    email = models.EmailField(blank=True, null=True)  # For absent notifications
    face_enrolled = models.BooleanField(default=False)
    qr_generated = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['student_class', 'name']
        verbose_name = 'Student'
        verbose_name_plural = 'Students'

    def __str__(self):
        return f"{self.student_id} — {self.name} ({self.student_class})"

    @property
    def attendance_percentage(self):
        """Overall attendance percentage across all recorded days."""
        total = Attendance.objects.filter(student=self).count()
        if total == 0:
            return 0.0
        # Total unique school days recorded
        all_days = Attendance.objects.values('date').distinct().count()
        if all_days == 0:
            return 0.0
        return round((total / all_days) * 100, 1)


class Attendance(models.Model):
    student = models.ForeignKey(
        Student,
        on_delete=models.CASCADE,
        related_name='attendance_records'
    )
    date = models.DateField(db_index=True)
    time = models.TimeField()
    is_late = models.BooleanField(default=False)
    is_manual = models.BooleanField(default=False)  # True = marked by admin, False = scanner
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
    late_cutoff_time = models.TimeField(
        default=time(9, 30),
        help_text="Attendance marked after this time is flagged as late"
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