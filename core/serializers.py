"""
Serializers: Django models ↔ JSON for the REST API consumed by the desktop scanner.
"""

from rest_framework import serializers
from .models import Student, Attendance
from django.utils import timezone
import datetime


class StudentSerializer(serializers.ModelSerializer):
    attendance_percentage = serializers.ReadOnlyField()

    class Meta:
        model = Student
        fields = [
            'id', 'student_id', 'name', 'student_class',
            'email', 'face_enrolled', 'qr_generated',
            'attendance_percentage', 'created_at',
        ]
        read_only_fields = ['id', 'created_at', 'attendance_percentage']


class AttendanceSerializer(serializers.ModelSerializer):
    student_name = serializers.CharField(source='student.name', read_only=True)
    student_class = serializers.CharField(source='student.student_class', read_only=True)

    class Meta:
        model = Attendance
        fields = [
            'id', 'student', 'student_name', 'student_class',
            'date', 'time', 'is_late', 'marked_at',
        ]
        read_only_fields = ['id', 'is_late', 'marked_at', 'student_name', 'student_class']


class MarkAttendanceSerializer(serializers.Serializer):
    """
    Used by the desktop scanner to POST attendance.
    Accepts student_id (string) rather than FK — matches QR data format.
    """
    student_id = serializers.CharField(max_length=20)
    date = serializers.DateField(required=False)
    time = serializers.TimeField(required=False)

    def validate_student_id(self, value):
        try:
            Student.objects.get(student_id=value)
        except Student.DoesNotExist:
            raise serializers.ValidationError(f"Student '{value}' not found in database.")
        return value

    def validate(self, data):
        # Default to current date/time if not provided (normal scanner flow)
        now = timezone.localtime(timezone.now())
        data.setdefault('date', now.date())
        data.setdefault('time', now.time())

        # Check for duplicate before hitting DB unique_together constraint
        student = Student.objects.get(student_id=data['student_id'])
        if Attendance.objects.filter(student=student, date=data['date']).exists():
            raise serializers.ValidationError({
                'detail': 'already_marked',
                'student_name': student.name,
            })
        return data

    def create(self, validated_data):
        student = Student.objects.get(student_id=validated_data['student_id'])
        record = Attendance.objects.create(
            student=student,
            date=validated_data['date'],
            time=validated_data['time'],
        )
        return record


class DailyReportSerializer(serializers.Serializer):
    """Read-only serializer for structured daily report response."""
    date = serializers.DateField()
    total_students = serializers.IntegerField()
    present = serializers.IntegerField()
    absent = serializers.IntegerField()
    late = serializers.IntegerField()
    attendance_percentage = serializers.FloatField()
    present_students = AttendanceSerializer(many=True)
    absent_students = StudentSerializer(many=True)