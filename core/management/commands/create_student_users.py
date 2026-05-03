from django.core.management.base import BaseCommand
from django.contrib.auth.models import User, Group
from core.models import Student, StudentProfile

class Command(BaseCommand):
    help = 'Auto-create Django User accounts for all students'
    
    def add_arguments(self, parser):
        parser.add_argument(
            '--default-password', default='changeme123',
            help='Default password for all student accounts'
        )
    
    def handle(self, *args, **options):
        group, _ = Group.objects.get_or_create(name='Student')
        password  = options['default_password']
        created   = 0
        skipped   = 0
        
        for student in Student.objects.all():
            username = student.student_id   # e.g. "220"
            
            if User.objects.filter(username=username).exists():
                skipped += 1
                continue
            
            user = User.objects.create_user(
                username   = username,
                password   = password,
                first_name = student.name,
            )
            user.groups.add(group)
            StudentProfile.objects.create(user=user, student=student)
            created += 1
            self.stdout.write(f"  ✓ Created: {username} ({student.name})")
        
        self.stdout.write(self.style.SUCCESS(
            f"\nDone — {created} created, {skipped} already existed."
        ))
        self.stdout.write(f"Default password: {password}")
        self.stdout.write("Remind students to change password on first login.")