"""
Analytics — ABIT Attendance System (v2)
========================================
All report functions now:
  1. Respect Sundays  — never counted as absent/working days
  2. Respect holidays — global (session=None) and session-specific
  3. Return day_type  — 'working' | 'sunday' | 'holiday' for frontend coloring
"""

from django.db.models import Count, Q
from django.utils import timezone
from datetime import date, timedelta
from collections import defaultdict
from .models import (
    Student, Attendance, AcademicSession, Holiday,
    is_today_a_working_day, parse_roll_number, COURSE_DURATION
)


  
#  INTERNAL HELPERS
  

def get_active_session(course=None):
    qs = AcademicSession.objects.filter(is_active=True)
    if course:
        qs = qs.filter(course__iexact=course)
    return qs.first()


def get_all_holiday_dates(session=None):
    """Global + session-specific holidays."""
    if session:
        qs = Holiday.objects.filter(
            Q(session=session) | Q(session__isnull=True)
        )
    else:
        qs = Holiday.objects.all()
    return set(qs.values_list('date', flat=True))


def _get_holiday_name(check_date, session=None):
    h = Holiday.objects.filter(
        Q(session=session) | Q(session__isnull=True),
        date=check_date
    ).first()
    return h.name if h else None


def classify_date(check_date, session=None, holiday_dates=None, present_dates=None, late_dates=None):
    """
    Returns one of: 'present' | 'late' | 'absent' | 'holiday' | 'sunday' | 'out_of_session'
    Used for heatmap rendering in student detail / dashboard.
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


def is_working_day(check_date, session=None, holiday_dates=None):
    if check_date.weekday() == 6:
        return False
    if session:
        if check_date < session.start_date or check_date > session.end_date:
            return False
    if holiday_dates is None:
        holiday_dates = get_all_holiday_dates(session)
    return check_date not in holiday_dates


def get_working_days_in_range(start_date, end_date, session=None, holiday_dates=None):
    if holiday_dates is None:
        holiday_dates = get_all_holiday_dates(session)
    count   = 0
    current = start_date
    while current <= end_date:
        if is_working_day(current, session, holiday_dates):
            count += 1
        current += timedelta(days=1)
    return count


  
#  DAILY REPORT  (core function — all report views use this)
  

def get_daily_report(target_date: date, student_ids: list = None) -> dict:
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
    present_count   = present_records.count()

    # Working day meta
    session       = get_active_session()
    holiday_dates = get_all_holiday_dates(session)
    day_is_sunday  = target_date.weekday() == 6
    day_is_holiday = target_date in holiday_dates
    day_is_working = not day_is_sunday and not day_is_holiday

    return {
        'date':               target_date,
        'total_students':     total,
        'present':            present_count,
        'absent':             absent_students.count(),
        'late':               late_count,
        'attendance_percentage': round((present_count / total * 100), 1) if total else 0.0,
        'present_students':   present_records,
        'absent_students':    absent_students,
        'is_working_day':     day_is_working,
        'is_sunday':          day_is_sunday,
        'is_holiday':         day_is_holiday,
        'holiday_name':       _get_holiday_name(target_date, session) if day_is_holiday else None,
    }


  
#  WEEKLY TREND (used by dashboard chart)
  

def get_weekly_trend(days: int = 7) -> dict:
    today      = timezone.localdate()
    start_date = today - timedelta(days=days - 1)
    total_students = Student.objects.count()

    records = (
        Attendance.objects
        .filter(date__range=(start_date, today))
        .values('date')
        .annotate(present=Count('id'))
        .order_by('date')
    )

    session       = get_active_session()
    holiday_dates = get_all_holiday_dates(session)
    record_map    = {r['date']: r['present'] for r in records}

    labels, present_data, absent_data, day_types = [], [], [], []
    for i in range(days):
        day     = start_date + timedelta(days=i)
        present = record_map.get(day, 0)
        dtype   = classify_date(day, session, holiday_dates)

        labels.append(day.strftime('%b %d'))
        present_data.append(present)
        # For non-working days show 0 absent (they shouldn't be counted)
        if dtype in ('sunday', 'holiday', 'out_of_session'):
            absent_data.append(0)
        else:
            absent_data.append(max(0, total_students - present))
        day_types.append(dtype)

    return {
        'labels':         labels,
        'present':        present_data,
        'absent':         absent_data,
        'day_types':      day_types,
        'total_students': total_students,
    }


  
#  CLASS-WISE REPORT
  

def get_classwise_report(target_date: date, student_ids: list = None) -> list:
    all_students = Student.objects.all()
    if student_ids is not None:
        all_students = all_students.filter(student_id__in=student_ids)

    present_qs  = Attendance.objects.filter(date=target_date)
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
            'class':      cls,
            'total':      total,
            'present':    present,
            'absent':     total - present,
            'late':       stats['late'],
            'percentage': round((present / total * 100), 1) if total else 0.0,
        })
    return result


  
#  STUDENT-LEVEL TREND (for heatmap in student detail)
  

def get_student_trend(student_id: str, days: int = 30) -> dict:
    today      = timezone.localdate()
    start_date = today - timedelta(days=days - 1)

    try:
        student = Student.objects.get(student_id=student_id)
    except Student.DoesNotExist:
        return {}

    records       = Attendance.objects.filter(
        student=student, date__range=(start_date, today)
    ).order_by('date')
    present_dates = {r.date for r in records}
    late_dates    = {r.date for r in records if r.is_late}

    session       = student.session or get_active_session(student.course)
    holiday_dates = get_all_holiday_dates(session)

    labels, statuses = [], []
    for i in range(days):
        day    = start_date + timedelta(days=i)
        status = classify_date(day, session, holiday_dates, present_dates, late_dates)
        labels.append(day.strftime('%b %d'))
        statuses.append(status)

    # Count only on actual working days
    working_present = sum(1 for d in present_dates if is_working_day(d, session, holiday_dates))
    working_late    = sum(1 for d in late_dates    if is_working_day(d, session, holiday_dates))
    working_total   = get_working_days_in_range(start_date, today, session, holiday_dates)

    return {
        'student':       student,
        'labels':        labels,
        'statuses':      statuses,
        'present_count': working_present,
        'late_count':    working_late,
        'absent_count':  max(0, working_total - working_present),
        'percentage':    round((working_present / working_total * 100), 1) if working_total else 0.0,
    }


  
#  DASHBOARD STATS (top cards)
  

def get_dashboard_stats() -> dict:
    today          = timezone.localdate()
    total          = Student.objects.count()
    present_today  = Attendance.objects.filter(date=today).count()
    late_today     = Attendance.objects.filter(date=today, is_late=True).count()

    session        = get_active_session()
    holiday_dates  = get_all_holiday_dates(session)
    day_is_sunday  = today.weekday() == 6
    day_is_holiday = today in holiday_dates
    today_is_working = not day_is_sunday and not day_is_holiday
    today_holiday_name = _get_holiday_name(today, session) if day_is_holiday else None

    week_ago = today - timedelta(days=6)
    weekly_records = (
        Attendance.objects
        .filter(date__range=(week_ago, today))
        .values('date')
        .annotate(count=Count('id'))
    )
    working_days_this_week = get_working_days_in_range(week_ago, today, session, holiday_dates)
    if weekly_records and total and working_days_this_week:
        total_possible = working_days_this_week * total
        total_present  = sum(r['count'] for r in weekly_records)
        avg = total_present / total_possible * 100
    else:
        avg = 0.0

    return {
        'total_students':   total,
        'present_today':    present_today,
        'absent_today':     max(0, total - present_today),
        'late_today':       late_today,
        'weekly_avg':       round(avg, 1),
        'today':            today,
        'today_is_working': today_is_working,
        'today_is_holiday': day_is_holiday,
        'today_is_sunday':  day_is_sunday,
        'holiday_name':     today_holiday_name,
    }


  
#  SESSION BROWSER  (for the academic session drill-down page)
  

def get_session_summary(session: AcademicSession) -> dict:
    """
    Returns structured data for the session drill-down view:
    {
      courses → [{ course, branches → [{ branch, sections → [{ section, students }] }] }]
    }
    This is already one session so we just group the students inside it.
    """
    students = session.students.all().order_by('branch', 'section', 'name')
    
    # Group: branch → section → [students]
    structure = defaultdict(lambda: defaultdict(list))
    for s in students:
        structure[s.branch or '—'][s.section or '—'].append(s)

    result = []
    for branch in sorted(structure.keys()):
        sections = []
        for sec in sorted(structure[branch].keys()):
            stud_list = structure[branch][sec]
            sections.append({
                'section':        sec,
                'students':       stud_list,
                'total':          len(stud_list),
            })
        result.append({
            'branch':   branch,
            'sections': sections,
            'total':    sum(len(structure[branch][s]) for s in structure[branch]),
        })

    return {
        'session':  session,
        'branches': result,
        'total':    students.count(),
    }


  
#  BATCH-LEVEL HELPERS
  

def get_all_batches():
    """Returns list of batch strings for the filter dropdown."""
    batches = set()
    for s in Student.objects.exclude(admission_year=None).values('admission_year', 'course'):
        dur = COURSE_DURATION.get(s['course'], 4)
        end = s['admission_year'] + dur
        batches.add(f"{s['admission_year']}-{str(end)[2:]}")
    return sorted(batches, reverse=True)


def filter_students_by_batch(qs, batch_str: str):
    """
    batch_str = '2022-26'  → admission_year = 2022
    """
    if not batch_str:
        return qs
    try:
        admission_year = int(batch_str.split('-')[0])
        return qs.filter(admission_year=admission_year)
    except (ValueError, IndexError):
        return qs