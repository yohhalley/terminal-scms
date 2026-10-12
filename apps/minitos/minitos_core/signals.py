
from django.contrib.auth.models import User
from django.db.models import Sum
from django.db.models.signals import post_delete, post_migrate, post_save, pre_save
from django.dispatch import receiver

from .models import CarrierVisit, Che, EquipmentType, VisitCargoRoute, ServiceCargoRoute


def _recalculate_service_moves(service_ids):
    from .models import CarrierService

    for service in CarrierService.objects.filter(pk__in=service_ids):
        moves_in = ServiceCargoRoute.objects.filter(
            inbound_carrier_service=service
        ).aggregate(total=Sum('container_count'))['total'] or 0
        moves_out = ServiceCargoRoute.objects.filter(
            outbound_carrier_service=service
        ).aggregate(total=Sum('container_count'))['total'] or 0
        CarrierService.objects.filter(pk=service.pk).update(moves_in=moves_in, moves_out=moves_out)


@receiver(pre_save, sender=ServiceCargoRoute)
def remember_previous_route_services(sender, instance, **kwargs):
    if not instance.pk:
        instance._previous_service_ids = set()
        return
    previous = sender.objects.filter(pk=instance.pk).values(
        'inbound_carrier_service_id', 'outbound_carrier_service_id'
    ).first()
    instance._previous_service_ids = set(previous.values()) if previous else set()


@receiver(post_save, sender=ServiceCargoRoute)
def update_service_moves_for_saved_route(sender, instance, **kwargs):
    service_ids = getattr(instance, '_previous_service_ids', set())
    service_ids.update([instance.inbound_carrier_service_id, instance.outbound_carrier_service_id])
    _recalculate_service_moves(service_ids)


@receiver(post_delete, sender=ServiceCargoRoute)
def update_service_moves_for_deleted_route(sender, instance, **kwargs):
    _recalculate_service_moves({instance.inbound_carrier_service_id, instance.outbound_carrier_service_id})

@receiver(post_save, sender=CarrierVisit)
def create_visit_cargo_routes(sender, instance, created, **kwargs):
    if created and instance.carrier_service:
        # Get all ServiceCargoRoutes where this carrier service is the inbound service
        service_routes = ServiceCargoRoute.objects.filter(inbound_carrier_service=instance.carrier_service)

        for service_route in service_routes:
            VisitCargoRoute.objects.create(
                inbound_carrier_visit=instance,
                inbound_carrier_service=instance.carrier_service,
                outbound_carrier_visit=None,  # Will be set later when outbound visit is created
                outbound_carrier_service=service_route.outbound_carrier_service,
                pod=service_route.pod,
                container_type=service_route.container_type,
                iso_type=service_route.iso_type,
                hazard_codes=service_route.hazard_codes,
                is_reefer=service_route.is_reefer,
                container_count=service_route.container_count,
                expected_dwell_time_days=service_route.expected_dwell_time_days
            )


@receiver(post_save, sender=EquipmentType)
def materialize_equipment_type_components(sender, instance, **kwargs):
    from .services.che_components import sync_equipment_type_components

    sync_equipment_type_components(instance)


@receiver(post_save, sender=Che)
def materialize_che_components_and_state(sender, instance, **kwargs):
    from .services.che_components import (
        apply_pending_che_component_values,
        sync_che_components,
        sync_che_summary_signals,
        transition_operational_state,
    )

    sync_che_components(instance)
    sync_che_summary_signals(instance)
    apply_pending_che_component_values(instance)
    transition_operational_state(instance)


@receiver(post_migrate)
def ensure_che_signal_catalog(sender, app_config, **kwargs):
    if app_config.label != 'minitos_core':
        return
    from .services.che_components import ensure_component_catalog, ensure_signal_catalog

    component_types = ensure_component_catalog()
    ensure_signal_catalog(component_types)
