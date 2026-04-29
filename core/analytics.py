"""
Analytics Module
================
All heavy data computation lives here, keeping views thin.
Called by both API endpoints and template views.
"""

from django.db.models import Count, Q
from django.utils import timezone
from datetime import date, timedelta
from collections import defaultdict
from .models import Student, Attendance


def get_daily_report(target_date: date) -> dict:
    """
    Full daily report: present, absent, late counts + student lists.
    Single DB query per category — no N+1.
    """
    all_students = Student.objects.all()
    total = all_students.count()

    present_records = (
        Attendance.objects
        .filter(date=target_date)
        .select_related('student')
        .order_by('time')
    )

    present_ids = set(present_records.values_list('student__student_id', flat=True))
    late_count = present_records.filter(is_late=True).count()

    absent_students = all_students.exclude(student_id__in=present_ids)
    present_count = present_records.count()
    absent_count = absent_students.count()

    return {
        'date': target_date,
        'total_students': total,
        'present': present_count,
        'absent': absent_count,
        'late': late_count,
        'attendance_percentage': round((present_count / total * 100), 1) if total else 0.0,
        'present_students': present_records,
        'absent_students': absent_students,
    }


def get_weekly_trend(days: int = 7) -> dict:
    """
    Returns day-by-day attendance counts for the last `days` days.
    Used for the Chart.js line graph on the dashboard.
    """
    today = timezone.localdate()
    start_date = today - timedelta(days=days - 1)
    total_students = Student.objects.count()

    # Single query: count present per day in range
    records = (
        Attendance.objects
        .filter(date__range=(start_date, today))
        .values('date')
        .annotate(present=Count('id'))
        .order_by('date')
    )

    # Build a complete date range (fill missing days with 0)
    record_map = {r['date']: r['present'] for r in records}
    labels = []
    present_data = []
    absent_data = []

    for i in range(days):
        day = start_date + timedelta(days=i)
        present = record_map.get(day, 0)
        labels.append(day.strftime('%b %d'))
        present_data.append(present)
        absent_data.append(max(0, total_students - present))

    return {
        'labels': labels,
        'present': present_data,
        'absent': absent_data,
        'total_students': total_students,
    }


def get_classwise_report(target_date: date) -> list:
    """
    Per-class attendance breakdown for a given date.
    Returns a list sorted by class name.
    """
    all_students = Student.objects.all()
    present_ids = set(
        Attendance.objects
        .filter(date=target_date)
        .values_list('student__student_id', flat=True)
    )

    class_map = defaultdict(lambda: {'total': 0, 'present': 0, 'late': 0})

    for student in all_students:
        cls = student.student_class
        class_map[cls]['total'] += 1
        if student.student_id in present_ids:
            class_map[cls]['present'] += 1

    # Add late counts per class
    late_records = (
        Attendance.objects
        .filter(date=target_date, is_late=True)
        .values('student__student_class')
        .annotate(late=Count('id'))
    )
    for r in late_records:
        class_map[r['student__student_class']]['late'] = r['late']

    result = []
    for cls, stats in sorted(class_map.items()):
        total = stats['total']
        present = stats['present']
        result.append({
            'class': cls,
            'total': total,
            'present': present,
            'absent': total - present,
            'late': stats['late'],
            'percentage': round((present / total * 100), 1) if total else 0.0,
        })

    return result


def get_student_trend(student_id: str, days: int = 30) -> dict:
    """
    Individual student attendance history — used on student detail page.
    """
    today = timezone.localdate()
    start_date = today - timedelta(days=days - 1)

    try:
        student = Student.objects.get(student_id=student_id)
    except Student.DoesNotExist:
        return {}

    records = (
        Attendance.objects
        .filter(student=student, date__range=(start_date, today))
        .order_by('date')
    )

    present_dates = {r.date for r in records}
    late_dates = {r.date for r in records if r.is_late}

    labels, statuses = [], []
    for i in range(days):
        day = start_date + timedelta(days=i)
        labels.append(day.strftime('%b %d'))
        if day in late_dates:
            statuses.append('late')
        elif day in present_dates:
            statuses.append('present')
        else:
            statuses.append('absent')

    total_days = len([s for s in statuses if s in ('present', 'late')])
    return {
        'student': student,
        'labels': labels,
        'statuses': statuses,
        'present_count': total_days,
        'late_count': len(late_dates),
        'absent_count': days - total_days,
        'percentage': round((total_days / days * 100), 1),
    }


def get_dashboard_stats() -> dict:
    """Top-level stats for the dashboard summary cards."""
    today = timezone.localdate()
    total = Student.objects.count()
    present_today = Attendance.objects.filter(date=today).count()
    late_today = Attendance.objects.filter(date=today, is_late=True).count()

    # 7-day overall average
    week_ago = today - timedelta(days=6)
    weekly_records = (
        Attendance.objects
        .filter(date__range=(week_ago, today))
        .values('date')
        .annotate(count=Count('id'))
    )
    if weekly_records and total:
        avg = sum(r['count'] for r in weekly_records) / (7 * total) * 100
    else:
        avg = 0.0

    return {
        'total_students': total,
        'present_today': present_today,
        'absent_today': max(0, total - present_today),
        'late_today': late_today,
        'weekly_avg': round(avg, 1),
        'today': today,
    }