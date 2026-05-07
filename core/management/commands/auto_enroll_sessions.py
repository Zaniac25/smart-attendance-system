"""
Management Command: auto_enroll_sessions
=========================================
Parses roll_number / student_id of existing students and assigns them to
the correct AcademicSession based on admission_year + course match.

Also handles students that have admission_year set but no session assigned.

Usage:
    python manage.py auto_enroll_sessions
    python manage.py auto_enroll_sessions --dry-run
    python manage.py auto_enroll_sessions --force   # re-assign even if session already set
    python manage.py auto_enroll_sessions --from-student-id  # parse student_id instead of roll_number
"""

from django.core.management.base import BaseCommand
from core.models import Student, AcademicSession, parse_roll_number


class Command(BaseCommand):
    help = 'Auto-assign existing students to AcademicSession based on roll_number or admission_year'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run',         action='store_true',
                            help='Preview without saving')
        parser.add_argument('--force',           action='store_true',
                            help='Re-assign even if session is already set')
        parser.add_argument('--from-student-id', action='store_true',
                            help='Parse student_id to extract admission_year (if roll_number not set)')
        parser.add_argument('--id-prefix-len',   type=int, default=2,
                            help='How many leading digits of student_id encode the year (default 2)')

    def handle(self, *args, **options):
        dry_run         = options['dry_run']
        force           = options['force']
        from_student_id = options['from_student_id']
        id_prefix_len   = options['id_prefix_len']

        if dry_run:
            self.stdout.write(self.style.WARNING('\n⚠  DRY RUN — no changes will be saved\n'))

        sessions = list(AcademicSession.objects.all())
        if not sessions:
            self.stdout.write(self.style.ERROR(
                'No AcademicSessions found. Create sessions first via the dashboard.'
            ))
            return

        self.stdout.write(f'Found {len(sessions)} session(s):\n')
        for s in sessions:
            self.stdout.write(
                f'  • {s.course} — {s.name}  '
                f'({s.start_date} → {s.end_date})  '
                f'[{"ACTIVE" if s.is_active else "inactive"}]'
            )
        self.stdout.write()

        assigned = 0
        skipped  = 0
        no_match = 0
        errors   = []

        for student in Student.objects.all():
            if student.session and not force:
                skipped += 1
                continue

            # 1. Try roll_number first
            year, ok = parse_roll_number(student.roll_number)

            # 2. Fall back to student_id if requested
            if not ok and from_student_id:
                sid = str(student.student_id).strip()
                if len(sid) >= id_prefix_len:
                    prefix = sid[:id_prefix_len]
                    if prefix.isdigit():
                        p = int(prefix)
                        year = 2000 + p if p <= 50 else 1900 + p
                        ok   = True

            # 3. Fall back to already-stored admission_year
            if not ok and student.admission_year:
                year = student.admission_year
                ok   = True

            if not ok or not year:
                no_match += 1
                self.stdout.write(
                    self.style.WARNING(
                        f'  [skip] {student.student_id} ({student.name}) — cannot determine admission year'
                    )
                )
                continue

            # Persist admission_year if just parsed
            if not student.admission_year:
                student.admission_year = year

            # Match session: same course (case-insensitive), start year == admission_year
            matched_session = None
            for s in sessions:
                if s.course.lower() == (student.course or '').lower() and s.start_date.year == year:
                    matched_session = s
                    break

            # Broader match: any active session for the course that covers the year
            if not matched_session:
                for s in sessions:
                    if s.course.lower() == (student.course or '').lower():
                        if s.start_date.year <= year <= s.end_date.year:
                            matched_session = s
                            break

            if not matched_session:
                no_match += 1
                self.stdout.write(
                    self.style.WARNING(
                        f'  [no session] {student.student_id} ({student.name}) '
                        f'— course={student.course}, admission_year={year}'
                    )
                )
                continue

            action = 'would assign' if dry_run else '✓ assigned'
            self.stdout.write(
                f'  {action}: {student.student_id} ({student.name}) '
                f'→ {matched_session.course} {matched_session.name}'
            )

            if not dry_run:
                student.session = matched_session
                student.save(update_fields=['session', 'admission_year'])

            assigned += 1

        self.stdout.write('\n' + '=' * 60)
        action_word = 'Would assign' if dry_run else 'Assigned'
        self.stdout.write(self.style.SUCCESS(f'{action_word}: {assigned} students'))
        self.stdout.write(f'Skipped (already assigned): {skipped}')
        self.stdout.write(f'No matching session found:  {no_match}')
        if dry_run:
            self.stdout.write(self.style.WARNING('\nRun without --dry-run to apply.'))
        self.stdout.write('=' * 60 + '\n')