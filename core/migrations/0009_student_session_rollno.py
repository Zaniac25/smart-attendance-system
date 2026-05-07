"""
Migration 0009 — Student gets:
  - session FK (the active session this student belongs to)
  - roll_number field (structured: YEAR-COURSE-BRANCH-SEC-NUM, e.g. 22-BT-CSE-A-001)
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0008_student_admission_year_academicsession_timetable_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='student',
            name='session',
            field=models.ForeignKey(
                null=True, blank=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='students',
                to='core.academicsession',
                help_text='Academic session this student is enrolled in',
            ),
        ),
        migrations.AddField(
            model_name='student',
            name='roll_number',
            field=models.CharField(
                max_length=30, blank=True, default='',
                db_index=True,
                help_text='Structured roll/regd no — e.g. 22BTCSEA001. Auto-parsed to fill admission_year.',
            ),
        ),
    ]