from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import connection
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from .forms import EquipmentStateOverrideForm
from .models import EquipmentCommand, EquipmentState, ExecutionOrder
from .serializers import (
    CommandEventSerializer,
    DispatchRequestSerializer,
    EquipmentCommandSerializer,
    EquipmentStateSerializer,
    ExecutionOrderSerializer,
)
from .services.dispatch import apply_command_event, dispatch_next_operation


def _service_error(exc):
    if hasattr(exc, "message_dict"):
        return exc.message_dict
    return {"detail": exc.messages if hasattr(exc, "messages") else [str(exc)]}


@api_view(["GET"])
@permission_classes([AllowAny])
def health(request):
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
        cursor.fetchone()
    return JsonResponse({"status": "ok", "service": "terminal-oes"})


@login_required
def equipment_console(request):
    equipment_queryset = EquipmentState.objects.order_by("terminal_code", "name")
    terminal_id = request.GET.get("terminal_id", "").strip()
    if terminal_id:
        equipment_queryset = equipment_queryset.filter(terminal_id=terminal_id)

    selected_id = request.POST.get("equipment_id") or request.GET.get("equipment")
    selected = None
    if selected_id:
        selected = get_object_or_404(equipment_queryset, pk=selected_id)
    elif equipment_queryset.exists():
        selected = equipment_queryset.first()

    if request.method == "POST":
        if selected is None:
            messages.error(request, "Select equipment before applying an override.")
            return redirect("equipment-console")
        form = EquipmentStateOverrideForm(request.POST, instance=selected)
        if form.is_valid():
            equipment = form.save_override(request.user)
            messages.success(request, f"{equipment.name} state updated.")
            return redirect(f"{request.path}?equipment={equipment.pk}")
    else:
        form = EquipmentStateOverrideForm(instance=selected) if selected else None

    stale_before = timezone.now() - timedelta(seconds=settings.EQUIPMENT_STALE_SECONDS)
    equipment = list(equipment_queryset)
    map_data = [
        {
            "id": item.pk,
            "name": item.name,
            "type": item.equipment_type_code or "CHE",
            "latitude": item.latitude,
            "longitude": item.longitude,
            "rotation": item.rotation,
            "powered_on": item.powered_on,
            "working": item.working,
            "available": item.is_available,
            "stale": item.last_seen_at < stale_before,
        }
        for item in equipment
        if item.latitude is not None and item.longitude is not None
    ]
    terminal_options = EquipmentState.objects.values(
        "terminal_id", "terminal_code"
    ).order_by("terminal_code").distinct()
    return render(request, "execution/equipment_console.html", {
        "equipment": equipment,
        "selected": selected,
        "form": form,
        "map_data": map_data,
        "stale_before": stale_before,
        "terminal_id": terminal_id,
        "terminal_options": terminal_options,
    })


class ReadOnlyModelViewSet(viewsets.ReadOnlyModelViewSet):
    pass


class ExecutionOrderViewSet(ReadOnlyModelViewSet):
    serializer_class = ExecutionOrderSerializer
    queryset = ExecutionOrder.objects.prefetch_related(
        "operations__assignment__equipment", "operations__assignment__commands",
    )
    filterset_fields = ()

    def get_queryset(self):
        queryset = super().get_queryset()
        terminal_id = self.request.query_params.get("terminal_id")
        status_value = self.request.query_params.get("status")
        if terminal_id:
            queryset = queryset.filter(terminal_id=terminal_id)
        if status_value:
            queryset = queryset.filter(status=status_value)
        return queryset

    @action(detail=True, methods=["post"], url_path="dispatch-next")
    def dispatch_next(self, request, pk=None):
        serializer = DispatchRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            assignment = dispatch_next_operation(
                self.get_object().pk,
                equipment_id=serializer.validated_data.get("equipment_id"),
            )
        except DjangoValidationError as exc:
            raise ValidationError(_service_error(exc)) from exc
        return Response(
            ExecutionOrderSerializer(
                assignment.operation.order,
                context=self.get_serializer_context(),
            ).data,
            status=status.HTTP_201_CREATED,
        )


class EquipmentStateViewSet(ReadOnlyModelViewSet):
    serializer_class = EquipmentStateSerializer
    queryset = EquipmentState.objects.all()

    def get_queryset(self):
        queryset = super().get_queryset()
        terminal_id = self.request.query_params.get("terminal_id")
        if terminal_id:
            queryset = queryset.filter(terminal_id=terminal_id)
        return queryset


class EquipmentCommandViewSet(
    mixins.RetrieveModelMixin,
    mixins.ListModelMixin,
    viewsets.GenericViewSet,
):
    serializer_class = EquipmentCommandSerializer
    queryset = EquipmentCommand.objects.select_related(
        "assignment__operation__order", "assignment__equipment",
    )

    @action(detail=True, methods=["post"], url_path="event")
    def event(self, request, pk=None):
        serializer = CommandEventSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            command = apply_command_event(
                self.get_object().pk,
                serializer.validated_data["event"],
                position_id=serializer.validated_data.get("position_id"),
                reason=serializer.validated_data.get("reason", ""),
            )
        except DjangoValidationError as exc:
            raise ValidationError(_service_error(exc)) from exc
        return Response(self.get_serializer(command).data)
