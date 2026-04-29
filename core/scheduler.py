"""
Scheduler Module
================
Uses APScheduler to fire the daily absent notification email.
Started automatically when Django boots via AppConfig.ready().

Place in core/apps.py — scheduler starts in a background thread.
"""

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from django.conf import settings
import logging

logger = logging.getLogger(__name__)

_scheduler = None


def send_daily_report_job():
    """Wrapper so APScheduler can import this without circular imports."""
    from core.notifications import send_daily_absent_report
    success = send_daily_absent_report()
    if success:
        logger.info("Scheduled daily report sent successfully.")
    else:
        logger.warning("Scheduled daily report failed or was skipped.")


def start():
    """
    Start the background scheduler.
    Called once from CoreConfig.ready() in apps.py.

    Schedule: Every day at 18:00 (6 PM IST) — configurable below.
    """
    global _scheduler

    if _scheduler and _scheduler.running:
        return  # Prevent duplicate scheduler in Django's dev auto-reloader

    _scheduler = BackgroundScheduler(timezone=settings.TIME_ZONE)

    _scheduler.add_job(
        send_daily_report_job,
        trigger=CronTrigger(hour=18, minute=0),  # 6:00 PM daily
        id='daily_absent_report',
        replace_existing=True,
        misfire_grace_time=300,  # Allow up to 5 min delay before skipping
    )

    _scheduler.start()
    logger.info("APScheduler started — daily report scheduled at 18:00.")


def stop():
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("APScheduler stopped.")