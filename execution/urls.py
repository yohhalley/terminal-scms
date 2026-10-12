from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import EquipmentCommandViewSet, EquipmentStateViewSet, ExecutionOrderViewSet, health

router = DefaultRouter()
router.register("orders", ExecutionOrderViewSet, basename="execution-order")
router.register("equipment", EquipmentStateViewSet, basename="equipment-state")
router.register("commands", EquipmentCommandViewSet, basename="equipment-command")

urlpatterns = [
    path("health/", health, name="health"),
    path("api/", include(router.urls)),
]
