import django_filters

from django.db import models
from django.template import loader
from django.shortcuts import get_object_or_404, render
from django.views.decorators.csrf import csrf_exempt

from rest_framework.parsers import JSONParser
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet
from django_filters import rest_framework as filters

from .models import Location, Block, Pile, Position, ContainerOwner, ContainerUnit, ContainerVisit, CarrierVisit, ISOType, Che, \
    WorkQueue, Order, OrderLeg, CarrierService, ServiceCargoRoute, VisitCargoRoute, PileBooking, PointOfWork, POWConnection
from .serializers import LocationSerializer, BlockSerializer, PileSerializer, PositionSerializer, UserSerializer, \
    GroupSerializer, ContainerUnitSerializer, ContainerVisitSerializer, CheSerializer, WorkQueueSerializer, \
    OrderSerializer, OrderLegSerializer, CarrierVisitSerializer, BulkContainerVisitSerializer, CarrierServiceSerializer, ServiceCargoRouteSerializer, VisitCargoRouteSerializer, PileBookingSerializer, PointOfWorkSerializer, POWConnectionSerializer

##################################################################

from django.contrib.auth.models import User, Group
from rest_framework import viewsets, permissions, generics


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
    queryset = ContainerVisit.objects.all()
    serializer_class = ContainerVisitSerializer
    permission_classes = [permissions.IsAuthenticated]


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


class BlockViewSet(viewsets.ModelViewSet):
    serializer_class = BlockSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        location_pk = self.kwargs.get('location_pk')
        queryset = Block.objects.filter(location=location_pk)
        
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
        location = get_object_or_404(Location, pk=location_pk)
        serializer.save(location=location)

    def retrieve(self, request, pk=None, location_pk=None):
        queryset = Block.objects.filter(location=location_pk)
        block = get_object_or_404(queryset, pk=pk)
        serializer = BlockSerializer(block)
        return Response(serializer.data)


class CheViewSet(viewsets.ViewSet):
    serializer_class = CheSerializer
    permission_classes = [permissions.IsAuthenticated]

    def list(self, request, location_pk=None, che_pk=None):
        queryset = Che.objects.filter(location=location_pk)
        serializer = CheSerializer(queryset, many=True)
        return Response(serializer.data)

    def retrieve(self, request, pk=None, *args, **kwargs):
        queryset = Che.objects.filter(pk=pk)
        block = get_object_or_404(queryset, pk=pk)
        serializer = CheSerializer(block)
        return Response(serializer.data)

    def partial_update(self, request, pk=None, *args, **kwargs):
        print(request.data)
        queryset = Che.objects.filter(pk=pk)
        che = get_object_or_404(queryset, pk=pk)
        if "x_position" in request.data:
            che.x_position = request.data["x_position"]
        if "y_position" in request.data:
            che.y_position = request.data["y_position"]
        che.status = request.data["status"]

        queryset2 = Block.objects.filter(pk=request.data["current_block"], location=request.data["location"])
        print(queryset2)
        cblock = get_object_or_404(queryset2, pk=request.data["current_block"])
        print(cblock)
        che.current_block = cblock
        queryset3 = User.objects.filter(pk=request.data["current_user"])
        cuser = get_object_or_404(queryset3, pk=request.data["current_user"])
        che.current_user = cuser
        che.twistlock_closed = request.data["twistlock_closed"]
        che.save()

        serializer = CheSerializer(che, data=request.data, partial=True)

        if serializer.is_valid():
            print(serializer.data)
            return Response(serializer.data)
        else:
            print("Serializer error:")
            print(serializer.errors)
            return Response(serializer.errors, status=400)


class PileViewSet(viewsets.ViewSet):
    serializer_class = PileSerializer
    permission_classes = [permissions.IsAuthenticated]
    filterset_fields = ['bay', 'row']
    filter_backends = [filters.DjangoFilterBackend]

    def list(self, request, location_pk=None, block_pk=None, *args, **kwargs):

        bay = self.request.query_params.get('bay')
        row = self.request.query_params.get('row')

        if bay is not None and row is not None:
            queryset = Pile.objects.filter(block__location=location_pk, block=block_pk, bay=bay, row=row)
        elif bay is not None:
            queryset = Pile.objects.filter(block__location=location_pk, block=block_pk, bay=bay)
        elif row is not None:
            queryset = Pile.objects.filter(block__location=location_pk, block=block_pk, row=row)
        else:
            queryset = Pile.objects.filter(block__location=location_pk, block=block_pk)
        # piles = get_object_or_404(queryset, many=True)
        serializer = PileSerializer(queryset, many=True)
        return Response(serializer.data)

    def retrieve(self, request, pk=None, block_pk=None, location_pk=None):
        queryset = Pile.objects.filter(block__location=location_pk, block=block_pk)
        pile = get_object_or_404(queryset, pk=pk)
        serializer = PileSerializer(pile)
        return Response(serializer.data)

    def partial_update(self, request, pk=None, *args, **kwargs):
        print(request.data)
        queryset = Pile.objects.filter(pk=pk)
        pile = get_object_or_404(queryset, pk=pk)
        pile.x_position = request.data["x_position"]
        pile.y_position = request.data["y_position"]
        pile.save()

        serializer = PileSerializer(pile, data=request.data, partial=True)
        if serializer.is_valid():
            return Response(serializer.data)
        else:
            return Response(serializer.errors, status=400)


class PositionViewSet(viewsets.ModelViewSet):
    serializer_class = PositionSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        location_pk = self.kwargs.get('location_pk')
        block_pk = self.kwargs.get('block_pk')
        pile_pk = self.kwargs.get('pile_pk')
        return Position.objects.filter(pile__block__location=location_pk, pile__block=block_pk, pile=pile_pk)

    def list(self, request, location_pk=None, block_pk=None, pile_pk=None):
        queryset = self.get_queryset()
        serializer = PositionSerializer(queryset, many=True)
        return Response(serializer.data)

    def retrieve(self, request, pk=None, location_pk=None, block_pk=None, pile_pk=None):
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
        print(request.data)
        queryset = WorkQueue.objects.filter(pk=pk)
        work_queue = get_object_or_404(queryset, pk=pk)
        work_queue.x_position = request.data["x_position"]
        work_queue.y_position = request.data["y_position"]
        work_queue.save()

        serializer = WorkQueueSerializer(work_queue, data=request.data, partial=True)

        if serializer.is_valid():
            return Response(serializer.data)
        else:
            return Response(serializer.errors, status=400)


class OrderViewSet(viewsets.ViewSet):
    serializer_class = OrderSerializer
    permission_classes = [permissions.IsAuthenticated]

    def list(self, request, work_queue_pk=None):
        queryset = Order.objects.filter(work_queue=work_queue_pk)
        serializer = OrderSerializer(queryset, many=True)
        return Response(serializer.data)

    def retrieve(self, request, pk=None, work_queue_pk=None):
        queryset = Order.objects.filter(pk=pk, work_queue=work_queue_pk)
        order = get_object_or_404(queryset, pk=pk)
        serializer = OrderSerializer(order)
        return Response(serializer.data)


class OrderLegViewSet(viewsets.ViewSet):
    serializer_class = OrderLegSerializer
    permission_classes = [permissions.IsAuthenticated]

    def list(self, request, work_queue_pk=None, order_pk=None):
        queryset = OrderLeg.objects.filter(order__work_queue=work_queue_pk, order=order_pk)
        serializer = OrderLegSerializer(queryset, many=True)
        return Response(serializer.data)

    def retrieve(self, request, pk=None, work_queue_pk=None, order_pk=None):
        queryset = OrderLeg.objects.filter(pk=pk, order=order_pk, order__work_queue=work_queue_pk)
        order = get_object_or_404(queryset, pk=pk)
        serializer = OrderLegSerializer(order)
        return Response(serializer.data)


##################################################################

#### CarrierVisit Views
class CarrierVisitViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing transport visits (vessels, trucks, trains, barges).
    """
    queryset = CarrierVisit.objects.all().order_by('-eta')
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
    queryset = CarrierService.objects.all().order_by('name')
    serializer_class = CarrierServiceSerializer
    permission_classes = [permissions.IsAuthenticated]
    filterset_fields = ['repeat', 'periodicity']
    filter_backends = [filters.DjangoFilterBackend]


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


class POWConnectionViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing POW connections.
    """
    queryset = POWConnection.objects.all().order_by('from_pow__code', 'to_pow__code')
    serializer_class = POWConnectionSerializer
    permission_classes = [permissions.IsAuthenticated]
    filterset_fields = ['from_pow', 'to_pow', 'is_enabled', 'is_bidirectional']
    filter_backends = [filters.DjangoFilterBackend]


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

    visit_manager = VisitStateManager()
    success, message = visit_manager.transition_visit_status(visit, new_status, request.user)

    if success:
        # Return updated visit
        serializer = VisitSerializer(visit)
        return Response({
            'success': True,
            'message': message,
            'visit': serializer.data
        })
    else:
        return Response({
            'success': False,
            'message': message
        }, status=status.HTTP_400_BAD_REQUEST)


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

    visit_manager = VisitStateManager()
    success, message = visit_manager.transition_container_status(container_visit, new_status, request.user)

    if success:
        # Return updated container
        serializer = ContainerVisitSerializer(container_visit)
        return Response({
            'success': True,
            'message': message,
            'container_visit': serializer.data
        })
    else:
        return Response({
            'success': False,
            'message': message
        }, status=status.HTTP_400_BAD_REQUEST)


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

