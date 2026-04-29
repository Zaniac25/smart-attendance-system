"""
API Client
==========
All communication between the desktop app and Django backend lives here.
This isolation means if the API changes, you touch only this file.

Usage:
    from api_client import AttendanceAPIClient
    client = AttendanceAPIClient()
    result = client.mark_attendance("220")
"""

import requests
from requests.exceptions import ConnectionError, Timeout, RequestException


BASE_URL = "http://127.0.0.1:8000"
TIMEOUT = 5  # seconds


class APIError(Exception):
    """Raised when the API returns an unexpected error."""
    pass


class ServerNotRunning(Exception):
    """Raised when Django server is not reachable."""
    pass


class AttendanceAPIClient:
    """
    Thin HTTP client wrapping the Django REST API.
    All methods return plain dicts — no Django/requests objects leak out.
    """

    def __init__(self, base_url: str = BASE_URL):
        self.base_url = base_url.rstrip('/')
        self.session = requests.Session()
        self.session.headers.update({'Content-Type': 'application/json'})

    def _get(self, endpoint: str, params: dict = None) -> dict:
        try:
            resp = self.session.get(
                f"{self.base_url}{endpoint}",
                params=params,
                timeout=TIMEOUT
            )
            resp.raise_for_status()
            return resp.json()
        except ConnectionError:
            raise ServerNotRunning(
                "Django server is not running. Start it with: python manage.py runserver"
            )
        except Timeout:
            raise APIError("Request timed out. Is the server under load?")
        except RequestException as e:
            raise APIError(f"Request failed: {e}")

    def _post(self, endpoint: str, data: dict) -> dict:
        try:
            resp = self.session.post(
                f"{self.base_url}{endpoint}",
                json=data,
                timeout=TIMEOUT
            )
            # Don't raise on 400 — we handle those (e.g. already_marked)
            return resp.json(), resp.status_code
        except ConnectionError:
            raise ServerNotRunning(
                "Django server is not running. Start it with: python manage.py runserver"
            )
        except Timeout:
            raise APIError("Request timed out.")
        except RequestException as e:
            raise APIError(f"Request failed: {e}")

    # ── Public API ─────────────────────────────────────────────────────────────

    def check_server(self) -> bool:
        """Returns True if Django server is reachable."""
        try:
            self._get('/api/students/')
            return True
        except (ServerNotRunning, APIError):
            return False

    def mark_attendance(self, student_id: str) -> dict:
        """
        POST attendance for a student.

        Returns:
            dict with keys:
                - status: 'success' | 'already_marked' | 'error'
                - student_name: str
                - is_late: bool (only on success)
                - message: str (human-readable)
        """
        data, code = self._post('/api/attendance/mark/', {'student_id': student_id})

        if data.get('status') == 'success':
            late_tag = " [LATE]" if data.get('is_late') else ""
            return {
                'status': 'success',
                'student_name': data['student_name'],
                'student_class': data.get('student_class', ''),
                'is_late': data.get('is_late', False),
                'message': f"{data['student_name']} — Attendance Marked!{late_tag}",
                'color': (0, 200, 100) if not data.get('is_late') else (30, 180, 220),
            }

        elif data.get('status') == 'already_marked':
            return {
                'status': 'already_marked',
                'student_name': data.get('student_name', ''),
                'message': f"{data.get('student_name', 'Student')} — Already Marked Today",
                'color': (0, 165, 255),
            }

        else:
            errors = data.get('errors', data)
            return {
                'status': 'error',
                'student_name': '',
                'message': f"Error: {errors}",
                'color': (0, 0, 220),
            }

    def is_marked_today(self, student_id: str) -> bool:
        """Quick check — has student been marked today?"""
        try:
            data = self._get(f'/api/attendance/today/{student_id}/')
            return data.get('marked_today', False)
        except (APIError, ServerNotRunning):
            return False  # Fail open — let mark_attendance handle the duplicate

    def get_student(self, student_id: str) -> dict | None:
        """Fetch student details. Returns None if not found."""
        try:
            return self._get(f'/api/students/{student_id}/')
        except Exception:
            return None

    def get_daily_report(self, date_str: str = None) -> dict:
        """
        Fetch today's (or specified date's) summary report.
        Args:
            date_str: 'YYYY-MM-DD', defaults to today on the server side.
        """
        params = {}
        if date_str:
            params['date'] = date_str
        return self._get('/api/reports/daily/', params=params)