from django.contrib.auth.models import User, Group
from django.db import models, transaction
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers, generics
from . models import Location, Block, Pile, Position, ContainerUnit, ContainerVisit, ISOType, ContainerOwner, Che, EquipmentType, WorkQueue, Order, OrderLeg, CarrierVisit, CarrierService, ServiceCargoRoute, VisitCargoRoute, PileBooking, PointOfWork, POWConnection, Area, Road, RoutingNode, ParkingArea, TerminalConnection
from .services import sync_yard_grid


####  Position for container view
class PositionSerializerShort(serializers.ModelSerializer):

    class Meta:
        model = Position
        fields = ['pk', '__str__','pile', 'is_reserved','is_occupied','tier']
        
        
class ContainerVisitSerializerShort(serializers.ModelSerializer):
    
    class Meta:
        model = ContainerVisit
        fields = ['pk', 'category', 'container_number', 'ib_carrier', 'ob_carrier', 'location', 'eta', 'etd', 'weight', 'pod', 'pod2', 'requires_power', 'is_imdg', 'x_position', 'y_position', 'z_position']
                
        
####  Position
class PositionSerializer(serializers.ModelSerializer):
    container = ContainerVisitSerializerShort(many=True, read_only=True)
    
    def get_formatted_str(self, obj):
        block_name = obj.pile.block.name
        bay = obj.pile.get_bay_name()
        row = obj.pile.get_row_name()
        tier = f"{obj.tier:02d}"
        return f"{block_name}.{bay}.{row}.{tier}"

    __str__ = serializers.SerializerMethodField(method_name='get_formatted_str')

    class Meta:
        model = Position
        fields = ['pk', '__str__', 'container_visit_id', 'is_reserved', 'is_occupied', 'tier', 'container']
       
    def partial_update(self, instance, validated_data):
        
        position = Position.objects.get(pk=instance.id)
        Position.objects.filter(pk=instance.id)\
                           .update(**validated_data)
        if position.container is Null:
            position.is_occupied = True
        else:
            position.is_occupied = False
        return position
        
####  Pile
class PileSerializer(serializers.ModelSerializer):

    
    def __init__(self, *args, **kwargs):
        kwargs['partial'] = True
        super(PileSerializer, self).__init__(*args, **kwargs)

    positions = PositionSerializer(many=True, read_only=True)
    
    def get_formatted_str(self, obj):
        block_name = obj.block.name
        bay = obj.get_bay_name()
        row = obj.get_row_name()
        return f"{block_name}.{bay}.{row}"

    def get_bay_name(self, obj):
        return obj.get_bay_name()

    def get_row_name(self, obj):
        return obj.get_row_name()

    __str__ = serializers.SerializerMethodField(method_name='get_formatted_str')
    bay_name = serializers.SerializerMethodField(method_name='get_bay_name')
    row_name = serializers.SerializerMethodField(method_name='get_row_name')
    block_name = serializers.CharField(source='block.name', read_only=True)

    class Meta:
        model = Pile
        fields = [
            'pk', 'block', 'block_name', 'bay', 'bay_name', 'row', 'row_name', 'tiers',
            'allows_in', 'allows_out', 'is_restricted', 'allows_20', 'allows_40', 'allows_45',
            'x_position', 'y_position', '__str__', 'positions'
        ]
        
    def partial_update(self, instance, validated_data):
        
        pile = Pile.objects.get(pk=instance.id)
        Pile.objects.filter(pk=instance.id)\
                           .update(**validated_data)
        return pile
        
####  Block
class BlockSerializer(serializers.ModelSerializer):
    location_name = serializers.CharField(source='location.name', read_only=True)
    block_type_display = serializers.CharField(source='get_block_type_display', read_only=True)
    piles = PileSerializer(many=True, read_only=True)
    
    # Statistics fields
    total_piles = serializers.IntegerField(read_only=True)
    total_positions_20 = serializers.IntegerField(read_only=True)
    total_positions_40 = serializers.IntegerField(read_only=True)
    total_positions_45 = serializers.IntegerField(read_only=True)
    free_positions_20 = serializers.IntegerField(read_only=True)
    free_positions_40 = serializers.IntegerField(read_only=True)
    free_positions_45 = serializers.IntegerField(read_only=True)

    class Meta:
        model = Block
        fields = ['pk', 'name', 'location', 'location_name', 'block_type', 'block_type_display',
                 'max_bays', 'max_rows', 'max_tier', 'x_position', 'y_position',
                 'anchor_lat', 'anchor_lon', 'heading_deg', 'slot_heading_deg',
                 'pile_length_m', 'pile_width_m', 'pile_height_m',
                 'initial_bay', 'initial_row', 'bay_order', 'row_order',
                 'bay_naming', 'row_naming', 'bay_use_heading_axis',
                 'padding_top_m', 'padding_bottom_m', 'padding_left_m', 'padding_right_m',
                 'bay_spacing_m', 'row_spacing_m', 'is_heapzone',
                 'total_piles', 'total_positions_20', 'total_positions_40', 'total_positions_45',
                 'free_positions_20', 'free_positions_40', 'free_positions_45', 'piles']
        extra_kwargs = {'location': {'required': False}}

    @transaction.atomic
    def create(self, validated_data):
        block = super().create(validated_data)
        try:
            return sync_yard_grid(block)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.messages)

    @transaction.atomic
    def update(self, instance, validated_data):
        block = super().update(instance, validated_data)
        try:
            return sync_yard_grid(block)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.messages)
        
####  Che
class CheSerializer(serializers.ModelSerializer):
    equipment_type_name = serializers.CharField(source='equipment_type.name', read_only=True)
    equipment_type_code = serializers.CharField(source='equipment_type.equipment_type', read_only=True)
    location_name = serializers.CharField(source='location.name', read_only=True)
    current_block_name = serializers.CharField(source='current_block.name', read_only=True, allow_null=True)
    current_user_name = serializers.CharField(source='current_user.username', read_only=True, allow_null=True)

    def __init__(self, *args, **kwargs):
        kwargs['partial'] = True
        super(CheSerializer, self).__init__(*args, **kwargs)

    cblock = BlockSerializer(many=False, read_only=True)
    class Meta:
        model = Che
        fields = [
            'pk', 'status', 'name', 'equipment_type', 'equipment_type_name', 'equipment_type_code',
            'current_block', 'current_block_name', 'current_user', 'current_user_name',
            'location', 'location_name', 'twistlock_closed', 'x_position', 'y_position', 
            'fuel_level', 'last_maintenance', 'cblock'
        ]
        
    def partial_update(self, instance, validated_data):
        
        che = Che.objects.get(pk=instance.id)
        Che.objects.filter(pk=instance.id)\
                           .update(**validated_data)
        return che

class UserSerializer(serializers.HyperlinkedModelSerializer):
    
    class Meta:
        model = User
        fields = ['url', 'username', 'email', 'groups']

class GroupSerializer(serializers.HyperlinkedModelSerializer):
    class Meta:
        model = Group
        fields = ['url', 'name']
        
class ContainerUnitSerializer(serializers.ModelSerializer):
    class Meta:
        model = ContainerUnit
        fields = ['iso_type', 'owner', 'container_number', 'is_active', 'is_in_terminal']

class ContainerVisitSerializer(serializers.ModelSerializer):
    position = PositionSerializerShort(many=False, read_only=True)
    container_number_name = serializers.CharField(source='container_number.container_number', read_only=True)
    location_name = serializers.CharField(source='location.name', read_only=True)
    pod_name = serializers.CharField(source='pod.name', read_only=True, allow_null=True)
    pod2_name = serializers.CharField(source='pod2.name', read_only=True, allow_null=True)
    ib_carrier_name = serializers.CharField(source='ib_carrier.name', read_only=True, allow_null=True)
    ob_carrier_name = serializers.CharField(source='ob_carrier.name', read_only=True, allow_null=True)
    category_display = serializers.CharField(source='get_category_display', read_only=True)

    class Meta:
        model = ContainerVisit
        fields = [
            'pk', 'category', 'category_display', 'container_number', 'container_number_name',
            'ib_carrier', 'ib_carrier_name', 'ob_carrier', 'ob_carrier_name',
            'location', 'location_name', 'position', 'eta', 'etd', 'weight',
            'pod', 'pod_name', 'pod2', 'pod2_name', 'requires_power', 'is_imdg',
            'x_position', 'y_position', 'z_position', 'bill_of_lading', 'booking'
        ]

class OrderLegSerializer(serializers.ModelSerializer):
    class Meta:
        model = OrderLeg
        fields = ['order', 'che','start_position','end_position']

class OrderSerializer(serializers.ModelSerializer):
    order_legs = OrderLegSerializer(many=True, read_only=True)
    class Meta:
        model = Order
        fields = ['order_type', 'container_number', 'container_number', 'work_queue', 'etc', 'atc', 'from_position', 'to_position', 'order_legs']
        
class WorkQueueSerializer(serializers.ModelSerializer):
    orders = OrderSerializer(many=True, read_only=True)
    
    class Meta:
        model = WorkQueue
        fields = ['name', 'is_active', 'description', 'current_user','orders']

class UserSerializer(serializers.ModelSerializer):
    work_queue = serializers.PrimaryKeyRelatedField(many=False, queryset=WorkQueue.objects.all())

    class Meta:
        model = User
        fields = ['id', 'username', 'work_queue']


#### CarrierVisit Serializers

class CarrierVisitSerializer(serializers.ModelSerializer):
    location_name = serializers.CharField(source='location.name', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    visit_type_display = serializers.CharField(source='get_visit_type_display', read_only=True)
    container_count = serializers.SerializerMethodField()

    def get_container_count(self, obj):
        # Count containers where this visit is ib_carrier or ob_carrier
        return ContainerVisit.objects.filter(
            models.Q(ib_carrier=obj) | models.Q(ob_carrier=obj)
        ).count()

    class Meta:
        model = CarrierVisit
        fields = [
            'id', 'name', 'visit_type', 'visit_type_display', 'status', 'status_display',
            'location', 'location_name', 'eta', 'etd', 'ata', 'atd', 'vessel_name',
            'voyage_number', 'carrier', 'berth', 'container_count',
            'created_at', 'last_updated'
        ]
        read_only_fields = ['created_at', 'last_updated', 'container_count']


#### Bulk Container Creation Serializer
class BulkContainerVisitSerializer(serializers.Serializer):
    containers = serializers.ListField(
        child=serializers.DictField(),
        allow_empty=False
    )

    def create(self, validated_data):
        containers_data = validated_data['containers']
        created_visits = []

        for container_data in containers_data:
            # Extract fields
            container_number = container_data.get('container_number')
            iso_type_code = container_data.get('iso_type', '2200')  # Default
            owner_code = container_data.get('owner', 'DEFAULT')  # Default
            category = container_data.get('category', 'I')
            location_id = container_data.get('location')
            weight = container_data.get('weight', 0)
            bill_of_lading = container_data.get('bill_of_lading')
            booking = container_data.get('booking')
            ib_carrier_id = container_data.get('ib_carrier')
            ob_carrier_id = container_data.get('ob_carrier')
            pod_id = container_data.get('pod')
            eta = container_data.get('eta')
            etd = container_data.get('etd')

            # Get or create ContainerUnit
            iso_type, _ = ISOType.objects.get_or_create(
                iso_type=iso_type_code,
                defaults={'length': iso_type_code[0], 'height': 'NC'}
            )
            owner, _ = ContainerOwner.objects.get_or_create(
                code=owner_code,
                defaults={'name': owner_code}
            )
            container_unit, _ = ContainerUnit.objects.get_or_create(
                container_number=container_number,
                defaults={'iso_type': iso_type, 'owner': owner}
            )

            # Create ContainerVisit
            container_visit = ContainerVisit.objects.create(
                container_number=container_unit,
                category=category,
                location_id=location_id,
                weight=weight,
                bill_of_lading=bill_of_lading,
                booking=booking,
                ib_carrier_id=ib_carrier_id,
                ob_carrier_id=ob_carrier_id,
                pod_id=pod_id,
                eta=eta,
                etd=etd,
                status=ContainerVisit.PREADVISED
            )
            created_visits.append(container_visit)

        return created_visits


class CarrierServiceSerializer(serializers.ModelSerializer):
    class Meta:
        model = CarrierService
        fields = '__all__'

    def get_fields(self):
        fields = super().get_fields()
        request = self.context.get('request', None)
        instance = self.instance

        # Skip dynamic filtering for list views where instance is a list/queryset
        if isinstance(instance, (list, tuple)):
            return fields

        # Get the repeat value from the request data or instance
        repeat_value = None
        if request and request.method in ['POST', 'PUT', 'PATCH']:
            repeat_value = request.data.get('repeat')
        elif instance:
            repeat_value = instance.repeat

        # Define which fields to show for each repeat type
        base_fields = [
            'id', 'name', 'start_date', 'duration', 'carrier_service_type', 'end_date', 'end_type', 'end_after_occurrences',
            'repeat', 'window_start_time', 'window_end_time', 'window_start_weekday',
            'window_end_weekday', 'window_start_monthday', 'window_end_monthday',
            'moves_in', 'moves_out', 'periodicity'
        ]

        # Add repeat-specific fields
        if repeat_value == 'Daily':
            base_fields.extend(['repeat_daily'])
        elif repeat_value == 'Weekly':
            base_fields.extend(['repeat_weekly', 'on_weekdays'])
        elif repeat_value == 'Monthly':
            base_fields.extend(['repeat_monthly', 'on_monthly_pattern'])
            # Add pattern-specific fields
            if request and request.method in ['POST', 'PUT', 'PATCH']:
                pattern = request.data.get('on_monthly_pattern')
            elif instance:
                pattern = instance.on_monthly_pattern
            else:
                pattern = None

            if pattern == 'Day':
                base_fields.append('on_monthly_day')
            elif pattern in ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']:
                base_fields.append('on_monthly_weekday')
        elif repeat_value == 'Yearly':
            base_fields.extend(['repeat_yearly', 'on_monthly_pattern'])
            # Add pattern-specific fields
            if request and request.method in ['POST', 'PUT', 'PATCH']:
                pattern = request.data.get('on_monthly_pattern')
            elif instance:
                pattern = instance.on_monthly_pattern
            else:
                pattern = None

            if pattern == 'Day':
                base_fields.append('on_monthly_day')
            elif pattern in ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']:
                base_fields.append('on_monthly_weekday')

        # Filter fields to only include relevant ones
        filtered_fields = {}
        for field_name in base_fields:
            if field_name in fields:
                filtered_fields[field_name] = fields[field_name]

        return filtered_fields


class ServiceCargoRouteSerializer(serializers.ModelSerializer):
    inbound_carrier_service_name = serializers.CharField(source='inbound_carrier_service.name', read_only=True)
    outbound_carrier_service_name = serializers.CharField(source='outbound_carrier_service.name', read_only=True)
    pod_name = serializers.CharField(source='pod.name', read_only=True)
    iso_type_code = serializers.CharField(source='iso_type.iso_type', read_only=True)

    class Meta:
        model = ServiceCargoRoute
        fields = [
            'id', 'inbound_carrier_service', 'inbound_carrier_service_name',
            'outbound_carrier_service', 'outbound_carrier_service_name',
            'pod', 'pod_name', 'container_type', 'iso_type', 'iso_type_code',
            'hazard_codes', 'is_reefer', 'container_count', 'expected_dwell_time_days'
        ]


class VisitCargoRouteSerializer(serializers.ModelSerializer):
    inbound_carrier_visit_name = serializers.CharField(source='inbound_carrier_visit.name', read_only=True)
    outbound_carrier_visit_name = serializers.CharField(source='outbound_carrier_visit.name', read_only=True, allow_null=True)
    outbound_carrier_service_name = serializers.CharField(source='outbound_carrier_service.name', read_only=True)
    pod_name = serializers.CharField(source='pod.name', read_only=True)
    iso_type_code = serializers.CharField(source='iso_type.iso_type', read_only=True)

    class Meta:
        model = VisitCargoRoute
        fields = [
            'id', 'inbound_carrier_visit', 'inbound_carrier_visit_name',
            'outbound_carrier_visit', 'outbound_carrier_visit_name',
            'outbound_carrier_service', 'outbound_carrier_service_name',
            'pod', 'pod_name', 'container_type', 'iso_type', 'iso_type_code',
            'hazard_codes', 'is_reefer', 'container_count', 'expected_dwell_time_days'
        ]


class PileBookingSerializer(serializers.ModelSerializer):
    pile_name = serializers.CharField(source='pile.__str__', read_only=True)
    visit_cargo_route_display = serializers.CharField(source='visit_cargo_route.__str__', read_only=True)

    class Meta:
        model = PileBooking
        fields = [
            'id', 'pile', 'pile_name', 'visit_cargo_route', 'visit_cargo_route_display',
            'tier_limit', 'start_booking_time', 'end_booking_time'
        ]


class PointOfWorkSerializer(serializers.ModelSerializer):
    location_name = serializers.CharField(source='location.name', read_only=True)
    related_ches_names = serializers.SerializerMethodField()
    pow_type_display = serializers.CharField(source='get_pow_type_display', read_only=True)

    def get_related_ches_names(self, obj):
        return [che.name for che in obj.related_ches.all()]

    class Meta:
        model = PointOfWork
        fields = [
            'id', 'name', 'pow_type', 'pow_type_display', 'related_ches', 'related_ches_names',
            'location', 'location_name', 'code', 'x_position', 'y_position',
            'max_throughput', 'is_active'
        ]


class POWConnectionSerializer(serializers.ModelSerializer):
    from_pow_name = serializers.CharField(source='from_pow.name', read_only=True)
    from_pow_code = serializers.CharField(source='from_pow.code', read_only=True)
    to_pow_name = serializers.CharField(source='to_pow.name', read_only=True)
    to_pow_code = serializers.CharField(source='to_pow.code', read_only=True)

    class Meta:
        model = POWConnection
        fields = [
            'id', 'from_pow', 'from_pow_name', 'from_pow_code',
            'to_pow', 'to_pow_name', 'to_pow_code',
            'distance', 'max_throughput', 'is_bidirectional',
            'cost', 'is_enabled'
        ]


class AreaSerializer(serializers.ModelSerializer):
    location_name = serializers.CharField(source='location.name', read_only=True)
    area_type_display = serializers.CharField(source='get_area_type_display', read_only=True)
    yard_grid = serializers.JSONField(write_only=True, required=False)
    block_config = serializers.SerializerMethodField()
    parking_settings = serializers.JSONField(write_only=True, required=False)
    parking_config = serializers.SerializerMethodField()

    def get_block_config(self, obj):
        if not obj.block_id:
            return None
        return BlockSerializer(obj.block, context=self.context).data

    def get_parking_config(self, obj):
        try:
            parking = obj.parking_config
        except ParkingArea.DoesNotExist:
            return None
        return ParkingAreaSerializer(parking, context=self.context).data

    def _save_yard_block(self, area, grid):
        if area.area_type != Area.YARD_BLOCK:
            if area.block_id:
                block = area.block
                area.block = None
                area.save(update_fields=['block'])
                block.delete()
            return area

        if not grid and area.block_id:
            return area
        if not grid:
            raise serializers.ValidationError({'yard_grid': 'Yard block configuration is required.'})

        grid = dict(grid)
        grid['name'] = area.name
        grid['location'] = area.location_id
        serializer = BlockSerializer(
            instance=area.block if area.block_id else None,
            data=grid,
            context=self.context,
        )
        serializer.is_valid(raise_exception=True)
        block = serializer.save()
        if area.block_id != block.pk:
            area.block = block
            area.save(update_fields=['block'])
        return area

    def _save_parking(self, area, settings):
        try:
            parking = area.parking_config
        except ParkingArea.DoesNotExist:
            parking = None
        if area.area_type != Area.PARKING:
            if parking:
                parking.delete()
            return area
        if settings is None and parking:
            return area
        settings = settings or {}
        allowed = settings.pop('allowed_che_types', [])
        if parking is None:
            parking = ParkingArea.objects.create(area=area, **settings)
        else:
            for key, value in settings.items():
                setattr(parking, key, value)
            parking.save()
        parking.allowed_che_types.set(allowed)
        return area

    @transaction.atomic
    def create(self, validated_data):
        grid = validated_data.pop('yard_grid', None)
        parking = validated_data.pop('parking_settings', None)
        area = super().create(validated_data)
        self._save_yard_block(area, grid)
        return self._save_parking(area, parking)

    @transaction.atomic
    def update(self, instance, validated_data):
        grid = validated_data.pop('yard_grid', None)
        parking = validated_data.pop('parking_settings', None)
        area = super().update(instance, validated_data)
        self._save_yard_block(area, grid)
        return self._save_parking(area, parking)

    class Meta:
        model = Area
        fields = [
            'id', 'location', 'location_name', 'name', 'area_type', 'area_type_display',
            'area', 'is_visible', 'coordinates', 'yard_grid', 'block_config',
            'parking_settings', 'parking_config'
        ]


class EquipmentTypeSerializer(serializers.ModelSerializer):
    class Meta:
        model = EquipmentType
        fields = ['id', 'name', 'equipment_type']


class RoutingNodeSerializer(serializers.ModelSerializer):
    class Meta:
        model = RoutingNode
        fields = ['id', 'location', 'name', 'x_position', 'y_position']


class RoadSerializer(serializers.ModelSerializer):
    allowed_che_type_details = EquipmentTypeSerializer(
        source='allowed_che_types', many=True, read_only=True
    )
    start_node_details = RoutingNodeSerializer(source='start_node', read_only=True)
    end_node_details = RoutingNodeSerializer(source='end_node', read_only=True)

    class Meta:
        model = Road
        fields = [
            'id', 'location', 'name', 'points', 'lane_count', 'capacity', 'direction',
            'allowed_che_types', 'allowed_che_type_details', 'start_node', 'end_node',
            'start_node_details', 'end_node_details', 'is_enabled'
        ]
        extra_kwargs = {
            'location': {'required': False},
            'start_node': {'read_only': True},
            'end_node': {'read_only': True},
        }

    def validate_points(self, value):
        if not isinstance(value, list) or len(value) < 2:
            raise serializers.ValidationError('A road requires at least two points.')
        for point in value:
            if not isinstance(point, list) or len(point) < 2:
                raise serializers.ValidationError('Every road point must be [x, y].')
            try:
                float(point[0])
                float(point[1])
            except (TypeError, ValueError):
                raise serializers.ValidationError('Road point coordinates must be numeric.')
        return value

    def _sync_endpoint_nodes(self, road):
        first, last = road.points[0], road.points[-1]
        if road.start_node_id is None:
            road.start_node = RoutingNode.objects.create(
                location=road.location, name=f'{road.name} start',
                x_position=first[0], y_position=first[1]
            )
        else:
            road.start_node.x_position, road.start_node.y_position = first[0], first[1]
            road.start_node.name = f'{road.name} start'
            road.start_node.save(update_fields=['name', 'x_position', 'y_position'])
        if road.end_node_id is None:
            road.end_node = RoutingNode.objects.create(
                location=road.location, name=f'{road.name} end',
                x_position=last[0], y_position=last[1]
            )
        else:
            road.end_node.x_position, road.end_node.y_position = last[0], last[1]
            road.end_node.name = f'{road.name} end'
            road.end_node.save(update_fields=['name', 'x_position', 'y_position'])
        road.save(update_fields=['start_node', 'end_node'])
        return road

    @transaction.atomic
    def create(self, validated_data):
        return self._sync_endpoint_nodes(super().create(validated_data))

    @transaction.atomic
    def update(self, instance, validated_data):
        return self._sync_endpoint_nodes(super().update(instance, validated_data))


class ParkingAreaSerializer(serializers.ModelSerializer):
    allowed_che_type_details = EquipmentTypeSerializer(
        source='allowed_che_types', many=True, read_only=True
    )

    class Meta:
        model = ParkingArea
        fields = ['id', 'area', 'capacity', 'allowed_che_types', 'allowed_che_type_details']
        extra_kwargs = {'area': {'read_only': True}}


class TerminalConnectionSerializer(serializers.ModelSerializer):
    class Meta:
        model = TerminalConnection
        fields = ['id', 'node', 'block', 'parking_area', 'is_bidirectional', 'cost', 'is_enabled']

    def validate(self, attrs):
        block = attrs.get('block', getattr(self.instance, 'block', None))
        parking = attrs.get('parking_area', getattr(self.instance, 'parking_area', None))
        node = attrs.get('node', getattr(self.instance, 'node', None))
        if bool(block) == bool(parking):
            raise serializers.ValidationError('Select exactly one block or parking area.')
        target_location = block.location_id if block else parking.area.location_id
        if node and node.location_id != target_location:
            raise serializers.ValidationError('The node and connected asset must belong to the same terminal.')
        return attrs


####  Location
class LocationSerializer(serializers.ModelSerializer):
    blocks = BlockSerializer(many=True, read_only=True)
    ches = CheSerializer(many=True, read_only=True)
    areas = AreaSerializer(many=True, read_only=True)

    class Meta:
        model = Location
        fields = [
            'pk', 'name', 'location_type',
            'latitude', 'longitude', 'map_origin_latitude', 'map_origin_longitude',
            'map_width_m', 'map_height_m', 'map_units_per_meter',
            'default_zoom', 'angle',
            'blocks', 'ches', 'areas'
        ]
