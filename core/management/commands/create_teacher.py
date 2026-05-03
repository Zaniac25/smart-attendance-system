"""
Management Command: create_teacher
===================================
Creates a teacher account and assigns classes to them.
Safe to run multiple times — updates existing profile if user already exists.

Usage:
    python manage.py create_teacher mrsmith pass123 --classes "Btech CSE Sec A" "BCS AI Sec B"
    python manage.py create_teacher --list   # list all teachers and their classes
"""

from django.core.management.base import BaseCommand
from django.contrib.auth.models import User, Group
from core.models import TeacherProfile, Student


class Command(BaseCommand):
    help = 'Create or update a teacher account with assigned classes'

    def add_arguments(self, parser):
        parser.add_argument('username', nargs='?', default=None, help='Teacher username')
        parser.add_argument('password', nargs='?', default=None, help='Teacher password')
        parser.add_argument(
            '--classes', nargs='+', default=[],
            help='One or more student_class strings e.g. "Btech CSE Sec A"'
        )
        parser.add_argument(
            '--list', action='store_true',
            help='List all current teacher accounts and their assigned classes'
        )

    def handle(self, *args, **options):
        # ── List mode ──────────────────────────────────────────────────────────
        if options['list']:
            profiles = TeacherProfile.objects.select_related('user').all()
            if not profiles:
                self.stdout.write('No teacher accounts found.')
                return
            self.stdout.write('\nTeacher Accounts:')
            self.stdout.write('─' * 50)
            for p in profiles:
                classes = ', '.join(p.assigned_classes) if p.assigned_classes else '(no classes)'
                self.stdout.write(f'  {p.user.username:<20} → {classes}')
            self.stdout.write('─' * 50)
            return

        # ── Create / update mode ───────────────────────────────────────────────
        username = options.get('username')
        password = options.get('password')

        if not username or not password:
            self.stdout.write(self.style.ERROR('Usage: create_teacher <username> <password> --classes "Class A"'))
            return

        # Validate that all class strings actually exist in DB
        invalid = []
        for cls in options['classes']:
            if not Student.objects.filter(student_class=cls).exists():
                invalid.append(cls)
        if invalid:
            self.stdout.write(self.style.WARNING(
                f'Warning: these class strings have no matching students: {invalid}'
            ))

        group, _ = Group.objects.get_or_create(name='Teacher')

        user, user_created = User.objects.get_or_create(username=username)
        if user_created:
            user.set_password(password)
            user.save()
            self.stdout.write(f'  ✓  User created: {username}')
        else:
            # Update password for existing user
            user.set_password(password)
            user.save()
            self.stdout.write(f'  ↻  User updated: {username} (password reset)')

        # Ensure in Teacher group, not Student group
        user.groups.add(group)
        student_group = Group.objects.filter(name='Student').first()
        if student_group:
            user.groups.remove(student_group)

        profile, _ = TeacherProfile.objects.get_or_create(user=user)
        profile.assigned_classes = options['classes']
        profile.save()

        self.stdout.write(self.style.SUCCESS(
            f'\n✓ Teacher "{username}" ready.'
        ))
        self.stdout.write(f'  Assigned classes: {options["classes"]}')
        self.stdout.write(f'  Students in scope: {profile.get_students().count()}')