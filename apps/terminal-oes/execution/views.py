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
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from .forms import EquipmentStateOverrideForm
from .models import (
    EnterpriseProjection, EquipmentCommand, EquipmentState, ExecutionOrder,
    SimulationSession, SiteProjection,
)
from .permissions import HasRunnerToken
from .serializers import (
    CommandEventSerializer,
    DispatchRequestSerializer,
    EquipmentCommandSerializer,
    EquipmentStateSerializer,
    EnterpriseProjectionSerializer,
    ExecutionOrderSerializer,
    RunnerClaimSerializer,
    RunnerStatusSerializer,
    SimulationSessionSerializer,
    StartSimulationSerializer,
    SiteProjectionSerializer,
)
from .services.dispatch import (
    DISPATCH_EQUIPMENT_INVALID,
    DISPATCH_IN_FLIGHT_CODES,
    DISPATCH_INVALID_REQUEST,
    DISPATCH_SIMULATION_NOT_RUNNING,
    SIMULATION_NOT_RUNNING_MESSAGE,
    apply_command_event,
    dispatch_next_operation,
    live_leg_session_running,
    order_has_live_leg,
)
from .services.simulations import (
    bootstrap_document,
    claim_next_session,
    launch_environment,
    record_session_heartbeat,
    report_runner_status,
    start_simulation,
    stop_simulation,
)


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
    equipment_queryset = EquipmentState.objects.select_related(
        "site__enterprise", "simulation_session",
    ).order_by("site__enterprise__code", "site__code", "name")
    site_id = request.GET.get("site_id", "").strip()
    if site_id:
        equipment_queryset = equipment_queryset.filter(site_id=site_id)

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
            "stale": (
                not item.device_connected
                or not item.data_valid
                or item.last_seen_at < stale_before
            ),
        }
        for item in equipment
        if item.latitude is not None and item.longitude is not None
    ]
    site_options = SiteProjection.objects.filter(
        equipment__isnull=False,
    ).select_related("enterprise").distinct().order_by("enterprise__code", "code")
    return render(request, "execution/equipment_console.html", {
        "equipment": equipment,
        "selected": selected,
        "form": form,
        "map_data": map_data,
        "stale_before": stale_before,
        "site_id": site_id,
        "site_options": site_options,
    })


@login_required
def simulation_console(request):
    if request.method == "POST":
        action_name = request.POST.get("action")
        try:
            if action_name == "start":
                session, created = start_simulation(
                    request.POST.get("site_id"),
                    simulator_version=request.POST.get("simulator_version") or "local",
                    time_scale=float(request.POST.get("time_scale") or 1.0),
                )
                messages.success(
                    request,
                    f"Simulation {session.pk} {'created' if created else 'already active'}.",
                )
            elif action_name == "stop":
                session = stop_simulation(request.POST.get("session_id"))
                messages.success(request, f"Stop requested for simulation {session.pk}.")
        except (DjangoValidationError, ValueError) as exc:
            messages.error(request, "; ".join(_service_error(exc).get("detail", [])))
        return redirect("simulation-console")

    sites = SiteProjection.objects.select_related("enterprise").prefetch_related(
        "simulation_sessions",
    )
    sessions = SimulationSession.objects.select_related(
        "site__enterprise",
    ).order_by("-created_at")[:50]
    return render(request, "execution/simulation_console.html", {
        "sites": sites,
        "sessions": sessions,
    })


class ReadOnlyModelViewSet(viewsets.ReadOnlyModelViewSet):
    pass


class EnterpriseProjectionViewSet(ReadOnlyModelViewSet):
    serializer_class = EnterpriseProjectionSerializer
    queryset = EnterpriseProjection.objects.all()


class SiteProjectionViewSet(ReadOnlyModelViewSet):
    serializer_class = SiteProjectionSerializer
    queryset = SiteProjection.objects.select_related("enterprise")

    def get_queryset(self):
        queryset = super().get_queryset()
        status_value = self.request.query_params.get("status")
        if status_value:
            queryset = queryset.filter(projection_status=status_value)
        return queryset

    @action(
        detail=True, methods=["post"], url_path="start-simulation",
        url_name="start-simulation",
    )
    def start_simulation_action(self, request, pk=None):
        serializer = StartSimulationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            session, created = start_simulation(
                self.get_object().pk,
                **serializer.validated_data,
            )
        except DjangoValidationError as exc:
            raise ValidationError(_service_error(exc)) from exc
        return Response(
            SimulationSessionSerializer(session).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class SimulationSessionViewSet(ReadOnlyModelViewSet):
    serializer_class = SimulationSessionSerializer
    queryset = SimulationSession.objects.select_related("site__enterprise")

    def get_permissions(self):
        if self.action in {"claim", "runner_status"}:
            return [HasRunnerToken()]
        if self.action in {"bootstrap", "heartbeat"}:
            return [AllowAny()]
        return super().get_permissions()

    @action(detail=False, methods=["post"], url_path="claim")
    def claim(self, request):
        serializer = RunnerClaimSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        session, token = claim_next_session(serializer.validated_data["runner_identity"])
        if session is None:
            return Response(status=status.HTTP_204_NO_CONTENT)
        return Response({
            "session": self.get_serializer(session).data,
            "environment": launch_environment(session, token),
        })

    @action(detail=True, methods=["post"], url_path="stop")
    def stop(self, request, pk=None):
        session = stop_simulation(self.get_object().pk)
        return Response(self.get_serializer(session).data)

    @action(detail=True, methods=["get"], url_path="bootstrap")
    def bootstrap(self, request, pk=None):
        try:
            document = bootstrap_document(
                self.get_object(), request.headers.get("X-Simulation-Token", ""),
            )
        except DjangoValidationError as exc:
            raise PermissionDenied(str(exc)) from exc
        return Response(document)

    @action(detail=True, methods=["post"], url_path="heartbeat")
    def heartbeat(self, request, pk=None):
        try:
            session = record_session_heartbeat(
                self.get_object(), request.headers.get("X-Simulation-Token", ""),
            )
        except DjangoValidationError as exc:
            raise PermissionDenied(str(exc)) from exc
        return Response(self.get_serializer(session).data)

    @action(detail=True, methods=["post"], url_path="runner-status")
    def runner_status(self, request, pk=None):
        serializer = RunnerStatusSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            session = report_runner_status(
                self.get_object().pk,
                serializer.validated_data["runner_identity"],
                serializer.validated_data["status"],
                serializer.validated_data.get("failure_reason", ""),
            )
        except DjangoValidationError as exc:
            raise ValidationError(_service_error(exc)) from exc
        return Response(self.get_serializer(session).data)


class ExecutionOrderViewSet(ReadOnlyModelViewSet):
    serializer_class = ExecutionOrderSerializer
    queryset = ExecutionOrder.objects.prefetch_related(
        "operations__assignment__equipment", "operations__assignment__commands",
    )
    filterset_fields = ()

    def get_queryset(self):
        queryset = super().get_queryset()
        site_id = self.request.query_params.get("site_id")
        status_value = self.request.query_params.get("status")
        if site_id:
            queryset = queryset.filter(site__source_id=site_id)
        if status_value:
            queryset = queryset.filter(status=status_value)
        return queryset

    @action(detail=True, methods=["post"], url_path="dispatch-next")
    def dispatch_next(self, request, pk=None):
        """TCL-028: dispatch the order's next step as TCL-002 DCMDs.

        201: new DCMDs queued. 200: the current step is still in flight in a
        running, connected session; nothing new was sent (an idempotent re-call,
        A-18). 409: the order is not released/active, has nothing left to
        dispatch, no CHE/simulation is available, or a leg is in flight but its
        session is stopped or offline (``simulation_not_running``, TL review S-1).
        400: a bad request body (``invalid_request``, plus the DRF ``errors``) or
        an unusable ``equipment_id`` (``equipment_invalid``). 404: unknown order,
        checked before the body (QA M1). Every refusal carries ``code`` (machine)
        and ``detail`` (human).
        """
        order = self.get_object()
        serializer = DispatchRequestSerializer(data=request.data)
        if not serializer.is_valid():
            # QA M1: same {code, detail} shape as every other refusal; ``errors``
            # keeps DRF's field errors (additive).
            return Response(
                {
                    "code": DISPATCH_INVALID_REQUEST,
                    "detail": _first_error_message(serializer.errors),
                    "errors": serializer.errors,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            assignment = dispatch_next_operation(
                order.pk,
                equipment_id=serializer.validated_data.get("equipment_id"),
            )
        except DjangoValidationError as exc:
            code = getattr(exc, "code", None) or "dispatch_refused"
            detail = "; ".join(exc.messages)
            order.refresh_from_db()
            if code in DISPATCH_IN_FLIGHT_CODES and order_has_live_leg(order):
                if not live_leg_session_running(order):
                    # TL review S-1: the leg is stuck, not in flight.
                    return Response(
                        {
                            "code": DISPATCH_SIMULATION_NOT_RUNNING,
                            "detail": SIMULATION_NOT_RUNNING_MESSAGE,
                        },
                        status=status.HTTP_409_CONFLICT,
                    )
                data = dict(ExecutionOrderSerializer(
                    order, context=self.get_serializer_context(),
                ).data)
                data.update(dispatched=False, code=code, detail=detail)
                return Response(data, status=status.HTTP_200_OK)
            return Response(
                {"code": code, "detail": detail},
                status=(
                    status.HTTP_400_BAD_REQUEST if code == DISPATCH_EQUIPMENT_INVALID
                    else status.HTTP_409_CONFLICT
                ),
            )
        data = dict(ExecutionOrderSerializer(
            assignment.operation.order, context=self.get_serializer_context(),
        ).data)
        data["dispatched"] = True
        return Response(data, status=status.HTTP_201_CREATED)


def _first_error_message(errors):
    """Short human text for a DRF error dict: ``field: first message``."""
    for field, field_messages in errors.items():
        message = field_messages
        if isinstance(field_messages, list) and field_messages:
            message = field_messages[0]
        return f"Invalid request body: {field}: {message}"
    return "Invalid request body."


class EquipmentStateViewSet(ReadOnlyModelViewSet):
    serializer_class = EquipmentStateSerializer
    queryset = EquipmentState.objects.all()

    def get_queryset(self):
        queryset = super().get_queryset()
        site_id = self.request.query_params.get("site_id")
        if site_id:
            queryset = queryset.filter(site__source_id=site_id)
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
                detail=serializer.validated_data.get("detail", ""),
                notify_che=True,
            )
        except DjangoValidationError as exc:
            raise ValidationError(_service_error(exc)) from exc
        return Response(self.get_serializer(command).data)
