
from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import CarrierVisit, VisitCargoRoute, ServiceCargoRoute

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


