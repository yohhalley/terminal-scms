
from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import ContainerOwner, ContainerUnit, ISOType, Che, WorkQueue, Order, OrderLeg, Block, Pile, Position, CarrierVisit, VisitCargoRoute, ServiceCargoRoute


@receiver(post_save, sender=Block)
def create_block(sender, instance, created, **kwargs):
    if created:
        #Block.objects.create(instance)
        max_rows = instance.max_rows
        max_bays = instance.max_bays
        max_tier = instance.max_tier
        x_start_pos = instance.x_position
        y_start_pos = instance.y_position
        x_offset = 0.35
        y_offset = 1.25
        
        for bay in range(1, max_bays+1):
            for row in range(1,max_rows+1):
                pile = Pile.objects.create(block=instance, bay=bay, row=row, allows_in=True, allows_out=True,is_restricted=False, x_position = x_start_pos + bay*x_offset, y_position = y_start_pos + row*y_offset)
                for tier in range(1, max_tier+1):
                    Position.objects.create(pile=pile, is_reserved=False, is_occupied=False, tier=tier)
   




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
    
    