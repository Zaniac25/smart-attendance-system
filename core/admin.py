from django.contrib import admin
from django.utils.html import format_html
from .models import *
from django.utils import timezone as tz



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
    


@admin.register(TeacherProfile)
class TeacherProfileAdmin(admin.ModelAdmin):
    list_display  = ['user', 'display_classes']
    search_fields = ['user__username']
 
    def display_classes(self, obj):
        return ', '.join(obj.assigned_classes) if obj.assigned_classes else '—'
    display_classes.short_description = 'Assigned Classes'
 
 
@admin.register(StudentProfile)
class StudentProfileAdmin(admin.ModelAdmin):
    list_display  = ['user', 'student']
    search_fields = ['user__username', 'student__name', 'student__student_id']
    raw_id_fields = ['student']
 
 
@admin.register(ChangeRequest)
class ChangeRequestAdmin(admin.ModelAdmin):
    list_display = ['requested_by', 'student_id', 'request_type', 'status', 'created_at', 'resolved_at']
    list_filter = ['status', 'request_type']
    search_fields  = ['student_id', 'requested_by__username', 'description']
    readonly_fields = ['requested_by', 'student_id', 'request_type', 'description',
                       'date_affected', 'created_at']
    actions        = ['approve_selected', 'reject_selected']
 
    def approve_selected(self, request, queryset):
        queryset.filter(status='pending').update(status='approved', resolved_at=tz.now())
        self.message_user(request, f'{queryset.count()} request(s) approved.')
    approve_selected.short_description = 'Approve selected requests'
 
    def reject_selected(self, request, queryset):
        queryset.filter(status='pending').update(status='rejected', resolved_at=tz.now())
        self.message_user(request, f'{queryset.count()} request(s) rejected.')
    reject_selected.short_description = 'Reject selected requests'


@admin.register(AcademicSession)
class AcademicSessionAdmin(admin.ModelAdmin):
    list_display  = ['name', 'course', 'start_date', 'end_date', 'is_active', 'working_days_count', 'holiday_count']
    list_filter   = ['course', 'is_active']
    search_fields = ['course', 'name']
    list_editable = ['is_active']
    ordering      = ['-start_date']
 
    def working_days_count(self, obj):
        return obj.get_working_days()
    working_days_count.short_description = 'Working Days (to date)'
 
    def holiday_count(self, obj):
        return obj.holidays.count()
    holiday_count.short_description = 'Holidays'
 
 
@admin.register(Holiday)
class HolidayAdmin(admin.ModelAdmin):
    list_display  = ['date', 'name', 'day_of_week', 'session']
    list_filter   = ['session']
    search_fields = ['name']
    ordering      = ['date']
    date_hierarchy = 'date'
 
    def day_of_week(self, obj):
        return obj.date.strftime('%A')
    day_of_week.short_description = 'Day'
 
 
@admin.register(Timetable)
class TimetableAdmin(admin.ModelAdmin):
    list_display  = ['teacher', 'session', 'has_file_display', 'has_grid_display', 'updated_at']
    list_filter   = ['session']
    search_fields = ['teacher__user__username']
    readonly_fields = ['updated_at']
 
    def has_file_display(self, obj):
        return format_html('<span style="color:green">✓</span>') if obj.has_file else '—'
    has_file_display.short_description = 'File'
 
    def has_grid_display(self, obj):
        return format_html('<span style="color:green">✓</span>') if obj.has_grid else '—'
    has_grid_display.short_description = 'Grid'