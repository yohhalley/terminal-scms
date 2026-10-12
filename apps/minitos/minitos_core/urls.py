from django.urls import path, include, re_path
from django.conf.urls import include
from django.contrib.auth.models import User
from rest_framework import routers, serializers, viewsets
from rest_framework_nested import routers as nested_routers

from rest_framework.urlpatterns import format_suffix_patterns
from . import views

app_name = 'minitos_core'


# urls.py
router = routers.DefaultRouter()

router.register(r'locations', views.LocationViewSet, basename='location')
router.register(r'container-units', views.ContainerUnitViewSet, basename='containerunit')
router.register(r'carrier-services', views.CarrierServiceViewSet, basename='carrier-service')
router.register(r'points-of-work', views.PointOfWorkViewSet, basename='point-of-work')
router.register(r'equipment-types', views.EquipmentTypeViewSet, basename='equipment-type')
router.register(r'component-types', views.ComponentTypeViewSet, basename='component-type')
router.register(r'signal-definitions', views.SignalDefinitionViewSet, basename='signal-definition')
router.register(r'layout-presets', views.LayoutPresetViewSet, basename='layout-preset')
router.register(r'process-hierarchies', views.ProcessHierarchyViewSet, basename='process-hierarchy')
router.register(r'business-processes', views.BusinessProcessViewSet, basename='business-process')
router.register(r'process-versions', views.ProcessVersionViewSet, basename='process-version')
router.register(r'process-executions', views.BusinessProcessExecutionViewSet, basename='process-execution')
## generates:
# /locations/
# /locations/{pk}/

location_router = nested_routers.NestedSimpleRouter(router, r'locations', lookup='location')
location_router.register(r'storage-grids', views.StorageGridViewSet, basename='storage-grid')
location_router.register(r'layout-versions', views.LayoutVersionViewSet, basename='layout-version')
location_router.register(r'layout-assets', views.LayoutAssetViewSet, basename='layout-asset')
location_router.register(r'access-policies', views.AccessPolicyViewSet, basename='access-policy')
location_router.register(r'linear-axes', views.LinearAxisViewSet, basename='linear-axis')
location_router.register(r'berth-segments', views.BerthSegmentViewSet, basename='berth-segment')
location_router.register(r'transfer-interfaces', views.TransferInterfaceViewSet, basename='transfer-interface')
location_router.register(r'operational-positions', views.OperationalPositionViewSet, basename='operational-position')
location_router.register(r'route-nodes', views.RouteNodeViewSet, basename='route-node')
location_router.register(r'route-edges', views.RouteEdgeViewSet, basename='route-edge')
location_router.register(r'che-operating-scopes', views.CheOperatingScopeViewSet, basename='che-operating-scope')

location_router.register(r'ches', views.CheViewSet, basename='che')
che_router = nested_routers.NestedSimpleRouter(location_router, r'ches', lookup='che')
che_router.register(r'components', views.CheComponentViewSet, basename='che-component')
che_router.register(
    r'operational-state-intervals',
    views.OperationalStateIntervalViewSet,
    basename='che-operational-state-interval',
)
che_router.register(r'handling-cycles', views.HandlingCycleViewSet, basename='che-handling-cycle')
che_component_router = nested_routers.NestedSimpleRouter(che_router, r'components', lookup='component')
che_component_router.register(
    r'signal-bindings', views.SignalBindingViewSet, basename='component-signal-binding'
)

equipment_type_router = nested_routers.NestedSimpleRouter(
    router, r'equipment-types', lookup='equipment_type'
)
equipment_type_router.register(
    r'components', views.EquipmentTypeComponentViewSet,
    basename='equipment-type-component',
)

location_router.register(r'work_queues', views.WorkQueueViewSet, basename='work_queue')
work_queue_router = nested_routers.NestedSimpleRouter(location_router, r'work_queues', lookup='work_queue')

location_router.register(r'container_visits', views.ContainerVisitViewSet, basename='container_visit')
container_visit_router = nested_routers.NestedSimpleRouter(location_router, r'container_visits', lookup='container_visit')

location_router.register(r'carrier-visits', views.CarrierVisitViewSet, basename='carrier-visit')
location_router.register(r'carrier-services', views.CarrierServiceViewSet, basename='carrier-service')
carrier_service_router = nested_routers.NestedSimpleRouter(location_router, r'carrier-services', lookup='carrier_service')
carrier_service_router.register(r'service-cargo-routes', views.ServiceCargoRouteViewSet, basename='service-cargo-route')

carrier_visit_router = nested_routers.NestedSimpleRouter(location_router, r'carrier-visits', lookup='carrier_visit')
carrier_visit_router.register(r'visit-cargo-routes', views.VisitCargoRouteViewSet, basename='visit-cargo-route')
carrier_visit_router.register(r'pile-bookings', views.PileBookingViewSet, basename='pile-booking')

#location_router.register(r'container_visits', views.ContainerVisitViewSet, basename='container_visit')
#container_visit_router = nested_routers.NestedSimpleRouter(location_router, r'container_visits', lookup='container_visit')

## generates:
# /locations/{location_pk}/blocks/
# /locations/{location_pk}/blocks/{block_pk}/
# /locations/{location_pk}/ches/{che_pk}/
# /locations/{location_pk}/work_queues/{work_queue_pk}/

storage_grid_router = nested_routers.NestedSimpleRouter(
    location_router, r'storage-grids', lookup='storage_grid'
)
storage_grid_router.register(r'piles', views.PileViewSet, basename='pile')
## generates:
# /locations/{location_pk}/blocks/{block_pk}/piles/
# /locations/{location_pk}/blocks/{block_pk}/piles/{pk}/

pile_router = nested_routers.NestedSimpleRouter(storage_grid_router, r'piles', lookup='pile')
pile_router.register(r'positions', views.PositionViewSet, basename='position')
## generates:
# /locations/{location_pk}/blocks/{block_pk}/piles/{pile_pk}/positions/
# /locations/{location_pk}/blocks/{block_pk}/piles/{pile_pk}//positions/{pk}


work_queue_router.register(r'orders', views.OrderViewSet, basename='order')
## generates:
# /locations/{location_pk}/work_queues/{work_queue_pk}//orders/
# /locations/{location_pk}/work_queues/{work_queue_pk}//orders/{order_pk}/

order_router = nested_routers.NestedSimpleRouter(work_queue_router, r'orders', lookup='order')
order_router.register(r'order_legs', views.OrderLegViewSet, basename='order-leg')
## generates:
# /work_queues/{lwork_queue_pk}/orders/{order_pk}/order_legs/
# /work_queues/{work_queue_pk}/orders/{order_pk}/order_legs/{pk}/

urlpatterns = [
    re_path(r'^', include(router.urls)),
    re_path(r'^', include(location_router.urls)),
    re_path(r'^', include(storage_grid_router.urls)),
    re_path(r'^', include(che_router.urls), name='index'),
    re_path(r'^', include(che_router.urls), name='partial_update'),
    re_path(r'^', include(che_component_router.urls)),
    re_path(r'^', include(equipment_type_router.urls)),
    re_path(r'^', include(pile_router.urls), name='index'),
    re_path(r'^', include(pile_router.urls), name='partial_update'),
    re_path(r'^', include(work_queue_router.urls)),
    re_path(r'^', include(order_router.urls)),
    re_path(r'^', include(carrier_service_router.urls)),
    re_path(r'^', include(carrier_visit_router.urls)),

    # Visit API endpoints
    path('api/bulk-create-container-visits/', views.bulk_create_container_visits, name='bulk_create_container_visits'),
    path('api/visits/<int:visit_id>/transition-status/', views.transition_visit_status, name='transition_visit_status'),
    path('api/container-visits/<int:container_id>/transition-status/', views.transition_container_status, name='transition_container_status'),
    path('api/visits/<int:visit_id>/status-summary/', views.visit_status_summary, name='visit_status_summary'),

    # Terminal Editor API endpoints
    path('api/location/<int:location_id>/', views.location_api_detail, name='location_api_detail'),
    path('api/location/', views.location_api_save, name='location_api_create'),
    path('api/location/<int:location_id>/save/', views.location_api_save, name='location_api_save'),
]

# urlpatterns = [
#
#   url(r'^', include(router.urls)),
#   url(r'^api-auth/', include('rest_framework.urls', namespace='rest_framework'))]
