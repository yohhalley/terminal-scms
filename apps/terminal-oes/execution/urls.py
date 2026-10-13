from django.urls import include, path
from django.contrib.auth import views as auth_views
from rest_framework.routers import DefaultRouter

from .views import (
    EquipmentCommandViewSet,
    EquipmentStateViewSet,
    ExecutionOrderViewSet,
    equipment_console,
    health,
)

router = DefaultRouter()
router.register("orders", ExecutionOrderViewSet, basename="execution-order")
router.register("equipment", EquipmentStateViewSet, basename="equipment-state")
router.register("commands", EquipmentCommandViewSet, basename="equipment-command")

urlpatterns = [
    path("", equipment_console, name="equipment-console"),
    path(
        "login/",
        auth_views.LoginView.as_view(template_name="registration/login.html"),
        name="login",
    ),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("equipment/", equipment_console, name="equipment-console-list"),
    path("health/", health, name="health"),
    path("api/", include(router.urls)),
]
