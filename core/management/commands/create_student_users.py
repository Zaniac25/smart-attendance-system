"""
Management Command: create_student_users
========================================
Auto-creates a Django User + StudentProfile for every Student in the DB.
Safe to run multiple times — skips existing accounts.

Usage:
    python manage.py create_student_users
    python manage.py create_student_users --default-password abit2026
    python manage.py create_student_users --student-id 220   # single student
"""

from django.core.management.base import BaseCommand
from django.contrib.auth.models import User, Group
from core.models import Student, StudentProfile


class Command(BaseCommand):
    help = 'Auto-create Django User accounts for all students (username = StudentID)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--default-password', default='changeme123',
            help='Default password assigned to all new accounts (default: changeme123)'
        )
        parser.add_argument(
            '--student-id', default=None,
            help='Only create account for this specific StudentID'
        )

    def handle(self, *args, **options):
        group, _ = Group.objects.get_or_create(name='Student')
        password  = options['default_password']
        target_id = options.get('student_id')

        qs = Student.objects.all()
        if target_id:
            qs = qs.filter(student_id=target_id)
            if not qs.exists():
                self.stdout.write(self.style.ERROR(f'Student ID "{target_id}" not found.'))
                return

        created = 0
        skipped = 0
        errors  = []

        for student in qs:
            username = str(student.student_id)

            # Skip if user already exists
            if User.objects.filter(username=username).exists():
                skipped += 1
                self.stdout.write(f'  [skip] {username} — account already exists')
                continue

            try:
                user = User.objects.create_user(
                    username   = username,
                    password   = password,
                    first_name = student.name.split()[0] if student.name else '',
                    last_name  = ' '.join(student.name.split()[1:]) if student.name else '',
                )
                user.groups.add(group)
                StudentProfile.objects.create(user=user, student=student)
                created += 1
                self.stdout.write(f'  ✓  Created: {username} ({student.name})')

            except Exception as e:
                errors.append(f'{username}: {e}')
                self.stdout.write(self.style.WARNING(f'  ✗  Failed: {username} — {e}'))

        self.stdout.write('\n' + '='*50)
        self.stdout.write(self.style.SUCCESS(f'Done — {created} created, {skipped} skipped'))
        if errors:
            self.stdout.write(self.style.WARNING(f'{len(errors)} errors — see above'))
        self.stdout.write(f'Default password: {password}')
        self.stdout.write('Students should change their password on first login.')
        self.stdout.write('='*50)