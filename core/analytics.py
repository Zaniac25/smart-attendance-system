"""
Analytics — ABIT Attendance System (v2)
========================================
All report functions now:
  1. Respect Sundays  — never counted as absent/working days
  2. Respect holidays — global (session=None) and session-specific
  3. Return day_type  — 'working' | 'sunday' | 'holiday' for frontend coloring

NEW in this version:
  4. get_monthly_report()  — per-student summary for a calendar month
  5. get_session_report()  — per-student summary for a full academic session
"""

from django.db.models import Count, Q
from django.utils import timezone
from datetime import date, timedelta
from collections import defaultdict
from .models import *


  
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
    count = 0
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

    session = get_active_session()
    holiday_dates = get_all_holiday_dates(session)
    day_is_sunday  = target_date.weekday() == 6
    day_is_holiday = target_date in holiday_dates
    day_is_working = not day_is_sunday and not day_is_holiday

    return {
        'date': target_date,
        'total_students': total,
        'present': present_count,
        'absent': absent_students.count(),
        'late': late_count,
        'attendance_percentage': round((present_count / total * 100), 1) if total else 0.0,
        'present_students': present_records,
        'absent_students': absent_students,
        'is_working_day': day_is_working,
        'is_sunday': day_is_sunday,
        'is_holiday': day_is_holiday,
        'holiday_name': _get_holiday_name(target_date, session) if day_is_holiday else None,
    }


  
#  MONTHLY REPORT
#  Returns per-student attendance summary for a given year + month.
#  Skips Sundays and holidays in the working-days denominator.
  

def get_monthly_report(year: int, month: int, student_ids: list = None) -> dict:
    """
    Builds a per-student attendance summary for the given calendar month.

    Returns:
      {
        'year': int, 'month': int, 'month_name': str,
        'working_days': int,          # excludes Sundays + holidays
        'total_students': int,
        'rows': [
          {
            'student_id', 'name', 'class', 'batch',
            'present', 'absent', 'late', 'percentage'
          }, ...
        ],
        'class_summary': [
          { 'class', 'total', 'avg_present', 'avg_percentage' }, ...
        ]
      }
    """
    import calendar
    from datetime import date as dt_date

    # Month date range
    first_day = dt_date(year, month, 1)
    last_day  = dt_date(year, month, calendar.monthrange(year, month)[1])
    # Cap last_day at today so we don't count future days
    last_day  = min(last_day, timezone.localdate())

    # Working days in range (excluding Sundays + holidays)
    session = get_active_session()
    holiday_dates = get_all_holiday_dates(session)
    working_days  = get_working_days_in_range(first_day, last_day, session, holiday_dates)

    # Students
    all_students = Student.objects.all().order_by('course', 'branch', 'section', 'name')
    if student_ids is not None:
        all_students = all_students.filter(student_id__in=student_ids)

    # Attendance in range — one query, group by student
    attendance_qs = (
        Attendance.objects
        .filter(date__range=(first_day, last_day))
        .select_related('student')
    )
    if student_ids is not None:
        attendance_qs = attendance_qs.filter(student__student_id__in=student_ids)

    # Build lookup: student_id → {present_dates, late_dates}
    student_records: dict = defaultdict(lambda: {'present': set(), 'late': set()})
    for rec in attendance_qs:
        sid = rec.student.student_id
        # Only count working days
        if is_working_day(rec.date, session, holiday_dates):
            student_records[sid]['present'].add(rec.date)
            if rec.is_late:
                student_records[sid]['late'].add(rec.date)

    rows = []
    class_map: dict = defaultdict(lambda: {'total': 0, 'present_sum': 0})

    for student in all_students:
        sid = student.student_id
        rec = student_records[sid]
        present = len(rec['present'])
        late = len(rec['late'])
        absent  = max(0, working_days - present)
        pct = round((present / working_days * 100), 1) if working_days else 0.0

        rows.append({
            'student_id': sid,
            'name': student.name,
            'class': student.student_class,
            'batch': student.batch or '—',
            'present': present,
            'absent': absent,
            'late': late,
            'percentage': pct,
        })

        cls = student.student_class
        class_map[cls]['total'] += 1
        class_map[cls]['present_sum'] += present

    # Class summary
    class_summary = []
    for cls, data in sorted(class_map.items()):
        avg_present = round(data['present_sum'] / data['total'], 1) if data['total'] else 0
        avg_pct = round((avg_present / working_days * 100), 1) if working_days else 0.0
        class_summary.append({
            'class': cls,
            'total': data['total'],
            'avg_present': avg_present,
            'avg_percentage': avg_pct,
        })

    return {
        'year': year,
        'month': month,
        'month_name': first_day.strftime('%B %Y'),
        'start_date': first_day,
        'end_date': last_day,
        'working_days': working_days,
        'total_students': all_students.count(),
        'rows': rows,
        'class_summary': class_summary,
    }


  
#  SESSION REPORT
#  Returns per-student attendance summary for an entire AcademicSession.
#  Respects the session's own holiday list + global holidays + Sundays.
  
def get_session_report(session: AcademicSession, student_ids: list = None) -> dict:
    """
    Builds a per-student attendance summary for the full academic session,
    up to today (or session end_date, whichever is earlier).

    Returns same structure as get_monthly_report() plus session metadata.
    """
    end_date = min(session.end_date, timezone.localdate())
    start_date = session.start_date
    holiday_dates = get_all_holiday_dates(session)
    working_days  = get_working_days_in_range(start_date, end_date, session, holiday_dates)

    # Students — prefer session-enrolled students; fall back to all
    if student_ids is not None:
        all_students = Student.objects.filter(student_id__in=student_ids).order_by(
            'course', 'branch', 'section', 'name'
        )
    else:
        # Default: students enrolled in this session
        all_students = session.students.all().order_by('branch', 'section', 'name')
        if not all_students.exists():
            # Fallback: all students matching the session's course
            all_students = Student.objects.filter(
                course__iexact=session.course
            ).order_by('branch', 'section', 'name')

    # All attendance in session date range
    attendance_qs = (
        Attendance.objects
        .filter(date__range=(start_date, end_date))
        .select_related('student')
    )
    if student_ids is not None:
        attendance_qs = attendance_qs.filter(student__student_id__in=student_ids)
    else:
        sid_list = list(all_students.values_list('student_id', flat=True))
        attendance_qs = attendance_qs.filter(student__student_id__in=sid_list)

    # Build per-student lookup
    student_records: dict = defaultdict(lambda: {'present': set(), 'late': set()})
    for rec in attendance_qs:
        if is_working_day(rec.date, session, holiday_dates):
            sid = rec.student.student_id
            student_records[sid]['present'].add(rec.date)
            if rec.is_late:
                student_records[sid]['late'].add(rec.date)

    rows = []
    class_map: dict = defaultdict(lambda: {'total': 0, 'present_sum': 0})

    for student in all_students:
        sid = student.student_id
        rec = student_records[sid]
        present = len(rec['present'])
        late = len(rec['late'])
        absent  = max(0, working_days - present)
        pct = round((present / working_days * 100), 1) if working_days else 0.0

        rows.append({
            'student_id': sid,
            'name': student.name,
            'class': student.student_class,
            'batch': student.batch or '—',
            'roll_number': student.roll_number or '—',
            'present': present,
            'absent': absent,
            'late': late,
            'percentage': pct,
        })

        cls = student.student_class
        class_map[cls]['total'] += 1
        class_map[cls]['present_sum'] += present

    class_summary = []
    for cls, data in sorted(class_map.items()):
        avg_present = round(data['present_sum'] / data['total'], 1) if data['total'] else 0
        avg_pct     = round((avg_present / working_days * 100), 1) if working_days else 0.0
        class_summary.append({
            'class': cls,
            'total': data['total'],
            'avg_present': avg_present,
            'avg_percentage': avg_pct,
        })

    return {
        'session': session,
        'session_name': f"{session.course} — {session.name}",
        'start_date': start_date,
        'end_date': end_date,
        'working_days': working_days,
        'holiday_count':  len(holiday_dates),
        'total_students': all_students.count(),
        'rows': rows,
        'class_summary':  class_summary,
    }


  
#  WEEKLY TREND (used by dashboard chart)
  
def get_weekly_trend(days: int = 7) -> dict:
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
        if dtype in ('sunday', 'holiday', 'out_of_session'):
            absent_data.append(0)
        else:
            absent_data.append(max(0, total_students - present))
        day_types.append(dtype)

    return {
        'labels': labels,
        'present': present_data,
        'absent': absent_data,
        'day_types': day_types,
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
            'class': cls,
            'total': total,
            'present': present,
            'absent': total - present,
            'late': stats['late'],
            'percentage': round((present / total * 100), 1) if total else 0.0,
        })
    return result


  
#  STUDENT-LEVEL TREND (heatmap)

def get_student_trend(student_id: str, days: int = 30) -> dict:
    today = timezone.localdate()
    start_date = today - timedelta(days=days - 1)

    try:
        student = Student.objects.get(student_id=student_id)
    except Student.DoesNotExist:
        return {}

    records = Attendance.objects.filter(
        student=student, date__range=(start_date, today)
    ).order_by('date')
    present_dates = {r.date for r in records}
    late_dates = {r.date for r in records if r.is_late}

    session = student.session or get_active_session(student.course)
    holiday_dates = get_all_holiday_dates(session)

    labels, statuses = [], []
    for i in range(days):
        day = start_date + timedelta(days=i)
        status = classify_date(day, session, holiday_dates, present_dates, late_dates)
        labels.append(day.strftime('%b %d'))
        statuses.append(status)

    working_present = sum(1 for d in present_dates if is_working_day(d, session, holiday_dates))
    working_late    = sum(1 for d in late_dates    if is_working_day(d, session, holiday_dates))
    working_total   = get_working_days_in_range(start_date, today, session, holiday_dates)

    return {
        'student': student,
        'labels': labels,
        'statuses': statuses,
        'present_count': working_present,
        'late_count': working_late,
        'absent_count':  max(0, working_total - working_present),
        'percentage': round((working_present / working_total * 100), 1) if working_total else 0.0,
    }


  
#  DASHBOARD STATS

def get_dashboard_stats() -> dict:
    today = timezone.localdate()
    total = Student.objects.count()
    present_today  = Attendance.objects.filter(date=today).count()
    late_today = Attendance.objects.filter(date=today, is_late=True).count()

    session = get_active_session()
    holiday_dates  = get_all_holiday_dates(session)
    day_is_sunday = today.weekday() == 6
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
        'total_students': total,
        'present_today': present_today,
        'absent_today': max(0, total - present_today),
        'late_today': late_today,
        'weekly_avg': round(avg, 1),
        'today': today,
        'today_is_working': today_is_working,
        'today_is_holiday': day_is_holiday,
        'today_is_sunday':  day_is_sunday,
        'holiday_name': today_holiday_name,
    }


#  SESSION BROWSER
  

def get_session_summary(session: AcademicSession) -> dict:
    students  = session.students.all().order_by('branch', 'section', 'name')
    structure = defaultdict(lambda: defaultdict(list))
    for s in students:
        structure[s.branch or '—'][s.section or '—'].append(s)

    result = []
    for branch in sorted(structure.keys()):
        sections = []
        for sec in sorted(structure[branch].keys()):
            stud_list = structure[branch][sec]
            sections.append({'section': sec, 'students': stud_list, 'total': len(stud_list)})
        result.append({
            'branch':   branch,
            'sections': sections,
            'total':    sum(len(structure[branch][s]) for s in structure[branch]),
        })

    return {'session': session, 'branches': result, 'total': students.count()}


  
#  BATCH-LEVEL HELPERS
  
def get_all_batches():
    batches = set()
    for s in Student.objects.exclude(admission_year=None).values('admission_year', 'course'):
        dur = COURSE_DURATION.get(s['course'], 4)
        end = s['admission_year'] + dur
        batches.add(f"{s['admission_year']}-{str(end)[2:]}")
    return sorted(batches, reverse=True)


def filter_students_by_batch(qs, batch_str: str):
    if not batch_str:
        return qs
    try:
        admission_year = int(batch_str.split('-')[0])
        return qs.filter(admission_year=admission_year)
    except (ValueError, IndexError):
        return qs