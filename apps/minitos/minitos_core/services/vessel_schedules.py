from datetime import date, datetime, time, timedelta

from django.db import transaction
from django.utils import timezone
from zoneinfo import ZoneInfo

from minitos_core.models import (
    CarrierService,
    CarrierVisit,
    CarrierVisitEquipmentRequirement,
)


WEEKDAYS = {
    'Monday': 0,
    'Tuesday': 1,
    'Wednesday': 2,
    'Thursday': 3,
    'Friday': 4,
    'Saturday': 5,
    'Sunday': 6,
}


def service_occurrence_dates(service, horizon_start, horizon_end):
    start = max(service.start_date, horizon_start)
    end = min(service.end_date or horizon_end, horizon_end)
    if start > end:
        return []
    dates = []
    current = start
    while current <= end:
        elapsed = (current - service.start_date).days
        include = False
        if service.repeat == CarrierService.REPEAT_DAILY:
            include = elapsed % max(service.repeat_daily or 1, 1) == 0
        elif service.repeat == CarrierService.REPEAT_WEEKLY:
            names = [value.strip() for value in (service.on_weekdays or '').split(',') if value.strip()]
            weekday_numbers = {WEEKDAYS[name] for name in names if name in WEEKDAYS}
            if not weekday_numbers and service.window_start_weekday in WEEKDAYS:
                weekday_numbers = {WEEKDAYS[service.window_start_weekday]}
            if not weekday_numbers:
                weekday_numbers = {service.start_date.weekday()}
            include = current.weekday() in weekday_numbers and (elapsed // 7) % max(service.repeat_weekly or 1, 1) == 0
        elif service.repeat == CarrierService.REPEAT_MONTHLY:
            day = service.on_monthly_day or service.start_date.day
            months = (current.year - service.start_date.year) * 12 + current.month - service.start_date.month
            include = current.day == day and months % max(service.repeat_monthly or 1, 1) == 0
        elif service.repeat == CarrierService.REPEAT_YEARLY:
            month = service.repeat_yearly or service.start_date.month
            day = service.on_monthly_day or service.start_date.day
            include = current.month == month and current.day == day
        if include:
            dates.append(current)
        current += timedelta(days=1)
    return dates


def service_window(service, occurrence_date):
    start_time = service.window_start_time or time(0, 0)
    start = datetime.combine(occurrence_date, start_time)
    end_time = service.window_end_time or start_time
    day_offset = 0
    if service.window_end_weekday in WEEKDAYS:
        day_offset = (WEEKDAYS[service.window_end_weekday] - occurrence_date.weekday()) % 7
    end = datetime.combine(occurrence_date + timedelta(days=day_offset), end_time)
    if end <= start:
        end += timedelta(days=1)
    terminal_timezone = ZoneInfo(service.location.timezone or 'UTC')
    start = timezone.make_aware(start, terminal_timezone)
    end = timezone.make_aware(end, terminal_timezone)
    return start, end


@transaction.atomic
def generate_service_visits(service, horizon_start, horizon_end):
    created = []
    for occurrence_date in service_occurrence_dates(service, horizon_start, horizon_end):
        eta, etd = service_window(service, occurrence_date)
        visit_name = f'{service.name}-{occurrence_date:%Y%m%d}'
        visit, was_created = CarrierVisit.objects.get_or_create(
            name=visit_name,
            defaults={
                'visit_type': service.carrier_service_type,
                'location': service.location,
                'carrier_service': service,
                'eta': eta,
                'window_start': eta,
                'window_end': etd,
                'planned_berth': service.default_berth,
                'berth_start_bollard': service.default_start_bollard,
                'berth_end_bollard': service.default_end_bollard,
                'moves_in': service.moves_in,
                'moves_out': service.moves_out,
                'total_moves': service.moves_in + service.moves_out,
                'primary_equipment_type': service.primary_equipment_type,
                'crane_split': service.crane_split,
                'expected_productivity_per_crane': service.expected_productivity_per_crane,
                'horizontal_transport_per_crane': service.horizontal_transport_per_crane,
            },
        )
        if was_created:
            CarrierVisitEquipmentRequirement.objects.bulk_create([
                CarrierVisitEquipmentRequirement(
                    carrier_visit=visit,
                    equipment_type=requirement.equipment_type,
                    equipment_split=requirement.equipment_split,
                    expected_productivity_per_hour=requirement.expected_productivity_per_hour,
                    horizontal_transport_per_unit=requirement.horizontal_transport_per_unit,
                )
                for requirement in service.equipment_requirements.all()
            ])
            created.append(visit)
    return created
