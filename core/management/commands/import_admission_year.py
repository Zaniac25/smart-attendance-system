"""
Management Command: import_admission_year
==========================================
One-time command to backfill admission_year on existing students.

Three modes:
  1. Auto-detect from StudentID (if your IDs encode admission year)
  2. Set by class string pattern (e.g. all "Btech CSE Sec A" students get 2022)
  3. Import from CSV file (StudentID, AdmissionYear)

Usage:
    # Mode 1: auto-detect from StudentID prefix (e.g. ID starts with 22 = 2022)
    python manage.py import_admission_year --auto-from-id --id-prefix-length 2

    # Mode 2: set year for an entire class
    python manage.py import_admission_year --class "Btech CSE Sec A" --year 2022

    # Mode 3: import from CSV
    python manage.py import_admission_year --csv students_admission.csv

    # Preview without saving
    python manage.py import_admission_year --auto-from-id --id-prefix-length 2 --dry-run

    # List current state
    python manage.py import_admission_year --list
"""

import csv
from django.core.management.base import BaseCommand
from core.models import Student


class Command(BaseCommand):
    help = 'Backfill admission_year on existing students'

    def add_arguments(self, parser):
        parser.add_argument('--auto-from-id',    action='store_true',
                            help='Auto-detect year from StudentID prefix (e.g. 22→2022, 21→2021)')
        parser.add_argument('--id-prefix-length', type=int, default=2,
                            help='How many leading digits of StudentID encode the year (default: 2)')
        parser.add_argument('--class',           dest='student_class', default=None,
                            help='Set year for all students in this class string')
        parser.add_argument('--year',            type=int, default=None,
                            help='Admission year to assign (use with --class)')
        parser.add_argument('--csv',             default=None,
                            help='Path to CSV with columns: StudentID, AdmissionYear')
        parser.add_argument('--dry-run',         action='store_true',
                            help='Preview changes without saving to DB')
        parser.add_argument('--list',            action='store_true',
                            help='Show all students and their current admission_year')
        parser.add_argument('--overwrite',       action='store_true',
                            help='Overwrite existing admission_year values (default: skip)')

    def handle(self, *args, **options):
        dry_run = options['dry_run']

        if dry_run:
            self.stdout.write(self.style.WARNING('\n⚠ DRY RUN — no changes will be saved\n'))

        # ── List mode ──────────────────────────────────────────────────────
        if options['list']:
            self._list_students()
            return

        # ── CSV mode ───────────────────────────────────────────────────────
        if options['csv']:
            self._import_from_csv(options['csv'], dry_run, options['overwrite'])
            return

        # ── Class mode ─────────────────────────────────────────────────────
        if options['student_class']:
            if not options['year']:
                self.stdout.write(self.style.ERROR('--year is required when using --class'))
                return
            self._set_by_class(options['student_class'], options['year'], dry_run, options['overwrite'])
            return

        # ── Auto-from-ID mode ──────────────────────────────────────────────
        if options['auto_from_id']:
            self._auto_from_id(options['id_prefix_length'], dry_run, options['overwrite'])
            return

        self.stdout.write(self.style.ERROR(
            'No mode specified. Use --auto-from-id, --class, --csv, or --list\n'
            'Run with --help for full usage.'
        ))

    # ── Modes ──────────────────────────────────────────────────────────────────

    def _list_students(self):
        students = Student.objects.all().order_by('course', 'student_id')
        self.stdout.write(f'\n{"ID":<12} {"Name":<25} {"Course":<10} {"AdmYear":<10} {"Batch":<10} {"Year Label"}')
        self.stdout.write('─' * 80)
        for s in students:
            year   = str(s.admission_year) if s.admission_year else '—'
            batch  = s.batch               if s.admission_year else '—'
            label  = s.current_year_label  if s.admission_year else '—'
            self.stdout.write(f'{s.student_id:<12} {s.name:<25} {s.course:<10} {year:<10} {batch:<10} {label}')
        self.stdout.write(f'\nTotal: {students.count()} students\n')

    def _auto_from_id(self, prefix_len, dry_run, overwrite):
        """
        Extract admission year from leading digits of StudentID.
        e.g. prefix_len=2: StudentID "22103" → prefix "22" → year 2022
             prefix_len=4: StudentID "2022103" → prefix "2022" → year 2022
        """
        self.stdout.write(f'\nAuto-detecting admission year from first {prefix_len} digit(s) of StudentID...\n')
        updated = skipped = errors = 0

        for student in Student.objects.all():
            sid = str(student.student_id).strip()
            if len(sid) < prefix_len:
                self.stdout.write(self.style.WARNING(f'  [skip] {sid} — too short to extract prefix'))
                errors += 1
                continue

            prefix = sid[:prefix_len]
            if not prefix.isdigit():
                self.stdout.write(self.style.WARNING(f'  [skip] {sid} — prefix "{prefix}" is not numeric'))
                errors += 1
                continue

            prefix_int = int(prefix)

            # Convert 2-digit to 4-digit year
            if prefix_len == 2:
                year = 2000 + prefix_int if prefix_int <= 50 else 1900 + prefix_int
            else:
                year = prefix_int

            # Sanity check: reasonable academic year range
            if not (2000 <= year <= 2099):
                self.stdout.write(self.style.WARNING(f'  [skip] {sid} — computed year {year} is out of range'))
                errors += 1
                continue

            if student.admission_year and not overwrite:
                self.stdout.write(f'  [skip] {sid} ({student.name}) — already has year {student.admission_year}')
                skipped += 1
                continue

            self.stdout.write(f'  {"[DRY]" if dry_run else "✓"} {sid} ({student.name}) → {year}')
            if not dry_run:
                student.admission_year = year
                student.save(update_fields=['admission_year'])
            updated += 1

        self._summary(updated, skipped, errors, dry_run)

    def _set_by_class(self, student_class, year, dry_run, overwrite):
        """Set the same admission_year for all students in a given class."""
        students = Student.objects.filter(student_class=student_class)
        if not students.exists():
            self.stdout.write(self.style.ERROR(f'No students found with class: "{student_class}"'))
            return

        self.stdout.write(f'\nSetting admission_year={year} for class: {student_class}\n')
        updated = skipped = 0

        for student in students:
            if student.admission_year and not overwrite:
                self.stdout.write(f'  [skip] {student.student_id} ({student.name}) — already has year {student.admission_year}')
                skipped += 1
                continue

            self.stdout.write(f'  {"[DRY]" if dry_run else "✓"} {student.student_id} ({student.name}) → {year}')
            if not dry_run:
                student.admission_year = year
                student.save(update_fields=['admission_year'])
            updated += 1

        self._summary(updated, skipped, 0, dry_run)

    def _import_from_csv(self, csv_path, dry_run, overwrite):
        """Import admission years from a CSV file."""
        try:
            with open(csv_path, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                rows   = list(reader)
        except FileNotFoundError:
            self.stdout.write(self.style.ERROR(f'File not found: {csv_path}'))
            return
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'Error reading file: {e}'))
            return

        self.stdout.write(f'\nImporting from {csv_path} — {len(rows)} rows\n')
        updated = skipped = errors = 0

        for i, row in enumerate(rows, 2):
            sid       = str(row.get('StudentID', '') or row.get('student_id', '')).strip()
            year_str  = str(row.get('AdmissionYear', '') or row.get('admission_year', '')).strip()

            if not sid or not year_str:
                self.stdout.write(self.style.WARNING(f'  Row {i}: missing StudentID or AdmissionYear — skipped'))
                errors += 1
                continue

            try:
                year = int(float(year_str))   # handles "2022.0" from Excel
            except ValueError:
                self.stdout.write(self.style.WARNING(f'  Row {i}: invalid year "{year_str}" — skipped'))
                errors += 1
                continue

            if not (2000 <= year <= 2099):
                self.stdout.write(self.style.WARNING(f'  Row {i}: year {year} out of range — skipped'))
                errors += 1
                continue

            try:
                student = Student.objects.get(student_id=sid)
            except Student.DoesNotExist:
                self.stdout.write(self.style.WARNING(f'  Row {i}: student "{sid}" not found — skipped'))
                errors += 1
                continue

            if student.admission_year and not overwrite:
                skipped += 1
                continue

            self.stdout.write(f'  {"[DRY]" if dry_run else "✓"} {sid} ({student.name}) → {year}')
            if not dry_run:
                student.admission_year = year
                student.save(update_fields=['admission_year'])
            updated += 1

        self._summary(updated, skipped, errors, dry_run)

    def _summary(self, updated, skipped, errors, dry_run):
        self.stdout.write('\n' + '=' * 50)
        action = 'Would update' if dry_run else 'Updated'
        self.stdout.write(self.style.SUCCESS(f'{action}: {updated}'))
        if skipped: self.stdout.write(f'Skipped (already set): {skipped}')
        if errors:  self.stdout.write(self.style.WARNING(f'Errors: {errors}'))
        if dry_run: self.stdout.write(self.style.WARNING('Run without --dry-run to apply changes.'))
        self.stdout.write('=' * 50 + '\n')