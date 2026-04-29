from django.contrib import admin
from django.utils.html import format_html
from .models import Student, Attendance, AttendanceSettings


@admin.register(Student)
class StudentAdmin(admin.ModelAdmin):
    list_display = ['student_id', 'name', 'student_class', 'face_enrolled', 'qr_generated', 'attendance_percentage']
    list_filter = ['student_class', 'face_enrolled', 'qr_generated']
    search_fields = ['student_id', 'name', 'email']
    readonly_fields = ['attendance_percentage', 'created_at', 'updated_at']

    def attendance_percentage(self, obj):
        pct = obj.attendance_percentage
        color = 'green' if pct >= 75 else 'orange' if pct >= 50 else 'red'
        return format_html('<b style="color:{}">{:.1f}%</b>', color, pct)
    attendance_percentage.short_description = 'Attendance %'


@admin.register(Attendance)
class AttendanceAdmin(admin.ModelAdmin):
    list_display = ['student', 'date', 'time', 'is_late', 'marked_at']
    list_filter = ['date', 'is_late', 'student__student_class']
    search_fields = ['student__name', 'student__student_id']
    date_hierarchy = 'date'
    readonly_fields = ['marked_at', 'is_late']


@admin.register(AttendanceSettings)
class AttendanceSettingsAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return not AttendanceSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False  # Singleton — never delete