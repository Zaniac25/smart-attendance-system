from django.db.models import Count, Q
from django.utils import timezone
from datetime import date, timedelta
from collections import defaultdict
from .models import *
# Lazy import to avoid circular dependency at module load time
def _get_session_model():
    return AcademicSession

def _get_holiday_model():
    return Holiday


def get_active_session(course=None):
    """
    Returns the active AcademicSession for a given course, or None.
    If course is None, returns any active session (for global views).
    """
    AcademicSession = _get_session_model()
    qs = AcademicSession.objects.filter(is_active=True)
    if course:
        qs = qs.filter(course=course)
    return qs.first()


def get_all_holiday_dates(session=None):
    """
    Returns a set of holiday dates.
    Includes: holidays for given session + global holidays (session=None).
    If session is None, returns all holidays.
    """
    Holiday = _get_holiday_model()
    if session:
        qs = Holiday.objects.filter(
            Q(session=session) | Q(session__isnull=True)
        )
    else:
        qs = Holiday.objects.all()
    return set(qs.values_list('date', flat=True))


def is_working_day(check_date, session=None, holiday_dates=None):
    """
    Returns True if check_date is a working day.
    - Not a Sunday
    - Not a holiday (checked against provided set or queried from DB)
    - Within session bounds if session is provided
    """
    if check_date.weekday() == 6:   # Sunday
        return False
    if session:
        if check_date < session.start_date or check_date > session.end_date:
            return False
    if holiday_dates is None:
        holiday_dates = get_all_holiday_dates(session)
    return check_date not in holiday_dates


def get_working_days_in_range(start_date, end_date, session=None, holiday_dates=None):
    """Count working days between two dates (inclusive)."""
    if holiday_dates is None:
        holiday_dates = get_all_holiday_dates(session)
    count   = 0
    current = start_date
    while current <= end_date:
        if is_working_day(current, session, holiday_dates):
            count += 1
        current += timedelta(days=1)
    return count


def classify_date(check_date, session=None, holiday_dates=None, present_dates=None, late_dates=None):
    """
    Returns the status of a specific date for heatmap rendering.
    Status values: 'present', 'late', 'absent', 'holiday', 'sunday', 'out_of_session'
    """
    if session:
        if check_date < session.start_date or check_date > session.end_date:
            return 'out_of_session'
    if check_date.weekday() == 6:
        return 'sunday'
    if holiday_dates is None:
        holiday_dates = get_all_holiday_dates(session)
    if check_date in holiday_dates:
        return 'holiday'
    if late_dates and check_date in late_dates:
        return 'late'
    if present_dates and check_date in present_dates:
        return 'present'
    return 'absent'


def get_daily_report(target_date: date, student_ids: list = None) -> dict:
    """
    Full daily report. Now also returns whether target_date is a working day.
    """
    all_students = Student.objects.all()
    if student_ids is not None:
        all_students = all_students.filter(student_id__in=student_ids)
    total = all_students.count()

    present_records = (
        Attendance.objects
        .filter(date=target_date)
        .select_related('student')
        .order_by('time')
    )
    if student_ids is not None:
        present_records = present_records.filter(student__student_id__in=student_ids)

    present_ids = set(present_records.values_list('student__student_id', flat=True))
    late_count  = present_records.filter(is_late=True).count()

    absent_students = all_students.exclude(student_id__in=present_ids)
    present_count = present_records.count()
    absent_count = absent_students.count()

    # Determine if today is a working day (check any active session)
    session = get_active_session()
    holiday_dates = get_all_holiday_dates(session)
    day_is_working = is_working_day(target_date, session, holiday_dates)
    is_sunday = target_date.weekday() == 6
    is_holiday = target_date in holiday_dates

    return {
        'date': target_date,
        'total_students': total,
        'present': present_count,
        'absent': absent_count,
        'late': late_count,
        'attendance_percentage': round((present_count / total * 100), 1) if total else 0.0,
        'present_students': present_records,
        'absent_students': absent_students,
        'is_working_day': day_is_working,
        'is_sunday': is_sunday,
        'is_holiday': is_holiday,
        'holiday_name': _get_holiday_name(target_date, session) if is_holiday else None,
    }


def _get_holiday_name(check_date, session=None):
    Holiday = _get_holiday_model()
    h = Holiday.objects.filter(
        Q(session=session) | Q(session__isnull=True),
        date=check_date
    ).first()
    return h.name if h else None



def get_weekly_trend(days: int = 7) -> dict:
    """
    Day-by-day attendance counts. Now marks each day's type.
    """
    today = timezone.localdate()
    start_date = today - timedelta(days=days - 1)
    total_students = Student.objects.count()

    records = (
        Attendance.objects
        .filter(date__range=(start_date, today))
        .values('date')
        .annotate(present=Count('id'))
        .order_by('date')
    )

    session = get_active_session()
    holiday_dates = get_all_holiday_dates(session)
    record_map = {r['date']: r['present'] for r in records}

    labels, present_data, absent_data, day_types = [], [], [], []

    for i in range(days):
        day = start_date + timedelta(days=i)
        present = record_map.get(day, 0)
        dtype = classify_date(day, session, holiday_dates)

        labels.append(day.strftime('%b %d'))
        present_data.append(present)
        absent_data.append(max(0, total_students - present) if dtype == 'working' else 0)
        day_types.append(dtype)

    return {
        'labels': labels,
        'present': present_data,
        'absent': absent_data,
        'day_types': day_types,
        'total_students': total_students,
    }



def get_classwise_report(target_date: date, student_ids: list = None) -> list:
    all_students = Student.objects.all()
    if student_ids is not None:
        all_students = all_students.filter(student_id__in=student_ids)

    present_qs = Attendance.objects.filter(date=target_date)
    if student_ids is not None:
        present_qs = present_qs.filter(student__student_id__in=student_ids)

    present_ids = set(present_qs.values_list('student__student_id', flat=True))
    class_map   = defaultdict(lambda: {'total': 0, 'present': 0, 'late': 0})

    for student in all_students:
        cls = student.student_class
        class_map[cls]['total'] += 1
        if student.student_id in present_ids:
            class_map[cls]['present'] += 1

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
        total   = stats['total']
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
    Individual student trend. Now classifies each day with holiday/sunday awareness.
    """
    today = timezone.localdate()
    start_date = today - timedelta(days=days - 1)

    try:
        student = Student.objects.get(student_id=student_id)
    except Student.DoesNotExist:
        return {}

    records = Attendance.objects.filter(student=student, date__range=(start_date, today)).order_by('date')
    present_dates = {r.date for r in records}
    late_dates = {r.date for r in records if r.is_late}

    session = get_active_session(student.course)
    holiday_dates = get_all_holiday_dates(session)

    labels, statuses = [], []
    for i in range(days):
        day = start_date + timedelta(days=i)
        status = classify_date(day, session, holiday_dates, present_dates, late_dates)
        labels.append(day.strftime('%b %d'))
        statuses.append(status)

    # Counts only working days for accurate stats
    working_present = sum(1 for d in present_dates if is_working_day(d, session, holiday_dates))
    working_late = sum(1 for d in late_dates    if is_working_day(d, session, holiday_dates))
    working_total = get_working_days_in_range(start_date, today, session, holiday_dates)

    return {
        'student': student,
        'labels': labels,
        'statuses': statuses,
        'present_count': working_present,
        'late_count': working_late,
        'absent_count': max(0, working_total - working_present),
        'percentage': round((working_present / working_total * 100), 1) if working_total else 0.0,
    }



def get_dashboard_stats() -> dict:
    today = timezone.localdate()
    total = Student.objects.count()
    present_today  = Attendance.objects.filter(date=today).count()
    late_today = Attendance.objects.filter(date=today, is_late=True).count()

    session = get_active_session()
    holiday_dates  = get_all_holiday_dates(session)
    today_is_working = is_working_day(today, session, holiday_dates)
    today_is_holiday = today in holiday_dates
    today_holiday_name = _get_holiday_name(today, session) if today_is_holiday else None

    week_ago = today - timedelta(days=6)
    weekly_records = (
        Attendance.objects
        .filter(date__range=(week_ago, today))
        .values('date')
        .annotate(count=Count('id'))
    )
    if weekly_records and total:
        # Only average over working days
        working = get_working_days_in_range(week_ago, today, session, holiday_dates)
        avg = sum(r['count'] for r in weekly_records) / (working * total) * 100 if working else 0
    else:
        avg = 0.0

    return {
        'total_students': total,
        'present_today': present_today,
        'absent_today': max(0, total - present_today),
        'late_today': late_today,
        'weekly_avg': round(avg, 1),
        'today': today,
        'today_is_working': today_is_working,
        'today_is_holiday': today_is_holiday,
        'holiday_name': today_holiday_name,
    }