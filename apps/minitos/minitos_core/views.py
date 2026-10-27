import django_filters

from django.db import models, transaction
from django.template import loader
from django.shortcuts import get_object_or_404, render
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView
from django.http import JsonResponse
from django.core.exceptions import PermissionDenied, ValidationError as DjangoValidationError
from django.utils import timezone as django_timezone
from django.utils.dateparse import parse_datetime
from zoneinfo import ZoneInfo

from rest_framework.parsers import JSONParser
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.viewsets import ModelViewSet
from django_filters import rest_framework as filters

from .models import (
    AccessPolicy, BerthSegment, BusinessProcess, BusinessProcessExecution,
    CarrierService, CarrierVisit, Che, CheComponent, CheOperatingScope,
    ComponentType, ContainerOwner, ContainerUnit, ContainerVisit, Enterprise, EquipmentType,
    EquipmentTypeComponent, HandlingCycle, InterfacePosition, ISOType, LayoutAsset,
    LayoutPreset, LayoutVersion, LinearAxis, LinearMarker, Location,
    OperationalPosition, OperationalStateInterval, Order, OrderLeg,
    OrderRoutePlan, Pile, PileBooking, POD, PointOfWork, Position,
    ProcessHierarchy, ProcessVersion, RouteEdge, RouteNode, ServiceCargoRoute, Site,
    SiteConfigurationSnapshot,
    SignalBinding, SignalDefinition, StorageGrid, TransferInterface,
    VisitCargoRoute, WorkQueue, validate_iana_timezone,
)
from .serializers import (
    AccessPolicySerializer, BerthSegmentSerializer, BulkContainerVisitSerializer,
    BusinessProcessExecutionSerializer, BusinessProcessSerializer,
    CarrierServiceSerializer, CarrierVisitSerializer, CheComponentSerializer,
    CheOperatingScopeSerializer, CheSerializer, ComponentTypeSerializer,
    ContainerUnitSerializer, ContainerVisitSerializer, CurrentSignalValueSerializer,
    EnterpriseSerializer, EquipmentTypeComponentSerializer, EquipmentTypeSerializer, GroupSerializer,
    HandlingCycleSerializer, LayoutAssetSerializer, LayoutPresetSerializer,
    LayoutVersionSerializer, LinearAxisSerializer, LinearMarkerSerializer,
    LocationSerializer, OperationalPositionSerializer,
    OperationalStateIntervalSerializer, OrderLegSerializer,
    OrderRoutePlanSerializer, OrderSerializer, PileBookingSerializer,
    PileSerializer, PointOfWorkSerializer, PositionSerializer,
    ProcessHierarchySerializer, ProcessVersionSerializer, RouteEdgeSerializer,
    RouteNodeSerializer, ServiceCargoRouteSerializer, SignalBindingSerializer,
    SignalDefinitionSerializer, SiteConfigurationSnapshotSerializer, SiteSerializer,
    StorageGridSerializer,
    TransferInterfaceSerializer, UserSerializer, VisitCargoRouteSerializer,
    WorkQueueSerializer,
)
from .permissions import IsMESService, IsOESServiceOrAuthenticated
from .services import (
    EXECUTION_EVENTS, accessible_terminals, apply_execution_event, create_site,
    current_layout, ensure_site_for_location,
    generate_service_visits, plan_order_route, publish_layout, release_order,
    sync_road_junctions, terminal_capabilities, validate_layout,
)

##################################################################

from django.contrib.auth.models import User, Group
from rest_framework import viewsets, permissions, generics


# Landing page view
def landing_page(request):
    """Simple landing page with button to admin panel"""
    return render(request, 'landing.html')


# Terminal workspace views
@login_required(login_url='admin:login')
def terminal_browser_view(request):
    """Choose a terminal and one of the tools available to the current user."""
    terminals = [
        {'location': location, **terminal_capabilities(request.user, location)}
        for location in accessible_terminals(request.user)
    ]
    return render(request, 'terminal_browser.html', {'terminals': terminals})


# Backward-compatible view name used by older imports.
location_select_view = terminal_browser_view


@login_required(login_url='admin:login')
def terminal_editor_view(request, location_id=None):
    """Map editor for a specific location"""
    if location_id:
        location = get_object_or_404(Location, pk=location_id, location_type__in=['TL', 'ID'])
    else:
        # Show a form to create a new location
        location = None

    capabilities = terminal_capabilities(request.user, location) if location else {
        'can_view_terminal': True,
        'can_edit_terminal': True,
        'can_manage_berth_plan': True,
    }
    if location and not capabilities['can_edit_terminal']:
        raise PermissionDenied

    context = {
        'location': location,
        'location_id': location_id,
        'layout': current_layout(location, request.user) if location else None,
        **capabilities,
    }
    return render(request, 'terminal_editor.html', context)


@login_required(login_url='admin:login')
def che_editor_view(request, location_id):
    """Detailed CHE and component editor for a terminal."""
    location = get_object_or_404(Location, pk=location_id, location_type__in=['TL', 'ID'])
    capabilities = terminal_capabilities(request.user, location)
    if not capabilities['can_edit_terminal']:
        raise PermissionDenied
    return render(request, 'che_editor.html', {
        'location': location,
        **capabilities,
    })


@login_required(login_url='admin:login')
def berth_plan_view(request, location_id):
    location = get_object_or_404(Location, pk=location_id, location_type__in=['TL', 'ID'])
    capabilities = terminal_capabilities(request.user, location)
    if not capabilities['can_manage_berth_plan']:
        raise PermissionDenied
    berths = BerthSegment.objects.filter(
        quay_asset__layout_version__location=location,
        quay_asset__layout_version__is_current=True,
    ).select_related('quay_asset', 'quay_face_axis').prefetch_related(
        'quay_face_axis__markers',
    ).order_by('name')
    services = CarrierService.objects.filter(location=location).select_related(
        'default_berth', 'default_start_bollard', 'default_end_bollard'
    ).prefetch_related('equipment_requirements__equipment_type').order_by('name')
    visits = CarrierVisit.objects.filter(location=location).select_related(
        'carrier_service', 'planned_berth', 'berth_start_bollard', 'berth_end_bollard'
    ).order_by('eta')
    cargo_routes = ServiceCargoRoute.objects.filter(
        models.Q(inbound_carrier_service__location=location) |
        models.Q(outbound_carrier_service__location=location)
    ).select_related(
        'inbound_carrier_service', 'outbound_carrier_service', 'pod', 'iso_type'
    ).distinct().order_by('inbound_carrier_service__name', 'outbound_carrier_service__name')
    equipment_types = EquipmentType.objects.filter(is_horizontal_transport=False).annotate(
        fleet_count=models.Count(
            'che', filter=models.Q(che__location=location), distinct=True
        ),
        available_count=models.Count(
            'che',
            filter=models.Q(che__location=location, che__is_available=True),
            distinct=True,
        ),
    ).filter(fleet_count__gt=0).order_by('name')
    equipment_type_data = EquipmentTypeSerializer(equipment_types, many=True).data
    for item, equipment_type in zip(equipment_type_data, equipment_types):
        item['fleet_count'] = equipment_type.fleet_count
        item['available_count'] = equipment_type.available_count
    return render(request, 'berth_plan.html', {
        'location': location,
        **capabilities,
        'berths_data': BerthSegmentSerializer(berths, many=True).data,
        'services_data': CarrierServiceSerializer(services, many=True).data,
        'visits_data': CarrierVisitSerializer(visits, many=True).data,
        'cargo_routes_data': ServiceCargoRouteSerializer(cargo_routes, many=True).data,
        'pods_data': list(POD.objects.order_by('name').values('id', 'name', 'uncode')),
        'iso_types_data': list(ISOType.objects.order_by('iso_type').values('id', 'iso_type', 'length', 'height')),
        'equipment_types_data': equipment_type_data,
    })


def _location_time_data(location):
    local_now = django_timezone.now().astimezone(ZoneInfo(location.timezone or 'UTC'))
    return {
        'timezone': location.timezone or 'UTC',
        'timezone_utc_offset_minutes': int(local_now.utcoffset().total_seconds() // 60),
        'local_datetime': local_now.isoformat(),
    }


# API endpoints for Terminal Editor
@login_required
def location_api_detail(request, location_id):
    """API endpoint to get location data (GET)"""
    try:
        location = Location.objects.get(pk=location_id, location_type__in=['TL', 'ID'])
        return JsonResponse({
            'id': location.id,
            'name': location.name,
            'location_type': location.location_type,
            'latitude': location.latitude,
            'longitude': location.longitude,
            'map_origin_latitude': location.map_origin_latitude if location.map_origin_latitude is not None else location.latitude,
            'map_origin_longitude': location.map_origin_longitude if location.map_origin_longitude is not None else location.longitude,
            'map_width_m': location.map_width_m,
            'map_height_m': location.map_height_m,
            'map_units_per_meter': location.map_units_per_meter,
            'default_zoom': location.default_zoom or 15,
            'angle': location.angle or 0,
            **_location_time_data(location),
        })
    except Location.DoesNotExist:
        return JsonResponse({'error': 'Location not found'}, status=404)


@login_required
def location_api_save(request, location_id=None):
    """API endpoint to save/create location data (POST/PUT)"""
    if request.method == 'POST' or request.method == 'PUT':
        try:
            data = JSONParser().parse(request)

            with transaction.atomic():
                if location_id:
                    location = Location.objects.get(pk=location_id, location_type__in=['TL', 'ID'])
                    location.name = data.get('name', location.name)
                    location.latitude = data.get('latitude', location.latitude)
                    location.longitude = data.get('longitude', location.longitude)
                    location.map_origin_latitude = data.get('map_origin_latitude', location.map_origin_latitude)
                    location.map_origin_longitude = data.get('map_origin_longitude', location.map_origin_longitude)
                    location.map_width_m = data.get('map_width_m', location.map_width_m)
                    location.map_height_m = data.get('map_height_m', location.map_height_m)
                    location.map_units_per_meter = data.get('map_units_per_meter', location.map_units_per_meter)
                    location.default_zoom = data.get('default_zoom', location.default_zoom)
                    location.angle = data.get('angle', location.angle)
                    location.timezone = data.get('timezone', location.timezone)
                else:
                    location = Location(
                        name=data.get('name', 'New Location'),
                        location_type=data.get('location_type', 'TL'),
                        latitude=data.get('latitude'),
                        longitude=data.get('longitude'),
                        map_origin_latitude=data.get('map_origin_latitude') or data.get('latitude'),
                        map_origin_longitude=data.get('map_origin_longitude') or data.get('longitude'),
                        map_width_m=data.get('map_width_m', 1000.0),
                        map_height_m=data.get('map_height_m', 1000.0),
                        map_units_per_meter=data.get('map_units_per_meter', 1.0),
                        default_zoom=data.get('default_zoom', 15),
                        angle=data.get('angle', 0),
                        timezone=data.get('timezone', 'UTC'),
                    )

                validate_iana_timezone(location.timezone)
                location.save()
                site, _ = ensure_site_for_location(location)
            return JsonResponse({
                'id': location.id,
                'name': location.name,
                'location_type': location.location_type,
                'latitude': location.latitude,
                'longitude': location.longitude,
                'map_origin_latitude': location.map_origin_latitude,
                'map_origin_longitude': location.map_origin_longitude,
                'map_width_m': location.map_width_m,
                'map_height_m': location.map_height_m,
                'map_units_per_meter': location.map_units_per_meter,
                'default_zoom': location.default_zoom,
                'angle': location.angle,
                'site': {'id': str(site.pk), 'code': site.code},
                **_location_time_data(location),
                'message': 'Location saved successfully'
            })
        except Location.DoesNotExist:
            return JsonResponse({'error': 'Location not found'}, status=404)
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=400)

    return JsonResponse({'error': 'Method not allowed'}, status=405)


####  ContainerUnit
class ContainerUnitList(generics.ListCreateAPIView):
    queryset = ContainerUnit.objects.all()
    serializer_class = ContainerUnitSerializer


class ContainerUnitDetail(generics.RetrieveUpdateDestroyAPIView):
    queryset = ContainerUnit.objects.all()
    serializer_class = ContainerUnitSerializer


####  User
class UserList(generics.ListAPIView):
    queryset = User.objects.all()
    serializer_class = UserSerializer


class UserDetail(generics.RetrieveAPIView):
    queryset = User.objects.all()
    serializer_class = UserSerializer


##################################################################

class UserViewSet(viewsets.ModelViewSet):
    queryset = User.objects.all().order_by('-date_joined')
    serializer_class = UserSerializer
    permission_classes = [permissions.IsAuthenticated]


class GroupViewSet(viewsets.ModelViewSet):
    queryset = Group.objects.all()
    serializer_class = GroupSerializer
    permission_classes = [permissions.IsAuthenticated]


class ContainerUnitViewSet(viewsets.ModelViewSet):
    queryset = ContainerUnit.objects.all()
    serializer_class = ContainerUnitSerializer
    permission_classes = [permissions.IsAuthenticated]


class ContainerVisitViewSet(viewsets.ModelViewSet):
    serializer_class = ContainerVisitSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        queryset = ContainerVisit.objects.all()
        location_pk = self.kwargs.get('location_pk')
        return queryset.filter(location_id=location_pk) if location_pk else queryset


class LocationViewSet(viewsets.ViewSet):
    serializer_class = LocationSerializer
    permission_classes = [permissions.IsAuthenticated]

    def list(self, request, ):
        queryset = Location.objects.filter()
        serializer = LocationSerializer(queryset, many=True)
        return Response(serializer.data)

    def retrieve(self, request, pk=None):
        queryset = Location.objects.filter()
        location = get_object_or_404(queryset, pk=pk)
        serializer = LocationSerializer(location)
        return Response(serializer.data)


class EnterpriseViewSet(viewsets.ModelViewSet):
    queryset = Enterprise.objects.all().order_by('name')
    serializer_class = EnterpriseSerializer
    permission_classes = [permissions.IsAuthenticated]


class SiteViewSet(viewsets.ModelViewSet):
    serializer_class = SiteSerializer
    permission_classes = [IsOESServiceOrAuthenticated]

    def get_queryset(self):
        return Site.objects.select_related('enterprise', 'location').order_by(
            'enterprise__name', 'location__name',
        )

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        values = serializer.validated_data
        site = create_site(
            enterprise=values['enterprise'],
            location=values['location'],
            code=values.get('code'),
            simulation_enabled=values.get('simulation_enabled', True),
        )
        return Response(
            self.get_serializer(site).data,
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=['get'], url_path='execution-snapshot')
    def execution_snapshot(self, request, *args, **kwargs):
        site = self.get_object()
        snapshots = SiteConfigurationSnapshot.objects.filter(site=site)
        revision = request.query_params.get('revision')
        if revision is not None:
            try:
                snapshots = snapshots.filter(revision=int(revision))
            except (TypeError, ValueError):
                raise DRFValidationError({'revision': 'Revision must be an integer.'})
        else:
            snapshots = snapshots.filter(revision=site.configuration_revision)
        snapshot = get_object_or_404(snapshots)
        return Response(SiteConfigurationSnapshotSerializer(snapshot).data)

class StorageGridViewSet(viewsets.ModelViewSet):
    serializer_class = StorageGridSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        location_pk = self.kwargs.get('location_pk')
        queryset = StorageGrid.objects.filter(
            layout_asset__layout_version__location_id=location_pk,
        ).select_related('layout_asset__layout_version__location')

        # Filter by bay if provided
        bay = self.request.query_params.get('bay', None)
        if bay is not None:
            queryset = queryset.filter(piles__bay=bay).distinct()

        # Filter by row if provided
        row = self.request.query_params.get('row', None)
        if row is not None:
            queryset = queryset.filter(piles__row=row).distinct()

        return queryset

    def perform_create(self, serializer):
        location_pk = self.kwargs.get('location_pk')
        asset = serializer.validated_data.get('layout_asset')
        if not asset or asset.layout_version.location_id != int(location_pk):
            raise DRFValidationError('Storage grid asset must belong to this terminal.')
        serializer.save()

    def retrieve(self, request, pk=None, location_pk=None):
        queryset = StorageGrid.objects.filter(layout_asset__layout_version__location_id=location_pk)
        block = get_object_or_404(queryset, pk=pk)
        serializer = StorageGridSerializer(block)
        return Response(serializer.data)


class CheViewSet(viewsets.ModelViewSet):
    serializer_class = CheSerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = None

    def get_queryset(self):
        return Che.objects.filter(location_id=self.kwargs.get('location_pk')).select_related(
            'equipment_type', 'location', 'current_asset', 'current_user'
        ).prefetch_related(
            'components__component_type', 'components__template',
            'components__signal_bindings__signal_definition',
            'components__signal_bindings__current_value', 'state_intervals',
            'operating_scopes__axis__asset',
        )

    def perform_create(self, serializer):
        location = get_object_or_404(Location, pk=self.kwargs.get('location_pk'))
        serializer.save(location=location)

    def perform_destroy(self, instance):
        if instance.referenced_interfaces.exists():
            raise DRFValidationError('Remove equipment-relative transfer references before deleting this CHE.')
        instance.delete()


class PileViewSet(viewsets.ViewSet):
    serializer_class = PileSerializer
    permission_classes = [permissions.IsAuthenticated]
    filterset_fields = ['bay', 'row']
    filter_backends = [filters.DjangoFilterBackend]

    def list(self, request, location_pk=None, storage_grid_pk=None, *args, **kwargs):

        bay = self.request.query_params.get('bay')
        row = self.request.query_params.get('row')

        if bay is not None and row is not None:
            queryset = Pile.objects.filter(storage_grid__layout_asset__layout_version__location=location_pk, storage_grid=storage_grid_pk, bay=bay, row=row)
        elif bay is not None:
            queryset = Pile.objects.filter(storage_grid__layout_asset__layout_version__location=location_pk, storage_grid=storage_grid_pk, bay=bay)
        elif row is not None:
            queryset = Pile.objects.filter(storage_grid__layout_asset__layout_version__location=location_pk, storage_grid=storage_grid_pk, row=row)
        else:
            queryset = Pile.objects.filter(storage_grid__layout_asset__layout_version__location=location_pk, storage_grid=storage_grid_pk)
        # piles = get_object_or_404(queryset, many=True)
        serializer = PileSerializer(queryset, many=True)
        return Response(serializer.data)

    def retrieve(self, request, pk=None, storage_grid_pk=None, location_pk=None):
        queryset = Pile.objects.filter(storage_grid__layout_asset__layout_version__location=location_pk, storage_grid=storage_grid_pk)
        pile = get_object_or_404(queryset, pk=pk)
        serializer = PileSerializer(pile)
        return Response(serializer.data)

    def partial_update(self, request, pk=None, *args, **kwargs):
        queryset = Pile.objects.filter(pk=pk)
        pile = get_object_or_404(queryset, pk=pk)
        serializer = PileSerializer(pile, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


class PositionViewSet(viewsets.ModelViewSet):
    serializer_class = PositionSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        location_pk = self.kwargs.get('location_pk')
        storage_grid_pk = self.kwargs.get('storage_grid_pk')
        pile_pk = self.kwargs.get('pile_pk')
        return Position.objects.filter(
            pile__storage_grid__layout_asset__layout_version__location=location_pk,
            pile__storage_grid=storage_grid_pk,
            pile=pile_pk,
        )

    def list(self, request, location_pk=None, storage_grid_pk=None, pile_pk=None):
        queryset = self.get_queryset()
        serializer = PositionSerializer(queryset, many=True)
        return Response(serializer.data)

    def retrieve(self, request, pk=None, location_pk=None, storage_grid_pk=None, pile_pk=None):
        queryset = self.get_queryset()
        position = get_object_or_404(queryset, pk=pk)
        serializer = PositionSerializer(position)
        return Response(serializer.data)


class WorkQueueViewSet(viewsets.ViewSet):
    serializer_class = WorkQueueSerializer
    permission_classes = [permissions.IsAuthenticated]

    def list(self, request, location_pk=None):
        queryset = WorkQueue.objects.filter(location=location_pk)
        serializer = WorkQueueSerializer(queryset, many=True)
        return Response(serializer.data)

    def retrieve(self, request, pk=None, location_pk=None):
        queryset = WorkQueue.objects.filter(pk=pk, location=location_pk)
        work_queue = get_object_or_404(queryset, pk=pk)
        serializer = WorkQueueSerializer(work_queue)
        return Response(serializer.data)

    def partial_update(self, request, pk=None, *args, **kwargs):
        queryset = WorkQueue.objects.filter(pk=pk)
        work_queue = get_object_or_404(queryset, pk=pk)
        serializer = WorkQueueSerializer(work_queue, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


class OrderViewSet(viewsets.ViewSet):
    serializer_class = OrderSerializer
    permission_classes = [permissions.IsAuthenticated]

    def list(self, request, location_pk=None, work_queue_pk=None):
        queryset = Order.objects.filter(work_queue=work_queue_pk, work_queue__location=location_pk)
        serializer = OrderSerializer(queryset, many=True)
        return Response(serializer.data)

    def retrieve(self, request, pk=None, location_pk=None, work_queue_pk=None):
        queryset = Order.objects.filter(pk=pk, work_queue=work_queue_pk, work_queue__location=location_pk)
        order = get_object_or_404(queryset, pk=pk)
        serializer = OrderSerializer(order)
        return Response(serializer.data)

    def _scoped_order(self, pk, location_pk, work_queue_pk):
        return get_object_or_404(
            Order.objects.filter(
                work_queue=work_queue_pk,
                work_queue__location=location_pk,
            ),
            pk=pk,
        )

    @transaction.atomic
    def create(self, request, location_pk=None, work_queue_pk=None):
        work_queue = get_object_or_404(
            WorkQueue.objects.filter(location=location_pk),
            pk=work_queue_pk,
        )
        payload = request.data.copy()
        payload['work_queue'] = work_queue.pk
        serializer = OrderSerializer(
            data=payload,
            context={'request': request, 'view': self},
        )
        serializer.is_valid(raise_exception=True)
        order = serializer.save()
        try:
            plan_order_route(order)
        except DjangoValidationError as exc:
            raise DRFValidationError({'route': exc.messages})
        return Response(OrderSerializer(order).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'])
    def release(self, request, pk=None, location_pk=None, work_queue_pk=None):
        order = self._scoped_order(pk, location_pk, work_queue_pk)
        try:
            order = release_order(order.pk)
        except DjangoValidationError as exc:
            raise DRFValidationError({'status': exc.messages})
        return Response(OrderSerializer(order).data)

    @action(
        detail=True,
        methods=['post'],
        url_path='execution-callback',
        permission_classes=[IsMESService],
    )
    def execution_callback(self, request, pk=None, location_pk=None, work_queue_pk=None):
        order = self._scoped_order(pk, location_pk, work_queue_pk)
        event = request.data.get('event')
        if event not in EXECUTION_EVENTS:
            raise DRFValidationError({
                'event': f'Use one of: {", ".join(sorted(EXECUTION_EVENTS))}.'
            })
        occurred_at = None
        if request.data.get('occurred_at'):
            occurred_at = parse_datetime(request.data['occurred_at'])
            if occurred_at is None:
                raise DRFValidationError({'occurred_at': 'Use an ISO-8601 date-time.'})
        try:
            order = apply_execution_event(
                order.pk,
                event,
                leg_id=request.data.get('leg_id'),
                che_id=request.data.get('che_id'),
                position_id=request.data.get('position_id'),
                occurred_at=occurred_at,
            )
        except (DjangoValidationError, Order.DoesNotExist, OrderLeg.DoesNotExist,
                ContainerVisit.DoesNotExist, Position.DoesNotExist, Che.DoesNotExist) as exc:
            messages = getattr(exc, 'messages', [str(exc)])
            raise DRFValidationError({'execution': messages})
        return Response(OrderSerializer(order).data)


class OrderLegViewSet(viewsets.ViewSet):
    serializer_class = OrderLegSerializer
    permission_classes = [permissions.IsAuthenticated]

    def list(self, request, location_pk=None, work_queue_pk=None, order_pk=None):
        queryset = OrderLeg.objects.filter(
            order__work_queue=work_queue_pk,
            order__work_queue__location=location_pk,
            order=order_pk,
        )
        serializer = OrderLegSerializer(queryset, many=True)
        return Response(serializer.data)

    def retrieve(self, request, pk=None, location_pk=None, work_queue_pk=None, order_pk=None):
        queryset = OrderLeg.objects.filter(
            pk=pk,
            order=order_pk,
            order__work_queue=work_queue_pk,
            order__work_queue__location=location_pk,
        )
        order = get_object_or_404(queryset, pk=pk)
        serializer = OrderLegSerializer(order)
        return Response(serializer.data)


##################################################################

#### CarrierVisit Views
class CarrierVisitViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing transport visits (vessels, trucks, trains, barges).
    """
    queryset = CarrierVisit.objects.select_related('primary_equipment_type').prefetch_related(
        'equipment_requirements__equipment_type'
    ).order_by('-eta')
    serializer_class = CarrierVisitSerializer
    permission_classes = [permissions.IsAuthenticated]
    filterset_fields = ['status', 'visit_type', 'location', 'vessel_name']
    filter_backends = [filters.DjangoFilterBackend]

    def get_queryset(self):
        queryset = super().get_queryset()
        location_pk = self.kwargs.get('location_pk')
        if location_pk:
            queryset = queryset.filter(location=location_pk)
        return queryset


class CarrierServiceViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing carrier services with conditional field display based on repeat pattern.
    """
    queryset = CarrierService.objects.select_related('primary_equipment_type').prefetch_related(
        'equipment_requirements__equipment_type'
    ).order_by('name')
    serializer_class = CarrierServiceSerializer
    permission_classes = [permissions.IsAuthenticated]
    filterset_fields = ['repeat', 'periodicity']
    filter_backends = [filters.DjangoFilterBackend]

    def get_queryset(self):
        queryset = super().get_queryset()
        location_pk = self.kwargs.get('location_pk') or self.request.query_params.get('location')
        return queryset.filter(location_id=location_pk) if location_pk else queryset

    @action(detail=True, methods=['post'], url_path='generate-visits')
    def generate_visits(self, request, pk=None):
        service = self.get_object()
        try:
            from datetime import date
            horizon_start = date.fromisoformat(request.data.get('start_date'))
            horizon_end = date.fromisoformat(request.data.get('end_date'))
        except (TypeError, ValueError):
            return Response(
                {'error': 'start_date and end_date are required in YYYY-MM-DD format.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if horizon_end < horizon_start:
            return Response({'error': 'end_date must not be before start_date.'}, status=status.HTTP_400_BAD_REQUEST)
        created = generate_service_visits(service, horizon_start, horizon_end)
        return Response({
            'created_count': len(created),
            'visits': CarrierVisitSerializer(created, many=True).data,
        }, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


class ServiceCargoRouteViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing service cargo routes.
    """
    queryset = ServiceCargoRoute.objects.all().order_by('inbound_carrier_service__name', 'outbound_carrier_service__name')
    serializer_class = ServiceCargoRouteSerializer
    permission_classes = [permissions.IsAuthenticated]
    filterset_fields = ['inbound_carrier_service', 'outbound_carrier_service', 'container_type', 'is_reefer']
    filter_backends = [filters.DjangoFilterBackend]

    def get_queryset(self):
        queryset = super().get_queryset()
        carrier_service_pk = self.kwargs.get('carrierservice_pk')
        if carrier_service_pk:
            queryset = queryset.filter(
                models.Q(inbound_carrier_service=carrier_service_pk) |
                models.Q(outbound_carrier_service=carrier_service_pk)
            )
        return queryset


class VisitCargoRouteViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing visit cargo routes.
    """
    queryset = VisitCargoRoute.objects.all().order_by('inbound_carrier_visit__name', 'outbound_carrier_service__name')
    serializer_class = VisitCargoRouteSerializer
    permission_classes = [permissions.IsAuthenticated]
    filterset_fields = ['inbound_carrier_visit', 'outbound_carrier_visit', 'outbound_carrier_service', 'container_type', 'is_reefer']
    filter_backends = [filters.DjangoFilterBackend]

    def get_queryset(self):
        queryset = super().get_queryset()
        carrier_visit_pk = self.kwargs.get('carriervisit_pk')
        if carrier_visit_pk:
            queryset = queryset.filter(
                models.Q(inbound_carrier_visit=carrier_visit_pk) |
                models.Q(outbound_carrier_visit=carrier_visit_pk)
            )
        return queryset


class PileBookingViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing pile bookings.
    """
    queryset = PileBooking.objects.all().order_by('start_booking_time')
    serializer_class = PileBookingSerializer
    permission_classes = [permissions.IsAuthenticated]
    filterset_fields = ['pile', 'visit_cargo_route']
    filter_backends = [filters.DjangoFilterBackend]

    def get_queryset(self):
        queryset = super().get_queryset()
        carrier_visit_pk = self.kwargs.get('carriervisit_pk')
        if carrier_visit_pk:
            # Get all VisitCargoRoutes for this carrier visit
            visit_cargo_routes = VisitCargoRoute.objects.filter(
                models.Q(inbound_carrier_visit=carrier_visit_pk) |
                models.Q(outbound_carrier_visit=carrier_visit_pk)
            )
            # Get all pile bookings for these visit cargo routes
            queryset = queryset.filter(visit_cargo_route__in=visit_cargo_routes)
        return queryset


class PointOfWorkViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing points of work.
    """
    queryset = PointOfWork.objects.all().order_by('location__name', 'code')
    serializer_class = PointOfWorkSerializer
    permission_classes = [permissions.IsAuthenticated]
    filterset_fields = ['location', 'pow_type', 'is_active']
    filter_backends = [filters.DjangoFilterBackend]


class LayoutVersionViewSet(viewsets.ModelViewSet):
    serializer_class = LayoutVersionSerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = None

    def get_queryset(self):
        return LayoutVersion.objects.filter(
            location_id=self.kwargs.get('location_pk')
        ).select_related('location', 'created_by').order_by('-version')

    def perform_create(self, serializer):
        location = get_object_or_404(Location, pk=self.kwargs.get('location_pk'))
        next_version = (location.layout_versions.aggregate(value=models.Max('version'))['value'] or 0) + 1
        make_current = serializer.validated_data.get('is_current', False)
        if make_current:
            location.layout_versions.filter(is_current=True).update(is_current=False)
        serializer.save(
            location=location,
            version=next_version,
            created_by=self.request.user,
        )

    @action(detail=True, methods=['get'], url_path='validate')
    def validate_definition(self, request, *args, **kwargs):
        return Response(validate_layout(self.get_object()))

    @action(detail=True, methods=['post'])
    def publish(self, request, *args, **kwargs):
        try:
            layout = publish_layout(self.get_object())
        except DjangoValidationError as exc:
            return Response({'detail': exc.messages}, status=status.HTTP_400_BAD_REQUEST)
        return Response(LayoutVersionSerializer(layout).data)


class CurrentLayoutScopedViewSet(viewsets.ModelViewSet):
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = None
    layout_lookup = 'layout_version'

    def selected_layout(self):
        location = get_object_or_404(Location, pk=self.kwargs.get('location_pk'))
        requested = self.request.query_params.get('layout_version') or self.request.data.get('layout_version')
        if requested:
            return get_object_or_404(LayoutVersion, pk=requested, location=location)
        return current_layout(location, self.request.user)

    def get_serializer_context(self):
        context = super().get_serializer_context()
        if self.request.method in {'POST', 'PUT', 'PATCH'}:
            context['layout_version'] = self.selected_layout()
        return context


class LayoutAssetViewSet(CurrentLayoutScopedViewSet):
    serializer_class = LayoutAssetSerializer

    def get_queryset(self):
        queryset = LayoutAsset.objects.filter(
            layout_version__location_id=self.kwargs.get('location_pk')
        ).select_related('layout_version', 'parent', 'access_policy').prefetch_related(
            'linear_axes__markers', 'transfer_interfaces__positions',
            'berth_segments__quay_face_axis__markers',
        )
        requested = self.request.query_params.get('layout_version')
        return queryset.filter(layout_version_id=requested) if requested else queryset.filter(layout_version__is_current=True)

    def perform_create(self, serializer):
        layout = self.selected_layout()
        parent = serializer.validated_data.get('parent')
        policy = serializer.validated_data.get('access_policy')
        if parent and parent.layout_version_id != layout.pk:
            raise DRFValidationError('Parent asset must belong to this terminal layout.')
        if policy and policy.layout_version_id != layout.pk:
            raise DRFValidationError('Access policy must belong to this terminal layout.')
        serializer.save(layout_version=layout)


class AccessPolicyViewSet(CurrentLayoutScopedViewSet):
    serializer_class = AccessPolicySerializer

    def get_queryset(self):
        queryset = AccessPolicy.objects.filter(
            layout_version__location_id=self.kwargs.get('location_pk')
        ).prefetch_related('allowed_che_types')
        requested = self.request.query_params.get('layout_version')
        return queryset.filter(layout_version_id=requested) if requested else queryset.filter(layout_version__is_current=True)

    def perform_create(self, serializer):
        serializer.save(layout_version=self.selected_layout())


class LinearAxisViewSet(CurrentLayoutScopedViewSet):
    serializer_class = LinearAxisSerializer

    def get_queryset(self):
        queryset = LinearAxis.objects.filter(
            asset__layout_version__location_id=self.kwargs.get('location_pk')
        ).select_related('asset', 'access_policy').prefetch_related('markers')
        requested = self.request.query_params.get('layout_version')
        return queryset.filter(asset__layout_version_id=requested) if requested else queryset.filter(asset__layout_version__is_current=True)

    def perform_create(self, serializer):
        asset = serializer.validated_data.get('asset')
        if not asset or asset.layout_version.location_id != int(self.kwargs.get('location_pk')):
            raise DRFValidationError('Axis asset must belong to this terminal.')
        serializer.save()

    def perform_destroy(self, instance):
        layout = instance.layout_version
        instance.delete()
        sync_road_junctions(layout)


class BerthSegmentViewSet(CurrentLayoutScopedViewSet):
    serializer_class = BerthSegmentSerializer

    def get_queryset(self):
        queryset = BerthSegment.objects.filter(
            quay_asset__layout_version__location_id=self.kwargs.get('location_pk')
        ).select_related('quay_asset', 'quay_face_axis').prefetch_related('quay_face_axis__markers')
        requested = self.request.query_params.get('layout_version')
        return queryset.filter(quay_asset__layout_version_id=requested) if requested else queryset.filter(quay_asset__layout_version__is_current=True)

    def perform_create(self, serializer):
        quay = serializer.validated_data.get('quay_asset')
        axis = serializer.validated_data.get('quay_face_axis')
        location_id = int(self.kwargs.get('location_pk'))
        if not quay or quay.layout_version.location_id != location_id:
            raise DRFValidationError('Quay asset must belong to this terminal.')
        if not axis or axis.asset_id != quay.pk:
            raise DRFValidationError('Quay face axis must belong to the selected quay.')
        serializer.save()


class TransferInterfaceViewSet(CurrentLayoutScopedViewSet):
    serializer_class = TransferInterfaceSerializer

    def get_queryset(self):
        queryset = TransferInterface.objects.filter(
            asset__layout_version__location_id=self.kwargs.get('location_pk')
        ).select_related(
            'asset', 'storage_grid', 'axis', 'road_axis', 'access_policy', 'reference_che',
        ).prefetch_related('positions')
        requested = self.request.query_params.get('layout_version')
        return queryset.filter(asset__layout_version_id=requested) if requested else queryset.filter(asset__layout_version__is_current=True)

    def perform_create(self, serializer):
        asset = serializer.validated_data.get('asset')
        if not asset or asset.layout_version.location_id != int(self.kwargs.get('location_pk')):
            raise DRFValidationError('Interface asset must belong to this terminal.')
        serializer.save()

    def perform_destroy(self, instance):
        asset = instance.asset
        instance.delete()
        if not asset.transfer_interfaces.exists():
            RouteNode.objects.filter(
                layout_version=asset.layout_version,
                asset=asset,
                is_generated=True,
                metadata__aggregate_kind='asset',
            ).delete()


class OperationalPositionViewSet(CurrentLayoutScopedViewSet):
    serializer_class = OperationalPositionSerializer

    def get_queryset(self):
        queryset = OperationalPosition.objects.filter(
            layout_version__location_id=self.kwargs.get('location_pk')
        ).select_related('asset', 'stack_position', 'axis',
                         'interface_position__transfer_interface__reference_che',
                         'interface_position__transfer_interface__axis')
        requested = self.request.query_params.get('layout_version')
        return queryset.filter(layout_version_id=requested) if requested else queryset.filter(layout_version__is_current=True)

    def perform_create(self, serializer):
        layout = self.selected_layout()
        asset = serializer.validated_data.get('asset')
        axis = serializer.validated_data.get('axis')
        if asset and asset.layout_version_id != layout.pk:
            raise DRFValidationError('Operational position asset belongs to another layout.')
        if axis and axis.asset.layout_version_id != layout.pk:
            raise DRFValidationError('Operational position axis belongs to another layout.')
        serializer.save(layout_version=layout)


class RouteNodeViewSet(CurrentLayoutScopedViewSet):
    serializer_class = RouteNodeSerializer

    def get_queryset(self):
        queryset = RouteNode.objects.filter(
            layout_version__location_id=self.kwargs.get('location_pk')
        ).select_related('asset', 'transfer_interface', 'operational_position',
                         'transfer_interface__reference_che', 'transfer_interface__axis',
                         'operational_position__interface_position__transfer_interface__reference_che',
                         'operational_position__interface_position__transfer_interface__axis')
        requested = self.request.query_params.get('layout_version')
        return queryset.filter(layout_version_id=requested) if requested else queryset.filter(layout_version__is_current=True)

    def perform_create(self, serializer):
        layout = self.selected_layout()
        asset = serializer.validated_data.get('asset')
        position = serializer.validated_data.get('operational_position')
        if asset and asset.layout_version_id != layout.pk:
            raise DRFValidationError('Route node asset belongs to another layout.')
        if position and position.layout_version_id != layout.pk:
            raise DRFValidationError('Route node position belongs to another layout.')
        serializer.save(layout_version=layout)

    def perform_update(self, serializer):
        if serializer.instance.is_generated:
            raise DRFValidationError('Generated nodes are managed by their movement axis.')
        serializer.save()

    def perform_destroy(self, instance):
        if instance.is_generated:
            raise DRFValidationError('Generated nodes are managed by their movement axis.')
        instance.delete()


class RouteEdgeViewSet(CurrentLayoutScopedViewSet):
    serializer_class = RouteEdgeSerializer

    def get_queryset(self):
        queryset = RouteEdge.objects.filter(
            layout_version__location_id=self.kwargs.get('location_pk')
        ).select_related('from_node', 'to_node', 'source_axis', 'access_policy').prefetch_related('requirements')
        requested = self.request.query_params.get('layout_version')
        return queryset.filter(layout_version_id=requested) if requested else queryset.filter(layout_version__is_current=True)

    def perform_create(self, serializer):
        layout = self.selected_layout()
        first = serializer.validated_data.get('from_node')
        second = serializer.validated_data.get('to_node')
        if not first or not second or any(
            node.layout_version_id != layout.pk for node in (first, second)
        ):
            raise DRFValidationError('Route edge nodes must belong to this terminal layout.')
        serializer.save(layout_version=layout)

    def perform_update(self, serializer):
        if serializer.instance.is_generated:
            raise DRFValidationError('Generated connections are managed by their movement axis.')
        serializer.save()

    def perform_destroy(self, instance):
        if instance.is_generated:
            raise DRFValidationError('Generated connections are managed by their movement axis.')
        instance.delete()


class CheOperatingScopeViewSet(viewsets.ModelViewSet):
    serializer_class = CheOperatingScopeSerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = None

    def get_queryset(self):
        location_id = self.kwargs.get('location_pk')
        return CheOperatingScope.objects.filter(
            models.Q(che__location_id=location_id)
            | models.Q(equipment_type__che__location_id=location_id)
            | models.Q(asset__layout_version__location_id=location_id)
            | models.Q(axis__asset__layout_version__location_id=location_id)
            | models.Q(service_lane_axis__asset__layout_version__location_id=location_id)
        ).select_related('che', 'equipment_type', 'asset', 'axis', 'service_lane_axis').distinct()

    def _validate_terminal(self, serializer):
        location_id = int(self.kwargs.get('location_pk'))
        asset = serializer.validated_data.get(
            'asset', getattr(serializer.instance, 'asset', None)
        )
        axis = serializer.validated_data.get(
            'axis', getattr(serializer.instance, 'axis', None)
        )
        service_lane_axis = serializer.validated_data.get(
            'service_lane_axis', getattr(serializer.instance, 'service_lane_axis', None)
        )
        che = serializer.validated_data.get(
            'che', getattr(serializer.instance, 'che', None)
        )
        topology_location_id = (
            asset.layout_version.location_id if asset
            else axis.asset.layout_version.location_id if axis
            else None
        )
        if topology_location_id != location_id:
            raise DRFValidationError('Operating scope topology must belong to this terminal.')
        if che and che.location_id != location_id:
            raise DRFValidationError('Operating scope CHE must belong to this terminal.')
        if service_lane_axis and service_lane_axis.asset.layout_version.location_id != location_id:
            raise DRFValidationError('Operating scope service lane must belong to this terminal.')

    def perform_create(self, serializer):
        self._validate_terminal(serializer)
        serializer.save()

    def perform_update(self, serializer):
        self._validate_terminal(serializer)
        serializer.save()


class LayoutPresetViewSet(viewsets.ModelViewSet):
    queryset = LayoutPreset.objects.filter(is_active=True).order_by('name', '-version')
    serializer_class = LayoutPresetSerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = None


class EquipmentTypeViewSet(viewsets.ModelViewSet):
    queryset = EquipmentType.objects.all().prefetch_related(
        'component_profiles__component_type'
    ).order_by('name')
    serializer_class = EquipmentTypeSerializer
    permission_classes = [permissions.IsAuthenticated]
    filterset_fields = [
        'equipment_type', 'energy_source', 'is_automated',
        'is_horizontal_transport', 'is_yard_equipment',
        'is_quay_equipment', 'is_rail_equipment', 'is_gate_equipment',
    ]
    filter_backends = [filters.DjangoFilterBackend]


class ComponentTypeViewSet(viewsets.ModelViewSet):
    queryset = ComponentType.objects.all().order_by('name')
    serializer_class = ComponentTypeSerializer
    permission_classes = [permissions.IsAuthenticated]
    filterset_fields = ['code', 'is_motion_component', 'is_energy_component']
    filter_backends = [filters.DjangoFilterBackend]


class SignalDefinitionViewSet(viewsets.ModelViewSet):
    queryset = SignalDefinition.objects.select_related('component_type').all()
    serializer_class = SignalDefinitionSerializer
    permission_classes = [permissions.IsAuthenticated]
    filterset_fields = ['component_type', 'data_type', 'value_kind', 'is_required_for_oee']
    filter_backends = [filters.DjangoFilterBackend]


class EquipmentTypeComponentViewSet(viewsets.ModelViewSet):
    serializer_class = EquipmentTypeComponentSerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = None

    def get_queryset(self):
        return EquipmentTypeComponent.objects.filter(
            equipment_type_id=self.kwargs.get('equipment_type_pk')
        ).select_related('equipment_type', 'component_type')

    def perform_create(self, serializer):
        equipment_type = get_object_or_404(
            EquipmentType, pk=self.kwargs.get('equipment_type_pk')
        )
        profile = serializer.save(equipment_type=equipment_type)
        self._sync_installed_components(profile.equipment_type)

    def perform_update(self, serializer):
        profile = serializer.save()
        self._sync_installed_components(profile.equipment_type)

    def _sync_installed_components(self, equipment_type):
        from .services.che_components import sync_che_components

        for che in equipment_type.che_set.all():
            sync_che_components(che)


class CheComponentViewSet(viewsets.ModelViewSet):
    serializer_class = CheComponentSerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = None

    def _parent_che(self):
        return get_object_or_404(
            Che, pk=self.kwargs.get('che_pk'), location_id=self.kwargs.get('location_pk')
        )

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context['che'] = Che.objects.filter(
            pk=self.kwargs.get('che_pk'), location_id=self.kwargs.get('location_pk')
        ).first()
        return context

    def get_queryset(self):
        return CheComponent.objects.filter(
            che_id=self.kwargs.get('che_pk'),
            che__location_id=self.kwargs.get('location_pk'),
        ).select_related(
            'che', 'component_type', 'template', 'parent', 'engine', 'battery',
            'energy_tank', 'motion_axis', 'spreader',
        ).prefetch_related('signal_bindings__signal_definition', 'signal_bindings__current_value')

    def perform_create(self, serializer):
        serializer.save(che=self._parent_che())


class SignalBindingViewSet(viewsets.ModelViewSet):
    serializer_class = SignalBindingSerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = None

    def _parent_component(self):
        return get_object_or_404(
            CheComponent,
            pk=self.kwargs.get('component_pk'),
            che_id=self.kwargs.get('che_pk'),
            che__location_id=self.kwargs.get('location_pk'),
        )

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context['component'] = self._parent_component()
        return context

    def get_queryset(self):
        return SignalBinding.objects.filter(
            component_id=self.kwargs.get('component_pk'),
            component__che_id=self.kwargs.get('che_pk'),
            component__che__location_id=self.kwargs.get('location_pk'),
        ).select_related('component', 'signal_definition', 'signal_definition__component_type')

    def perform_create(self, serializer):
        serializer.save(component=self._parent_component())

    @action(detail=True, methods=['put', 'patch'], url_path='current-value')
    def current_value(self, request, *args, **kwargs):
        binding = self.get_object()
        current = getattr(binding, 'current_value', None)
        serializer = CurrentSignalValueSerializer(
            current,
            data=request.data,
            partial=request.method == 'PATCH',
        )
        serializer.is_valid(raise_exception=True)
        from .services.che_components import record_signal_value

        try:
            current = record_signal_value(binding, **serializer.validated_data)
        except DjangoValidationError as exc:
            return Response({'value': exc.messages}, status=status.HTTP_400_BAD_REQUEST)
        return Response(CurrentSignalValueSerializer(current).data)


class OperationalStateIntervalViewSet(viewsets.ModelViewSet):
    serializer_class = OperationalStateIntervalSerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = None

    def get_queryset(self):
        return OperationalStateInterval.objects.filter(
            che_id=self.kwargs.get('che_pk'),
            che__location_id=self.kwargs.get('location_pk'),
        )

    def perform_create(self, serializer):
        che = get_object_or_404(
            Che, pk=self.kwargs.get('che_pk'), location_id=self.kwargs.get('location_pk')
        )
        with transaction.atomic():
            if serializer.validated_data.get('ended_at') is None:
                OperationalStateInterval.objects.filter(
                    che=che, ended_at__isnull=True
                ).update(ended_at=serializer.validated_data['started_at'])
            serializer.save(che=che)


class HandlingCycleViewSet(viewsets.ModelViewSet):
    serializer_class = HandlingCycleSerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = None

    def get_queryset(self):
        return HandlingCycle.objects.filter(
            che_id=self.kwargs.get('che_pk'),
            che__location_id=self.kwargs.get('location_pk'),
        ).select_related('che', 'order_leg', 'container_visit')

    def perform_create(self, serializer):
        che = get_object_or_404(
            Che, pk=self.kwargs.get('che_pk'), location_id=self.kwargs.get('location_pk')
        )
        serializer.save(che=che)


class ProcessHierarchyViewSet(viewsets.ModelViewSet):
    queryset = ProcessHierarchy.objects.select_related('parent').all()
    serializer_class = ProcessHierarchySerializer
    permission_classes = [permissions.IsAuthenticated]
    filterset_fields = ['process_area', 'parent', 'is_active']
    filter_backends = [filters.DjangoFilterBackend]


class BusinessProcessViewSet(viewsets.ModelViewSet):
    serializer_class = BusinessProcessSerializer
    permission_classes = [permissions.IsAuthenticated]
    filterset_fields = ['hierarchy', 'location', 'trigger_type', 'is_active']
    filter_backends = [filters.DjangoFilterBackend]

    def get_queryset(self):
        return BusinessProcess.objects.select_related('hierarchy', 'location').prefetch_related('versions')


class ProcessVersionViewSet(viewsets.ModelViewSet):
    serializer_class = ProcessVersionSerializer
    permission_classes = [permissions.IsAuthenticated]
    filterset_fields = ['business_process', 'status', 'is_current']
    filter_backends = [filters.DjangoFilterBackend]

    def get_queryset(self):
        return ProcessVersion.objects.select_related('business_process', 'created_by')

    @action(detail=True, methods=['post'])
    def execute(self, request, pk=None):
        version = self.get_object()
        location_id = request.data.get('location') or version.business_process.location_id
        location = None
        if location_id:
            location = get_object_or_404(Location, pk=location_id)
        execution = BusinessProcessExecution.objects.create(
            business_process=version.business_process,
            process_version=version,
            location=location,
            status=BusinessProcessExecution.RUNNING,
            trigger_source=request.data.get('trigger_source', BusinessProcess.TRIGGER_MANUAL),
            trigger_payload=request.data.get('trigger_payload', {}),
            requested_by=request.user if request.user.is_authenticated else None,
        )
        execution.mark_completed({
            'message': 'Process execution recorded. Node-RED runtime deployment is not enabled yet.',
            'nodered_flow_node_count': len(version.nodered_flow) if isinstance(version.nodered_flow, list) else None,
            'mqtt_announcement_topic': version.mqtt_announcement_topic,
        })
        serializer = BusinessProcessExecutionSerializer(execution, context={'request': request})
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class BusinessProcessExecutionViewSet(viewsets.ModelViewSet):
    serializer_class = BusinessProcessExecutionSerializer
    permission_classes = [permissions.IsAuthenticated]
    filterset_fields = ['business_process', 'process_version', 'location', 'status']
    filter_backends = [filters.DjangoFilterBackend]

    def get_queryset(self):
        return BusinessProcessExecution.objects.select_related(
            'business_process', 'process_version', 'location', 'requested_by'
        )


@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
def bulk_create_container_visits(request):
    """
    API endpoint for bulk creating container visits.
    Expects: { "containers": [ {container_data}, ... ] }
    """
    serializer = BulkContainerVisitSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    try:
        created_visits = serializer.save()
        response_data = {
            'success': True,
            'message': f'Successfully created {len(created_visits)} container visits',
            'created_visits': [visit.id for visit in created_visits]
        }
        return Response(response_data, status=status.HTTP_201_CREATED)
    except Exception as e:
        return Response({
            'success': False,
            'message': f'Failed to create container visits: {str(e)}'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
def transition_visit_status(request, visit_id):
    """
    API endpoint for transitioning visit status.
    """
    try:
        visit = CarrierVisit.objects.get(id=visit_id)
    except CarrierVisit.DoesNotExist:
        return Response({'error': 'Visit not found'}, status=status.HTTP_404_NOT_FOUND)

    new_status = request.data.get('status')
    if not new_status:
        return Response({'error': 'New status is required'}, status=status.HTTP_400_BAD_REQUEST)

    # TCL-032: the legacy VisitStateManager/VisitSerializer never existed in this
    # codebase. Use the same rules as the CarrierVisit API (CarrierVisitSerializer:
    # status must be one of CarrierVisit.STATUS_CHOICES); no transition graph is
    # enforced because none is defined yet (open question for the PO).
    old_status = visit.status
    serializer = CarrierVisitSerializer(visit, data={'status': new_status}, partial=True)
    if not serializer.is_valid():
        return Response({
            'success': False,
            'message': f'Invalid status {new_status!r} for visit {visit.name}.',
            'errors': serializer.errors,
        }, status=status.HTTP_400_BAD_REQUEST)
    serializer.save()
    return Response({
        'success': True,
        'message': f'Visit {visit.name} status changed from {old_status} to {visit.status}.',
        'visit': serializer.data,
    })


@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
def transition_container_status(request, container_id):
    """
    API endpoint for transitioning container visit status.
    """
    try:
        container_visit = ContainerVisit.objects.get(id=container_id)
    except ContainerVisit.DoesNotExist:
        return Response({'error': 'Container visit not found'}, status=status.HTTP_404_NOT_FOUND)

    new_status = request.data.get('status')
    if not new_status:
        return Response({'error': 'New status is required'}, status=status.HTTP_400_BAD_REQUEST)

    # TCL-032: same fix as transition_visit_status. Validate through the existing
    # ContainerVisitSerializer (model status choices + its OnEquipment rules);
    # no transition graph is enforced. A same-status request is a 200 no-op.
    old_status = container_visit.status
    if new_status == old_status:
        return Response({
            'success': True,
            'message': f'Container visit {container_visit.pk} is already {old_status}; no change.',
            'container_visit': ContainerVisitSerializer(container_visit).data,
        })
    serializer = ContainerVisitSerializer(container_visit, data={'status': new_status}, partial=True)
    if not serializer.is_valid():
        return Response({
            'success': False,
            'message': f'Invalid status {new_status!r} for container visit {container_visit.pk}.',
            'errors': serializer.errors,
        }, status=status.HTTP_400_BAD_REQUEST)
    serializer.save()
    return Response({
        'success': True,
        'message': f'Container visit {container_visit.pk} status changed from {old_status} to {container_visit.status}.',
        'container_visit': serializer.data,
    })


@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated])
def visit_status_summary(request, visit_id):
    """
    API endpoint for getting container status summary for a visit.
    """
    try:
        visit = CarrierVisit.objects.get(id=visit_id)
    except CarrierVisit.DoesNotExist:
        return Response({'error': 'Visit not found'}, status=status.HTTP_404_NOT_FOUND)

    # Count containers linked to this visit
    discharge_count = ContainerVisit.objects.filter(ib_carrier=visit).count()
    load_count = ContainerVisit.objects.filter(ob_carrier=visit).count()

    return Response({
        'visit_id': visit_id,
        'discharge_containers': discharge_count,
        'load_containers': load_count,
        'total_containers': discharge_count + load_count
    })


##################################################################

def owner_detail(request, containerowner_id):
    owner = get_object_or_404(ContainerOwner, pk=containerowner_id)
    return render(request, 'containers/owners/detail.html', {'owner': owner})


def owner_index(request):
    all_owners = ContainerOwner.objects.all()
    return render(request, 'containers/owners/index.html', {'all_owners': all_owners})


def isotype_detail(request, isotype_id):
    isotype = get_object_or_404(ISOType, pk=isotype_id)
    return render(request, 'containers/isotypes/detail.html', {'isotype': isotype})


def isotype_index(request):
    all_isotypes = ISOType.objects.all()
    return render(request, 'containers/isotypes/index.html', {'all_isotypes': all_isotypes})


def container_detail(request, container_id):
    container = get_object_or_404(ContainerUnit, pk=container_id)
    return render(request, 'containers/containers/detail.html', {'container': container})


def container_index(request):
    all_containers = ContainerUnit.objects.all()
    return render(request, 'containers/containers/index.html', {'all_containers': all_containers})


def owner_containers(request, containerowner_id):
    owner_containers = get_object_or_404(ContainerOwner, pk=containerowner_id)
    containers = ContainerUnit.objects.filter(owner=containerowner_id)
    return render(request, 'containers/owners/owner_containers.html',
                  {'owner_containers': owner_containers, 'containers': containers})


def isotype_containers(request, isotype_id):
    isotype_containers = get_object_or_404(ISOType, pk=isotype_id)
    containers = ContainerUnit.objects.filter(iso_type=isotype_id)
    return render(request, 'containers/isotypes/isotype_containers.html',
                  {'isotype_containers': isotype_containers, 'containers': containers})
