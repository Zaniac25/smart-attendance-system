"""
Views — Complete
================
All views for the ABIT Attendance System:
  - Auth (login/logout)
  - Dashboard, Reports, Settings
  - Browser-based scanner (process frames via POST)
  - Student CRUD + CSV bulk import
  - QR code generation + download
  - REST API for desktop scanner fallback
"""

import os
import io
import csv
import json
import zipfile
import numpy as np
from io import BytesIO
from datetime import date, datetime

import qrcode
import pandas as pd

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.utils.decorators import method_decorator
from django.utils import timezone
from django.views import View
from django.http import JsonResponse, HttpResponse
from django.conf import settings

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import AllowAny

from .models import Student, Attendance, AttendanceSettings
from .serializers import StudentSerializer, MarkAttendanceSerializer
from .analytics import (
    get_daily_report, get_weekly_trend,
    get_classwise_report, get_student_trend, get_dashboard_stats,
)
from .notifications import send_daily_absent_report

# Optional libs
try:
    from pyzbar import pyzbar
    PYZBAR_AVAILABLE = True
except ImportError:
    PYZBAR_AVAILABLE = False

try:
    import face_recognition
    import pickle
    FACE_RECOGNITION_AVAILABLE = True
except ImportError:
    FACE_RECOGNITION_AVAILABLE = False


# ── Helpers ────────────────────────────────────────────────────────────────────

def _generate_qr_bytes(student):
    data = f"{student.student_id}|{student.name}|{student.student_class}"
    qr = qrcode.QRCode(version=1, error_correction=qrcode.constants.ERROR_CORRECT_H,
                       box_size=10, border=4)
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = BytesIO()
    img.save(buf, format='PNG')
    return buf.getvalue()


def _decode_qr_from_bytes(image_bytes):
    if not PYZBAR_AVAILABLE:
        return []
    import cv2
    nparr = np.frombuffer(image_bytes, np.uint8)
    frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    if frame is None:
        return []
    return [obj.data.decode('utf-8') for obj in pyzbar.decode(frame)]


def _verify_face(image_bytes, student_id, strict=False):
    """
    Verify face in image_bytes against stored encoding.

    Args:
        strict: True during live scanner (tighter tolerance = 0.42).
                False during enrollment preview (looser = 0.5).

    Returns (verified: bool, message: str)

    Tolerance guide (face_recognition library):
        0.6 = liberal  — too many false positives
        0.5 = default  — reasonable for controlled environments
        0.42 = strict  — required for scanner to reject photo spoofing
    """
    if not FACE_RECOGNITION_AVAILABLE:
        return True, "Face verification unavailable — skipped"

    for path in [
        os.path.join(settings.BASE_DIR, 'face_encodings.pkl'),
        os.path.join(settings.BASE_DIR, 'desktop', 'face_encodings.pkl'),
    ]:
        if os.path.exists(path):
            encodings_path = path
            break
    else:
        return True, "No encodings file — skipped"

    with open(encodings_path, 'rb') as f:
        encodings = pickle.load(f)

    if student_id not in encodings:
        return False, "Face not enrolled"

    stored = encodings[student_id]['encoding']

    import cv2
    nparr = np.frombuffer(image_bytes, np.uint8)
    frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    locations = face_recognition.face_locations(rgb, model='hog')

    if not locations:
        return False, "No face detected"

    # Strict mode for live scanner — rejects most photo spoofing attempts
    tolerance = 0.42 if strict else 0.50

    for enc in face_recognition.face_encodings(rgb, locations):
        distance = face_recognition.face_distance([stored], enc)[0]
        confidence = round((1 - distance) * 100, 1)
        if distance <= tolerance:
            return True, f"Verified ({confidence}% match)"

    return False, "Face does not match"


# ═══════════════════════════════════════════════════════════════════════════════
# AUTH
# ═══════════════════════════════════════════════════════════════════════════════

class LoginView(View):
    def get(self, request):
        if request.user.is_authenticated:
            return redirect('dashboard')
        return render(request, 'dashboard/login.html')

    def post(self, request):
        user = authenticate(request,
                            username=request.POST.get('username', '').strip(),
                            password=request.POST.get('password', '').strip())
        if user:
            login(request, user)
            return redirect('dashboard')
        return render(request, 'dashboard/login.html', {'error': 'Invalid credentials'})


class LogoutView(View):
    def get(self, request):
        logout(request)
        return redirect('login')


# ═══════════════════════════════════════════════════════════════════════════════
# DASHBOARD
# ═══════════════════════════════════════════════════════════════════════════════

@method_decorator(login_required, name='dispatch')
class DashboardView(View):
    def get(self, request):
        today = timezone.localdate()
        context = {
            'stats': get_dashboard_stats(),
            'class_report': get_classwise_report(today),
            'today': today,
            **{k: json.dumps(v) for k, v in [
                ('trend_labels',  get_weekly_trend(7)['labels']),
                ('trend_present', get_weekly_trend(7)['present']),
                ('trend_absent',  get_weekly_trend(7)['absent']),
            ]},
        }
        return render(request, 'dashboard/index.html', context)


# ═══════════════════════════════════════════════════════════════════════════════
# BROWSER SCANNER
# ═══════════════════════════════════════════════════════════════════════════════

@method_decorator(login_required, name='dispatch')
class ScannerPageView(View):
    def get(self, request):
        return render(request, 'dashboard/scanner.html')


@method_decorator(login_required, name='dispatch')
class ProcessFrameView(View):
    """
    POST /scanner/process-frame/
    Two modes selected by the 'mode' POST field:

    mode=qr_only
        Decode QR from frame. Do NOT mark attendance.
        Returns: qr_detected | already_marked | unknown_student | no_qr | invalid_qr

    mode=face_and_mark
        Verify face for student_id. If verified, mark attendance.
        Returns: success | face_mismatch | face_not_enrolled | no_face | already_marked
    """
    def post(self, request):
        mode = request.POST.get('mode', 'qr_only')
        frame_file = request.FILES.get('frame')
        if not frame_file:
            return JsonResponse({'status': 'no_frame'}, status=400)

        image_bytes = frame_file.read()

        # ── Mode 1: QR decode only ────────────────────────────────────────────
        if mode == 'qr_only':
            qr_results = _decode_qr_from_bytes(image_bytes)
            if not qr_results:
                return JsonResponse({'status': 'no_qr'})

            parts = qr_results[0].split('|')
            if len(parts) != 3:
                return JsonResponse({'status': 'invalid_qr'})

            student_id    = parts[0].strip()
            student_name  = parts[1].strip()
            student_class = parts[2].strip()

            try:
                student = Student.objects.get(student_id=student_id)
            except Student.DoesNotExist:
                return JsonResponse({'status': 'unknown_student',
                                     'message': f'Student {student_id} not in database'})

            # Check duplicate before moving to face phase
            if Attendance.objects.filter(student=student, date=timezone.localdate()).exists():
                return JsonResponse({
                    'status': 'already_marked',
                    'student_name': student.name,
                    'student_class': student.student_class,
                })

            return JsonResponse({
                'status': 'qr_detected',
                'student_id': student.student_id,
                'student_name': student.name,
                'student_class': student.student_class,
            })

        # ── Mode 2: Face verify + mark ────────────────────────────────────────
        if mode == 'face_and_mark':
            student_id = request.POST.get('student_id', '').strip()
            if not student_id:
                return JsonResponse({'status': 'error', 'message': 'student_id required'}, status=400)

            try:
                student = Student.objects.get(student_id=student_id)
            except Student.DoesNotExist:
                return JsonResponse({'status': 'unknown_student'})

            # Double-check duplicate (race condition guard)
            if Attendance.objects.filter(student=student, date=timezone.localdate()).exists():
                return JsonResponse({'status': 'already_marked',
                                     'student_name': student.name,
                                     'student_class': student.student_class})

            # Face verification
            if FACE_RECOGNITION_AVAILABLE:
                # Check enrollment first
                encodings_path = None
                for path in [
                    os.path.join(settings.BASE_DIR, 'face_encodings.pkl'),
                    os.path.join(settings.BASE_DIR, 'desktop', 'face_encodings.pkl'),
                ]:
                    if os.path.exists(path):
                        encodings_path = path
                        break

                if encodings_path:
                    with open(encodings_path, 'rb') as f:
                        encodings = pickle.load(f)
                    if student_id not in encodings:
                        return JsonResponse({
                            'status': 'face_not_enrolled',
                            'student_name': student.name,
                            'student_class': student.student_class,
                        })

                verified, face_msg = _verify_face(image_bytes, student_id, strict=True)

                # No face detected — tell frontend to keep trying
                if not verified and 'No face' in face_msg:
                    return JsonResponse({'status': 'no_face'})

                if not verified:
                    return JsonResponse({
                        'status': 'face_mismatch',
                        'student_name': student.name,
                        'student_class': student.student_class,
                        'message': face_msg,
                    })

            # Mark attendance
            now = datetime.now()
            record = Attendance.objects.create(
                student=student,
                date=now.date(),
                time=now.time(),
            )
            return JsonResponse({
                'status': 'success',
                'student_name': student.name,
                'student_class': student.student_class,
                'student_id': student_id,
                'time': now.strftime('%I:%M %p'),
                'is_late': record.is_late,
            })

        return JsonResponse({'status': 'error', 'message': 'Invalid mode'}, status=400)


# ═══════════════════════════════════════════════════════════════════════════════
# REPORTS
# ═══════════════════════════════════════════════════════════════════════════════

@method_decorator(login_required, name='dispatch')
class ReportsView(View):
    def get(self, request):
        date_str = request.GET.get('date', timezone.localdate().isoformat())
        try:
            target_date = date.fromisoformat(date_str)
        except ValueError:
            target_date = timezone.localdate()

        trend = get_weekly_trend(days=14)
        context = {
            'report': get_daily_report(target_date),
            'class_report': get_classwise_report(target_date),
            'date_str': date_str,
            'trend_labels': json.dumps(trend['labels']),
            'trend_present': json.dumps(trend['present']),
        }
        return render(request, 'dashboard/reports.html', context)


@method_decorator(login_required, name='dispatch')
class ExportExcelView(View):
    def get(self, request):
        date_str = request.GET.get('date', timezone.localdate().isoformat())
        try:
            target_date = date.fromisoformat(date_str)
        except ValueError:
            target_date = timezone.localdate()

        report = get_daily_report(target_date)
        output = BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            pd.DataFrame([{
                'Date': str(report['date']), 'Total': report['total_students'],
                'Present': report['present'], 'Absent': report['absent'],
                'Late': report['late'], 'Attendance %': f"{report['attendance_percentage']}%",
            }]).to_excel(writer, sheet_name='Summary', index=False)

            if report['present_students']:
                pd.DataFrame([{
                    'ID': r.student.student_id, 'Name': r.student.name,
                    'Class': r.student.student_class, 'Time': str(r.time),
                    'Late': 'Yes' if r.is_late else 'No',
                } for r in report['present_students']]).to_excel(writer, sheet_name='Present', index=False)

            if report['absent_students']:
                pd.DataFrame([{
                    'ID': s.student_id, 'Name': s.name, 'Class': s.student_class,
                } for s in report['absent_students']]).to_excel(writer, sheet_name='Absent', index=False)

            pd.DataFrame(get_classwise_report(target_date)).to_excel(
                writer, sheet_name='Class-wise', index=False)

        output.seek(0)
        response = HttpResponse(output.read(),
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        response['Content-Disposition'] = f'attachment; filename="attendance_{date_str}.xlsx"'
        return response


@method_decorator(login_required, name='dispatch')
class SendNotificationView(View):
    def post(self, request):
        date_str = request.POST.get('date', timezone.localdate().isoformat())
        try:
            target_date = date.fromisoformat(date_str)
        except ValueError:
            target_date = timezone.localdate()
        return JsonResponse({'success': send_daily_absent_report(target_date)})


# ═══════════════════════════════════════════════════════════════════════════════
# STUDENTS CRUD
# ═══════════════════════════════════════════════════════════════════════════════

@method_decorator(login_required, name='dispatch')
class StudentsView(View):
    def get(self, request):
        qs = Student.objects.all()
        search = request.GET.get('q', '').strip()
        class_filter = request.GET.get('class', '').strip()
        if search:
            qs = qs.filter(name__icontains=search) | qs.filter(student_id__icontains=search)
        if class_filter:
            qs = qs.filter(student_class=class_filter)
        return render(request, 'dashboard/students.html', {
            'students': qs.order_by('student_class', 'name'),
            'search': search, 'class_filter': class_filter,
            'all_classes': Student.objects.values_list('student_class', flat=True).distinct().order_by('student_class'),
            'total': qs.count(),
        })


@method_decorator(login_required, name='dispatch')
class StudentAddView(View):
    def get(self, request):
        return render(request, 'dashboard/student_form.html', {'action': 'Add'})

    def post(self, request):
        sid = request.POST.get('student_id', '').strip()
        name = request.POST.get('name', '').strip()
        cls = request.POST.get('student_class', '').strip()
        email = request.POST.get('email', '').strip()
        errors = {}
        if not sid:
            errors['student_id'] = 'Required.'
        elif Student.objects.filter(student_id=sid).exists():
            errors['student_id'] = f'ID "{sid}" already exists.'
        if not name:
            errors['name'] = 'Required.'
        if not cls:
            errors['student_class'] = 'Required.'
        if errors:
            return render(request, 'dashboard/student_form.html',
                          {'action': 'Add', 'errors': errors, 'data': request.POST})
        Student.objects.create(student_id=sid, name=name, student_class=cls, email=email or None)
        return redirect('students')


@method_decorator(login_required, name='dispatch')
class StudentEditView(View):
    def get(self, request, student_id):
        s = get_object_or_404(Student, student_id=student_id)
        return render(request, 'dashboard/student_form.html', {
            'action': 'Edit', 'student': s,
            'data': {'student_id': s.student_id, 'name': s.name,
                     'student_class': s.student_class, 'email': s.email or ''},
        })

    def post(self, request, student_id):
        s = get_object_or_404(Student, student_id=student_id)
        name = request.POST.get('name', '').strip()
        cls = request.POST.get('student_class', '').strip()
        email = request.POST.get('email', '').strip()
        errors = {}
        if not name:
            errors['name'] = 'Required.'
        if not cls:
            errors['student_class'] = 'Required.'
        if errors:
            return render(request, 'dashboard/student_form.html',
                          {'action': 'Edit', 'student': s, 'errors': errors, 'data': request.POST})
        s.name, s.student_class, s.email = name, cls, email or None
        s.save()
        return redirect('student_detail', student_id=student_id)


@method_decorator(login_required, name='dispatch')
class StudentDeleteView(View):
    def post(self, request, student_id):
        get_object_or_404(Student, student_id=student_id).delete()
        return redirect('students')


@method_decorator(login_required, name='dispatch')
class StudentDetailView(View):
    def get(self, request, student_id):
        student = get_object_or_404(Student, student_id=student_id)
        trend = get_student_trend(student_id, days=30)
        return render(request, 'dashboard/student_detail.html', {
            'student': student,
            'trend': trend,
            'recent_records': Attendance.objects.filter(student=student).order_by('-date')[:10],
            'statuses_json': json.dumps(trend.get('statuses', [])),
            'labels_json': json.dumps(trend.get('labels', [])),
        })


@method_decorator(login_required, name='dispatch')
class StudentImportView(View):
    def get(self, request):
        return render(request, 'dashboard/student_import.html')

    def post(self, request):
        csv_file = request.FILES.get('csv_file')
        if not csv_file or not csv_file.name.endswith('.csv'):
            return render(request, 'dashboard/student_import.html',
                          {'error': 'Please upload a valid .csv file.'})

        reader = csv.DictReader(io.StringIO(csv_file.read().decode('utf-8')))
        if not {'StudentID', 'Name', 'Class'}.issubset(set(reader.fieldnames or [])):
            return render(request, 'dashboard/student_import.html',
                          {'error': 'CSV must have headers: StudentID, Name, Class'})

        created, updated, skipped, row_errors = 0, 0, 0, []
        for i, row in enumerate(reader, 2):
            sid = row.get('StudentID', '').strip()
            name = row.get('Name', '').strip()
            cls = row.get('Class', '').strip()
            email = row.get('Email', '').strip() or None
            if not all([sid, name, cls]):
                row_errors.append(f"Row {i}: incomplete — skipped")
                skipped += 1
                continue
            _, was_created = Student.objects.update_or_create(
                student_id=sid, defaults={'name': name, 'student_class': cls, 'email': email})
            created += 1 if was_created else 0
            updated += 0 if was_created else 1

        return render(request, 'dashboard/student_import.html', {
            'result': {'created': created, 'updated': updated,
                       'skipped': skipped, 'errors': row_errors}
        })


# ═══════════════════════════════════════════════════════════════════════════════
# QR GENERATION
# ═══════════════════════════════════════════════════════════════════════════════

@method_decorator(login_required, name='dispatch')
class QRGenerateView(View):
    def get(self, request):
        students = Student.objects.all().order_by('student_class', 'name')

        # Build dict of {student_id: face_url} for enrolled students
        faces_dir = os.path.join(settings.BASE_DIR, 'media', 'student_faces')
        face_photos = {}
        for student in students:
            face_path = os.path.join(faces_dir, f'{student.student_id}.jpg')
            if os.path.exists(face_path):
                face_photos[student.student_id] = f'/media/student_faces/{student.student_id}.jpg'

        return render(request, 'dashboard/qr_generate.html', {
            'students': students,
            'face_photos': face_photos,
        })

    def post(self, request):
        generate_all = request.POST.get('generate_all') == '1'
        selected_ids = request.POST.getlist('student_ids')
        qs = Student.objects.all() if generate_all else Student.objects.filter(student_id__in=selected_ids)
        count = 0
        for student in qs:
            student.qr_generated = True
            student.save(update_fields=['qr_generated'])
            count += 1
        return JsonResponse({'success': True, 'count': count})


@method_decorator(login_required, name='dispatch')
class QRDownloadView(View):
    def get(self, request, student_id):
        student = get_object_or_404(Student, student_id=student_id)
        png_bytes = _generate_qr_bytes(student)
        student.qr_generated = True
        student.save(update_fields=['qr_generated'])
        safe_name = student.name.replace(' ', '_')
        response = HttpResponse(png_bytes, content_type='image/png')
        response['Content-Disposition'] = f'attachment; filename="QR_{student_id}_{safe_name}.png"'
        return response


@method_decorator(login_required, name='dispatch')
class QRPreviewView(View):
    """Serves QR code as inline image (for use in <img> tags, no download prompt)."""
    def get(self, request, student_id):
        student = get_object_or_404(Student, student_id=student_id)
        png_bytes = _generate_qr_bytes(student)
        return HttpResponse(png_bytes, content_type='image/png')


@method_decorator(login_required, name='dispatch')
class QRDownloadAllView(View):
    def get(self, request):
        zip_buf = BytesIO()
        with zipfile.ZipFile(zip_buf, 'w', zipfile.ZIP_DEFLATED) as zf:
            for student in Student.objects.all():
                safe_name = student.name.replace(' ', '_')
                zf.writestr(f"QR_{student.student_id}_{safe_name}.png", _generate_qr_bytes(student))
                student.qr_generated = True
                student.save(update_fields=['qr_generated'])
        zip_buf.seek(0)
        response = HttpResponse(zip_buf.read(), content_type='application/zip')
        response['Content-Disposition'] = 'attachment; filename="all_qr_codes.zip"'
        return response


@method_decorator(login_required, name='dispatch')
class IDCardDownloadView(View):
    """
    Generates a printable ID card PNG combining:
    - Student face photo (left)
    - QR code (right)
    - Name, Class, ID below
    Uses Pillow — no extra dependencies needed.
    """
    def get(self, request, student_id):
        from PIL import Image, ImageDraw, ImageFont
        import textwrap

        student = get_object_or_404(Student, student_id=student_id)

        # ── Card dimensions ────────────────────────────────────────────────────
        CARD_W, CARD_H = 800, 320
        PADDING        = 24
        FACE_SIZE      = 220   # square face crop
        QR_SIZE        = 220   # QR code size
        BG_COLOR       = (255, 255, 255)
        PRIMARY        = (30,  58,  95)   # dark blue
        TEXT_DARK      = (31,  41,  55)
        TEXT_GRAY      = (107, 114, 128)
        ACCENT         = (232, 160, 32)   # yellow

        card = Image.new('RGB', (CARD_W, CARD_H), BG_COLOR)
        draw = ImageDraw.Draw(card)

        # ── Header bar ────────────────────────────────────────────────────────
        draw.rectangle([0, 0, CARD_W, 48], fill=PRIMARY)

        # Try to load a font; fall back to default if not available
        try:
            font_title  = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', 15)
            font_name   = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', 18)
            font_detail = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', 13)
            font_id     = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf', 13)
            font_small  = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', 11)
        except Exception:
            font_title = font_name = font_detail = font_id = font_small = ImageFont.load_default()

        draw.text((PADDING, 14), "ABIT — Student Attendance Card", font=font_title, fill=(255, 255, 255))

        # ── Face photo ────────────────────────────────────────────────────────
        face_x, face_y = PADDING, 60
        faces_dir = os.path.join(settings.BASE_DIR, 'media', 'student_faces')
        face_path = os.path.join(faces_dir, f'{student_id}.jpg')

        if os.path.exists(face_path):
            face_img = Image.open(face_path).convert('RGB')
            face_img = face_img.resize((FACE_SIZE, FACE_SIZE), Image.LANCZOS)
            # Rounded border effect — draw a rect behind
            draw.rectangle(
                [face_x - 3, face_y - 3, face_x + FACE_SIZE + 3, face_y + FACE_SIZE + 3],
                outline=ACCENT, width=3
            )
            card.paste(face_img, (face_x, face_y))
        else:
            # Placeholder if no face enrolled
            draw.rectangle([face_x, face_y, face_x + FACE_SIZE, face_y + FACE_SIZE],
                           fill=(241, 245, 249), outline=(203, 213, 225), width=2)
            draw.text((face_x + 55, face_y + 90), "No Face\nEnrolled",
                      font=font_detail, fill=TEXT_GRAY, align='center')

        # ── QR code ───────────────────────────────────────────────────────────
        qr_bytes = _generate_qr_bytes(student)
        qr_img   = Image.open(BytesIO(qr_bytes)).convert('RGB')
        qr_img   = qr_img.resize((QR_SIZE, QR_SIZE), Image.LANCZOS)
        qr_x     = CARD_W - PADDING - QR_SIZE
        qr_y     = 60
        draw.rectangle(
            [qr_x - 3, qr_y - 3, qr_x + QR_SIZE + 3, qr_y + QR_SIZE + 3],
            outline=(203, 213, 225), width=2
        )
        card.paste(qr_img, (qr_x, qr_y))

        # ── Student info (centre column) ──────────────────────────────────────
        info_x = face_x + FACE_SIZE + PADDING
        info_w = qr_x - info_x - PADDING

        # Name (wrap if long)
        name_lines = textwrap.wrap(student.name, width=18)
        y_cursor = 70
        for line in name_lines[:2]:
            draw.text((info_x, y_cursor), line, font=font_name, fill=TEXT_DARK)
            y_cursor += 26

        y_cursor += 6
        draw.text((info_x, y_cursor), student.student_class, font=font_detail, fill=TEXT_GRAY)
        y_cursor += 22

        # ID pill
        id_text = f"ID: {student.student_id}"
        draw.rounded_rectangle(
            [info_x, y_cursor, info_x + 130, y_cursor + 24],
            radius=6, fill=(239, 246, 255)
        )
        draw.text((info_x + 8, y_cursor + 4), id_text, font=font_id, fill=PRIMARY)
        y_cursor += 36

        # Face status
        face_enrolled = os.path.exists(face_path)
        face_label = "✓ Face Enrolled" if face_enrolled else "✗ Face Not Enrolled"
        face_color = (22, 163, 74) if face_enrolled else (220, 38, 38)
        draw.text((info_x, y_cursor), face_label, font=font_small, fill=face_color)
        y_cursor += 18

        # Scan to mark attendance hint
        draw.text((info_x, y_cursor + 6), "Scan QR to mark attendance",
                  font=font_small, fill=TEXT_GRAY)

        # ── Bottom bar ────────────────────────────────────────────────────────
        draw.rectangle([0, CARD_H - 28, CARD_W, CARD_H], fill=(248, 250, 252))
        draw.line([0, CARD_H - 28, CARD_W, CARD_H - 28], fill=(226, 232, 240), width=1)
        draw.text((PADDING, CARD_H - 20),
                  "Ajay Binay Institute of Technology — Attendance System",
                  font=font_small, fill=TEXT_GRAY)

        # ── Serve as PNG download ─────────────────────────────────────────────
        out_buf = BytesIO()
        card.save(out_buf, format='PNG', dpi=(150, 150))
        out_buf.seek(0)

        safe_name = student.name.replace(' ', '_')
        response = HttpResponse(out_buf.read(), content_type='image/png')
        response['Content-Disposition'] = f'attachment; filename="IDCard_{student_id}_{safe_name}.png"'

        # Mark QR as generated
        student.qr_generated = True
        student.save(update_fields=['qr_generated'])
        return response


@method_decorator(login_required, name='dispatch')
class IDCardDownloadAllView(View):
    """Download all student ID cards as a ZIP."""
    def get(self, request):
        from PIL import Image
        zip_buf = BytesIO()
        with zipfile.ZipFile(zip_buf, 'w', zipfile.ZIP_DEFLATED) as zf:
            for student in Student.objects.all():
                # Reuse single card view logic by calling its get() internally
                view = IDCardDownloadView()
                resp = view.get(request, student.student_id)
                safe_name = student.name.replace(' ', '_')
                zf.writestr(f"IDCard_{student.student_id}_{safe_name}.png", resp.content)
        zip_buf.seek(0)
        response = HttpResponse(zip_buf.read(), content_type='application/zip')
        response['Content-Disposition'] = 'attachment; filename="all_id_cards.zip"'
        return response


# ═══════════════════════════════════════════════════════════════════════════════
# SETTINGS
# ═══════════════════════════════════════════════════════════════════════════════

@method_decorator(login_required, name='dispatch')
class SettingsView(View):
    def get(self, request):
        return render(request, 'dashboard/settings.html', {'config': AttendanceSettings.get()})

    def post(self, request):
        config = AttendanceSettings.get()

        # Parse cutoff time from hidden field (format: HH:MM sent by JS)
        raw_cutoff = request.POST.get('late_cutoff_time', '').strip()
        if raw_cutoff:
            try:
                from datetime import time as dt_time
                parts = raw_cutoff.split(':')
                h, m = int(parts[0]), int(parts[1]) if len(parts) > 1 else 0
                # Clamp to valid range
                h = max(0, min(23, h))
                m = max(0, min(59, m))
                config.late_cutoff_time = dt_time(h, m)
            except (ValueError, IndexError):
                pass  # Keep existing value if parsing fails

        config.notification_email = request.POST.get('notification_email', '')
        config.notify_on_absent = request.POST.get('notify_on_absent') == 'on'
        config.save()

        from datetime import time as dt_time
        saved_display = config.late_cutoff_time.strftime('%I:%M %p')
        return render(request, 'dashboard/settings.html', {
            'config': config,
            'success': f'Settings saved! Late cutoff: {saved_display}'
        })


# ═══════════════════════════════════════════════════════════════════════════════
# FACE ENROLLMENT
# ═══════════════════════════════════════════════════════════════════════════════

def _get_encodings_path():
    """Find face_encodings.pkl — check root then desktop/ subfolder."""
    for path in [
        os.path.join(settings.BASE_DIR, 'face_encodings.pkl'),
        os.path.join(settings.BASE_DIR, 'desktop', 'face_encodings.pkl'),
    ]:
        if os.path.exists(path):
            return path
    # Default write location
    return os.path.join(settings.BASE_DIR, 'face_encodings.pkl')


def _load_encodings():
    path = _get_encodings_path()
    if os.path.exists(path):
        with open(path, 'rb') as f:
            return pickle.load(f)
    return {}


def _save_encodings(encodings):
    path = os.path.join(settings.BASE_DIR, 'face_encodings.pkl')
    with open(path, 'wb') as f:
        pickle.dump(encodings, f)


@method_decorator(login_required, name='dispatch')
class FaceEnrollView(View):
    """
    Dashboard page listing all students with their enrollment status.
    Each student has an Enroll / Re-enroll button.
    Enrollment itself happens via webcam capture on the same page (no terminal needed).
    """
    def get(self, request):
        students = Student.objects.all().order_by('student_class', 'name')
        enrolled_ids = set()
        if FACE_RECOGNITION_AVAILABLE:
            encodings = _load_encodings()
            enrolled_ids = set(encodings.keys())
        return render(request, 'dashboard/face_enroll.html', {
            'students': students,
            'enrolled_ids': enrolled_ids,
            'face_available': FACE_RECOGNITION_AVAILABLE,
        })


@method_decorator(login_required, name='dispatch')
class FaceEnrollStudentView(View):
    """
    POST /face/enroll/<student_id>/
    Accepts two input types:
      1. 'frame' file  — JPEG from live webcam capture
      2. 'photo' file  — uploaded image file (JPG/PNG) for absent students
    Extracts face encoding, saves to face_encodings.pkl, marks face_enrolled=True.
    """
    def post(self, request, student_id):
        if not FACE_RECOGNITION_AVAILABLE:
            return JsonResponse({'success': False, 'message': 'face_recognition library not installed'})

        student = get_object_or_404(Student, student_id=student_id)

        # Accept either webcam frame or uploaded photo
        image_file = request.FILES.get('frame') or request.FILES.get('photo')
        if not image_file:
            return JsonResponse({'success': False, 'message': 'No image received'})

        # Validate file type for photo uploads
        if request.FILES.get('photo'):
            allowed = ['image/jpeg', 'image/jpg', 'image/png', 'image/webp']
            if image_file.content_type not in allowed:
                return JsonResponse({'success': False,
                                     'message': 'Only JPG, PNG, or WEBP images are accepted'})
            if image_file.size > 10 * 1024 * 1024:
                return JsonResponse({'success': False, 'message': 'Image too large — max 10MB'})

        image_bytes = image_file.read()

        import cv2
        nparr = np.frombuffer(image_bytes, np.uint8)
        frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if frame is None:
            return JsonResponse({'success': False, 'message': 'Could not read image — try a different file'})

        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        locations = face_recognition.face_locations(rgb, model='hog')

        if not locations:
            return JsonResponse({
                'success': False,
                'message': 'No face detected. Ensure the face is clear, well-lit, and not obscured.'
            })

        if len(locations) > 1:
            return JsonResponse({
                'success': False,
                'message': f'{len(locations)} faces detected. Use a photo with only one person.'
            })

        encodings_list = face_recognition.face_encodings(rgb, locations)
        if not encodings_list:
            return JsonResponse({'success': False,
                                 'message': 'Could not encode face — try a clearer, better-lit photo'})

        encoding = encodings_list[0]

        # ── Save cropped face image to media/student_faces/<id>.jpg ──────────
        top, right, bottom, left = locations[0]
        # Add 30% padding around detected face box
        h_img, w_img = frame.shape[:2]
        pad_y = int((bottom - top) * 0.3)
        pad_x = int((right - left) * 0.3)
        y1 = max(0, top - pad_y)
        y2 = min(h_img, bottom + pad_y)
        x1 = max(0, left - pad_x)
        x2 = min(w_img, right + pad_x)
        face_crop = frame[y1:y2, x1:x2]

        # Resize to a standard 300×300 thumbnail
        face_thumb = cv2.resize(face_crop, (300, 300), interpolation=cv2.INTER_AREA)

        faces_dir = os.path.join(settings.BASE_DIR, 'media', 'student_faces')
        os.makedirs(faces_dir, exist_ok=True)
        face_path = os.path.join(faces_dir, f'{student_id}.jpg')
        cv2.imwrite(face_path, face_thumb)

        # ── Save encoding ────────────────────────────────────────────────────
        encodings = _load_encodings()
        encodings[student_id] = {'name': student.name, 'encoding': encoding}
        _save_encodings(encodings)

        student.face_enrolled = True
        student.save(update_fields=['face_enrolled'])

        source = 'photo' if request.FILES.get('photo') else 'webcam'
        return JsonResponse({
            'success': True,
            'message': f'{student.name} enrolled successfully via {source}',
            'student_name': student.name,
            'source': source,
            'face_url': f'/media/student_faces/{student_id}.jpg',
        })


# ═══════════════════════════════════════════════════════════════════════════════
# REST API
# ═══════════════════════════════════════════════════════════════════════════════

class APIMarkAttendance(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = MarkAttendanceSerializer(data=request.data)
        if serializer.is_valid():
            record = serializer.save()
            return Response({'status': 'success', 'student_name': record.student.name,
                             'is_late': record.is_late}, status=status.HTTP_201_CREATED)
        errors = serializer.errors
        for err in errors.get('non_field_errors', []):
            if isinstance(err, dict) and err.get('detail') == 'already_marked':
                return Response({'status': 'already_marked',
                                 'student_name': err.get('student_name', '')})
        return Response({'status': 'error', 'errors': errors}, status=400)


class APIStudentList(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        return Response(StudentSerializer(Student.objects.all(), many=True).data)


class APIStudentDetail(APIView):
    permission_classes = [AllowAny]

    def get(self, request, student_id):
        return Response(StudentSerializer(get_object_or_404(Student, student_id=student_id)).data)


class APIDailyReport(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        date_str = request.query_params.get('date', timezone.localdate().isoformat())
        try:
            target_date = date.fromisoformat(date_str)
        except ValueError:
            return Response({'error': 'Use YYYY-MM-DD format'}, status=400)
        r = get_daily_report(target_date)
        return Response({'date': str(r['date']), 'total_students': r['total_students'],
                         'present': r['present'], 'absent': r['absent'],
                         'late': r['late'], 'attendance_percentage': r['attendance_percentage']})


class APITodayStatus(APIView):
    permission_classes = [AllowAny]

    def get(self, request, student_id):
        marked = Attendance.objects.filter(
            student__student_id=student_id, date=timezone.localdate()).exists()
        return Response({'student_id': student_id, 'marked_today': marked})