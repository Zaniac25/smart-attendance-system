from django.core.management.base import BaseCommand
from django.contrib.auth.models import User, Group
from core.models import TeacherProfile
import json

class Command(BaseCommand):
    help = 'Create a teacher account with assigned classes'
    
    def add_arguments(self, parser):
        parser.add_argument('username')
        parser.add_argument('password')
        parser.add_argument('--classes', nargs='+', required=True,
                            help='Class strings e.g. "Btech CSE Sec A" "BCS AI Sec B"')
    
    def handle(self, *args, **options):
        group, _ = Group.objects.get_or_create(name='Teacher')
        
        user, created = User.objects.get_or_create(username=options['username'])
        if created:
            user.set_password(options['password'])
            user.save()
        
        user.groups.add(group)
        
        profile, _ = TeacherProfile.objects.get_or_create(user=user)
        profile.assigned_classes = options['classes']
        profile.save()
        
        self.stdout.write(self.style.SUCCESS(
            f"✓ Teacher '{user.username}' assigned to: {options['classes']}"
        ))