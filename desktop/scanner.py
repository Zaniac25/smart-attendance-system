"""
Student Attendance Scanner (Django-backed)
==========================================
QR + Face verification scanner. Marks attendance via the Django REST API.
The desktop is now a thin client — all data lives in Django's database.

Requirements: Django server must be running (python manage.py runserver)

Controls:
  q — quit
  r — print today's count (via API)
"""

import cv2
from pyzbar import pyzbar
import numpy as np
from datetime import datetime
import sys
import os

# Add desktop dir to path so imports work when run directly
sys.path.insert(0, os.path.dirname(__file__))

from api_client import AttendanceAPIClient, ServerNotRunning, APIError

# Face verification uses existing local modules (face data stays on device)
try:
    from face_verifier import verify_face, is_enrolled
    FACE_VERIFICATION_AVAILABLE = True
except ImportError as e:
    print(f"⚠️  face_verifier not available: {e}")
    FACE_VERIFICATION_AVAILABLE = False


class QRScanner:
    """
    QR scanner that marks attendance via the Django API.
    Face verification still runs locally (no need to send images to server).
    """

    def __init__(self, api_base_url: str = "http://127.0.0.1:8000"):
        self.client = AttendanceAPIClient(api_base_url)
        self.camera = None
        self.last_scanned = None
        self.scan_cooldown = 3  # seconds
        self.last_scan_time = 0

    def initialize_camera(self, index: int = 0) -> bool:
        self.camera = cv2.VideoCapture(index)
        if not self.camera.isOpened():
            print(f"❌ Cannot open camera {index}")
            return False
        self.camera.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        self.camera.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
        print("✓ Camera initialized")
        return True

    def parse_qr(self, raw: str) -> dict | None:
        """Parse QR format: StudentID|Name|Class"""
        parts = raw.split('|')
        if len(parts) != 3 or not all(parts):
            return None
        return {'StudentID': parts[0].strip(), 'Name': parts[1].strip(), 'Class': parts[2].strip()}

    def process_scan(self, qr_data: str, frame=None) -> tuple[str, str, tuple]:
        """
        Full scan pipeline:
          1. Parse QR
          2. Face verification (if enrolled + available)
          3. POST to Django API

        Returns: (message, status_key, bgr_color)
        """
        student = self.parse_qr(qr_data)
        if not student:
            return "Invalid QR Code", "error", (0, 0, 200)

        sid = student['StudentID']

        # ── Face verification (local, no API call) ────────────────────────────
        if FACE_VERIFICATION_AVAILABLE and frame is not None:
            if not is_enrolled(sid):
                return f"{student['Name']} — Face Not Enrolled!", "error", (0, 0, 200)

            verified, confidence, msg = verify_face(frame, sid)
            if not verified:
                print(f"❌ Face mismatch for {student['Name']}: {msg}")
                return f"{student['Name']} — Face Mismatch!", "error", (0, 0, 200)

        # ── Mark via API ──────────────────────────────────────────────────────
        try:
            result = self.client.mark_attendance(sid)
        except ServerNotRunning:
            return "Server offline — start Django!", "error", (0, 0, 180)
        except APIError as e:
            return f"API Error: {e}", "error", (0, 0, 180)

        status = result['status']
        message = result['message']
        color = result['color']

        if status == 'success':
            print(f"\n✓ {message} | {datetime.now().strftime('%H:%M:%S')}")
        elif status == 'already_marked':
            print(f"\n⚠ {message}")
        else:
            print(f"\n❌ {message}")

        return message, status, color

    def draw_overlay(self, frame, decoded_obj, message: str, color: tuple):
        """Draw QR bounding box + status text on frame."""
        points = decoded_obj.polygon
        if len(points) == 4:
            pts = np.array([(p.x, p.y) for p in points], dtype=np.int32)
            cv2.polylines(frame, [pts], True, color, 3)

        x, y = decoded_obj.rect.left, decoded_obj.rect.top
        cv2.rectangle(frame, (x, y - 50), (x + 450, y), color, -1)
        cv2.putText(frame, message, (x + 8, y - 14),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255, 255, 255), 2)

    def run(self):
        """Main scanner loop."""
        if not self.initialize_camera():
            return

        # Verify server is up before starting
        print("\n🔗 Checking connection to Django server...")
        if not self.client.check_server():
            print("❌ Django server not reachable at http://127.0.0.1:8000")
            print("   Run: python manage.py runserver")
            return

        print("✓ Connected to Django backend\n")
        print("=" * 60)
        print("QR Code Attendance Scanner — Django Mode")
        print("=" * 60)
        print("📷 Point camera at student QR code")
        print("⌨️  q = quit | r = today's count")
        print("=" * 60 + "\n")

        try:
            while True:
                ret, frame = self.camera.read()
                if not ret:
                    print("❌ Failed to capture frame")
                    break

                decoded = pyzbar.decode(frame)
                for obj in decoded:
                    qr_data = obj.data.decode('utf-8')
                    now = datetime.now().timestamp()

                    # Cooldown to avoid rapid re-scanning
                    if qr_data == self.last_scanned and (now - self.last_scan_time) < self.scan_cooldown:
                        continue

                    message, status, color = self.process_scan(qr_data, frame)
                    self.draw_overlay(frame, obj, message, color)
                    self.last_scanned = qr_data
                    self.last_scan_time = now

                cv2.putText(frame, "Press 'q' to quit", (10, 30),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255, 255, 255), 2)
                cv2.imshow('ABIT Attendance Scanner', frame)

                key = cv2.waitKey(1) & 0xFF
                if key == ord('q'):
                    print("\n👋 Quitting scanner...")
                    break
                elif key == ord('r'):
                    try:
                        report = self.client.get_daily_report()
                        print(f"\n📊 Today: {report['present']}/{report['total_students']} present "
                              f"({report['attendance_percentage']}%)")
                    except Exception as e:
                        print(f"Could not fetch report: {e}")

        finally:
            if self.camera:
                self.camera.release()
            cv2.destroyAllWindows()
            print("✓ Scanner closed")


def main():
    try:
        print("🚀 Starting ABIT Attendance Scanner (Django mode)...\n")
        scanner = QRScanner()
        scanner.run()
    except KeyboardInterrupt:
        print("\n\n⚠️  Interrupted by user")


if __name__ == "__main__":
    main()