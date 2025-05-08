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
## generates:
# /locations/
# /locations/{pk}/

location_router = nested_routers.NestedSimpleRouter(router, r'locations', lookup='location')
location_router.register(r'blocks', views.BlockViewSet, basename='block')

location_router.register(r'ches', views.CheViewSet, basename='che')
che_router = nested_routers.NestedSimpleRouter(location_router, r'ches', lookup='che')

location_router.register(r'work_queues', views.WorkQueueViewSet, basename='work_queue')
work_queue_router = nested_routers.NestedSimpleRouter(location_router, r'work_queues', lookup='work_queue')

location_router.register(r'container_visits', views.ContainerVisitViewSet, basename='container_visit')
container_visit_router = nested_routers.NestedSimpleRouter(location_router, r'container_visits', lookup='container_visit')

#location_router.register(r'container_visits', views.ContainerVisitViewSet, basename='container_visit')
#container_visit_router = nested_routers.NestedSimpleRouter(location_router, r'container_visits', lookup='container_visit')

## generates:
# /locations/{location_pk}/blocks/
# /locations/{location_pk}/blocks/{block_pk}/
# /locations/{location_pk}/ches/{che_pk}/
# /locations/{location_pk}/work_queues/{work_queue_pk}/

block_router = nested_routers.NestedSimpleRouter(location_router, r'blocks', lookup='block')
block_router.register(r'piles', views.PileViewSet, basename='pile')
## generates:
# /locations/{location_pk}/blocks/{block_pk}/piles/
# /locations/{location_pk}/blocks/{block_pk}/piles/{pk}/

pile_router = nested_routers.NestedSimpleRouter(block_router, r'piles', lookup='pile')
pile_router.register(r'positions', views.PositionViewSet, basename='position')
## generates:
# /locations/{location_pk}/blocks/{block_pk}/piles/{pile_pk}/positions/
# /locations/{location_pk}/blocks/{block_pk}/piles/{pile_pk}//positions/{pk}


work_queue_router.register(r'orders', views.OrderViewSet, basename='order')
## generates:
# /locations/{location_pk}/work_queues/{work_queue_pk}//orders/
# /locations/{location_pk}/work_queues/{work_queue_pk}//orders/{order_pk}/

order_router = nested_routers.NestedSimpleRouter(work_queue_router, r'orders', lookup='order')
order_router.register(r'order_legs', views.PileViewSet, basename='pile')
## generates:
# /work_queues/{lwork_queue_pk}/orders/{order_pk}/order_legs/
# /work_queues/{work_queue_pk}/orders/{order_pk}/order_legs/{pk}/

urlpatterns = [
    re_path(r'^', include(router.urls)),
    re_path(r'^', include(location_router.urls)),
    re_path(r'^', include(block_router.urls)),
    re_path(r'^', include(che_router.urls), name='index'),
    re_path(r'^', include(che_router.urls), name='partial_update'),
    re_path(r'^', include(pile_router.urls), name='index'),
    re_path(r'^', include(pile_router.urls), name='partial_update'),
    re_path(r'^', include(work_queue_router.urls)),
    re_path(r'^', include(order_router.urls)),
]

# urlpatterns = [
#   
#   url(r'^', include(router.urls)),
#   url(r'^api-auth/', include('rest_framework.urls', namespace='rest_framework'))]