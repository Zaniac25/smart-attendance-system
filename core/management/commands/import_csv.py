"""
Management Command: import_csv
==============================
Migrates your existing students.csv and attendance.csv into the Django database.

Usage:
    python manage.py import_csv
    python manage.py import_csv --students path/to/students.csv --attendance path/to/attendance.csv
"""

import csv
from datetime import datetime
from django.core.management.base import BaseCommand, CommandError
from core.models import Student, Attendance


class Command(BaseCommand):
    help = 'Import existing students.csv and attendance.csv into the database'

    def add_arguments(self, parser):
        parser.add_argument('--students', default='students.csv', help='Path to students CSV')
        parser.add_argument('--attendance', default='attendance.csv', help='Path to attendance CSV')

    def handle(self, *args, **options):
        self.import_students(options['students'])
        self.import_attendance(options['attendance'])

    def import_students(self, filepath):
        self.stdout.write(f"\nImporting students from: {filepath}")
        created, updated = 0, 0

        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    sid = row['StudentID'].strip()
                    name = row['Name'].strip()
                    cls = row['Class'].strip()

                    if not all([sid, name, cls]):
                        self.stdout.write(self.style.WARNING(f"  Skipping incomplete row: {row}"))
                        continue

                    student, was_created = Student.objects.update_or_create(
                        student_id=sid,
                        defaults={'name': name, 'student_class': cls}
                    )
                    if was_created:
                        created += 1
                    else:
                        updated += 1

        except FileNotFoundError:
            self.stdout.write(self.style.WARNING(f"  {filepath} not found — skipping students import."))
            return

        self.stdout.write(self.style.SUCCESS(
            f"  Students: {created} created, {updated} updated"
        ))

    def import_attendance(self, filepath):
        self.stdout.write(f"\nImporting attendance from: {filepath}")
        created, skipped = 0, 0

        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    sid = row['StudentID'].strip()
                    date_str = row['Date'].strip()
                    time_str = row['Time'].strip()

                    try:
                        student = Student.objects.get(student_id=sid)
                    except Student.DoesNotExist:
                        self.stdout.write(self.style.WARNING(
                            f"  Student {sid} not found — skipping attendance row"
                        ))
                        skipped += 1
                        continue

                    try:
                        record_date = datetime.strptime(date_str, '%Y-%m-%d').date()
                        record_time = datetime.strptime(time_str, '%H:%M:%S').time()
                    except ValueError:
                        self.stdout.write(self.style.WARNING(f"  Bad date/time: {date_str} {time_str}"))
                        skipped += 1
                        continue

                    _, was_created = Attendance.objects.get_or_create(
                        student=student,
                        date=record_date,
                        defaults={'time': record_time}
                    )
                    if was_created:
                        created += 1
                    else:
                        skipped += 1  # Duplicate — already in DB

        except FileNotFoundError:
            self.stdout.write(self.style.WARNING(f"  {filepath} not found — skipping attendance import."))
            return

        self.stdout.write(self.style.SUCCESS(
            f"  Attendance: {created} created, {skipped} skipped (duplicates/errors)"
        ))
        self.stdout.write(self.style.SUCCESS("\n✓ Import complete!"))