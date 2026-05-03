from django.shortcuts import redirect
from .roles import get_role

ROLE_HOME = {
    'admin':   '/dashboard/',
    'teacher': '/teacher/dashboard/',
    'student': '/student/dashboard/',
}

class RoleRedirectMiddleware:
    """After login, redirect to role-appropriate home."""
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)