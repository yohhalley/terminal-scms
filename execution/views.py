from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import connection
from django.http import JsonResponse
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

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

