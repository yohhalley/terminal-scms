from django.contrib.auth.models import User, Group
from django.db import models, transaction
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers, generics
from django.utils import timezone as django_timezone
from zoneinfo import ZoneInfo
from . models import Location, Block, Pile, Position, ContainerUnit, ContainerVisit, ISOType, ContainerOwner, Che, EquipmentType, WorkQueue, Order, OrderLeg, CarrierVisit, CarrierService, CarrierServiceEquipmentRequirement, CarrierVisitEquipmentRequirement, ServiceCargoRoute, VisitCargoRoute, PileBooking, PointOfWork, POWConnection, Area, Road, RoutingNode, ParkingArea, TerminalConnection, Berth, Bollard, ProcessHierarchy, BusinessProcess, ProcessVersion, BusinessProcessExecution, Battery, CheComponent, ComponentType, CurrentSignalValue, EnergyTank, Engine, EquipmentTypeComponent, HandlingCycle, MotionAxis, OperationalStateInterval, SignalBinding, SignalDefinition, Spreader
from .services import berth_polygon, sync_berth_bollards, sync_yard_grid, vessel_end_bollard


####  Position for container view
class PositionSerializerShort(serializers.ModelSerializer):

    class Meta:
        model = Position
        fields = ['pk', '__str__','pile', 'is_reserved','is_occupied','tier']
        
        
class ContainerVisitSerializerShort(serializers.ModelSerializer):
    
    class Meta:
        model = ContainerVisit
        fields = ['pk', 'category', 'container_number', 'ib_carrier', 'ob_carrier', 'location', 'eta', 'etd', 'weight', 'pod', 'pod2', 'requires_power', 'is_imdg', 'latitude', 'longitude', 'z_position']
                
        
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
            'latitude', 'longitude', '__str__', 'positions'
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
                 'max_bays', 'max_rows', 'max_tier', 'latitude', 'longitude',
                 'heading_deg', 'slot_heading_deg',
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
        
class ComponentTypeSerializer(serializers.ModelSerializer):
    class Meta:
        model = ComponentType
        fields = [
            'id', 'code', 'name', 'tic40_subject', 'description',
            'is_motion_component', 'is_energy_component', 'supports_multiple',
        ]


class SignalDefinitionSerializer(serializers.ModelSerializer):
    component_type_details = ComponentTypeSerializer(source='component_type', read_only=True)

    class Meta:
        model = SignalDefinition
        fields = [
            'id', 'component_type', 'component_type_details', 'code', 'name',
            'tic40_path', 'data_type', 'unit', 'value_kind', 'standard_version',
            'description', 'is_required_for_oee',
        ]


class EquipmentTypeComponentSerializer(serializers.ModelSerializer):
    equipment_type = serializers.PrimaryKeyRelatedField(read_only=True)
    component_type_details = ComponentTypeSerializer(source='component_type', read_only=True)

    class Meta:
        model = EquipmentTypeComponent
        fields = [
            'id', 'equipment_type', 'component_type', 'component_type_details',
            'role', 'display_name', 'parent_role', 'is_required', 'is_active',
            'quantity', 'default_configuration',
        ]


class EngineSerializer(serializers.ModelSerializer):
    class Meta:
        model = Engine
        exclude = ['component']


class BatterySerializer(serializers.ModelSerializer):
    class Meta:
        model = Battery
        exclude = ['component']

    def validate(self, attrs):
        attrs = super().validate(attrs)
        minimum = attrs.get(
            'minimum_soc_percent', getattr(self.instance, 'minimum_soc_percent', 0)
        )
        maximum = attrs.get(
            'maximum_soc_percent', getattr(self.instance, 'maximum_soc_percent', 100)
        )
        if minimum > maximum:
            raise serializers.ValidationError({
                'maximum_soc_percent': 'Maximum SOC must be at least the minimum SOC.'
            })
        return attrs


class EnergyTankSerializer(serializers.ModelSerializer):
    class Meta:
        model = EnergyTank
        exclude = ['component']


class MotionAxisSerializer(serializers.ModelSerializer):
    class Meta:
        model = MotionAxis
        exclude = ['component']


class SpreaderSerializer(serializers.ModelSerializer):
    class Meta:
        model = Spreader
        exclude = ['component']


class CurrentSignalValueSerializer(serializers.ModelSerializer):
    binding = serializers.PrimaryKeyRelatedField(read_only=True)
    class Meta:
        model = CurrentSignalValue
        fields = [
            'id', 'binding', 'value', 'source_timestamp', 'recorded_at',
            'quality', 'sequence',
        ]
        read_only_fields = ['recorded_at']


class SignalBindingSerializer(serializers.ModelSerializer):
    component = serializers.PrimaryKeyRelatedField(read_only=True)
    signal_definition_details = SignalDefinitionSerializer(source='signal_definition', read_only=True)
    current_value = CurrentSignalValueSerializer(read_only=True)

    class Meta:
        model = SignalBinding
        fields = [
            'id', 'component', 'signal_definition', 'signal_definition_details',
            'source_protocol', 'source_metric', 'source_topic', 'scale_factor',
            'offset', 'is_enabled', 'current_value',
        ]

    def validate(self, attrs):
        attrs = super().validate(attrs)
        component = attrs.get(
            'component', getattr(self.instance, 'component', self.context.get('component'))
        )
        definition = attrs.get(
            'signal_definition', getattr(self.instance, 'signal_definition', None)
        )
        if component and definition and component.component_type_id != definition.component_type_id:
            raise serializers.ValidationError({
                'signal_definition': 'The signal is not valid for this component type.'
            })
        return attrs


class CheComponentSerializer(serializers.ModelSerializer):
    che = serializers.PrimaryKeyRelatedField(read_only=True)
    component_type_details = ComponentTypeSerializer(source='component_type', read_only=True)
    engine = EngineSerializer(required=False)
    battery = BatterySerializer(required=False)
    energy_tank = EnergyTankSerializer(required=False)
    motion_axis = MotionAxisSerializer(required=False)
    spreader = SpreaderSerializer(required=False)
    signal_bindings = SignalBindingSerializer(many=True, read_only=True)

    DETAIL_FIELDS = {
        'engine': Engine,
        'battery': Battery,
        'energy_tank': EnergyTank,
        'motion_axis': MotionAxis,
        'spreader': Spreader,
    }

    class Meta:
        model = CheComponent
        fields = [
            'id', 'che', 'component_type', 'component_type_details', 'template',
            'parent', 'role', 'name', 'manufacturer', 'model', 'serial_number',
            'installed_at', 'removed_at', 'is_active', 'metadata', 'engine',
            'battery', 'energy_tank', 'motion_axis', 'spreader', 'signal_bindings',
        ]

    def _save_details(self, component, details):
        for field_name, values in details.items():
            detail_model = self.DETAIL_FIELDS[field_name]
            detail_model.objects.update_or_create(component=component, defaults=values)

    @transaction.atomic
    def create(self, validated_data):
        details = {
            name: validated_data.pop(name)
            for name in self.DETAIL_FIELDS if name in validated_data
        }
        component = super().create(validated_data)
        self._save_details(component, details)
        return component

    @transaction.atomic
    def update(self, instance, validated_data):
        details = {
            name: validated_data.pop(name)
            for name in self.DETAIL_FIELDS if name in validated_data
        }
        component = super().update(instance, validated_data)
        self._save_details(component, details)
        return component

    def validate(self, attrs):
        attrs = super().validate(attrs)
        component_type = attrs.get('component_type', getattr(self.instance, 'component_type', None))
        detail_rules = {
            'engine': (ComponentType.POWER_SOURCE, {'engine'}),
            'battery': (ComponentType.ENERGY_STORE, {'battery'}),
            'energy_tank': (ComponentType.ENERGY_STORE, {'fuel_tank'}),
            'motion_axis': (
                None,
                {'drive', 'hoist', 'trolley', 'gantry', 'boom'},
            ),
            'spreader': (ComponentType.SPREADER, {'spreader'}),
        }
        role = attrs.get('role', getattr(self.instance, 'role', ''))
        errors = {}
        for detail_name, (expected_type, roles) in detail_rules.items():
            if detail_name not in attrs:
                continue
            if role not in roles or (
                expected_type and (component_type is None or component_type.code != expected_type)
            ):
                errors[detail_name] = f'{detail_name} details are not valid for component role {role}.'
        parent = attrs.get('parent')
        che = attrs.get(
            'che', getattr(self.instance, 'che', self.context.get('che'))
        )
        if parent and che and parent.che_id != che.pk:
            errors['parent'] = 'A component parent must belong to the same CHE.'
        if parent and self.instance and parent.pk == self.instance.pk:
            errors['parent'] = 'A component cannot be its own parent.'
        template = attrs.get('template', getattr(self.instance, 'template', None))
        if template and che and template.equipment_type_id != che.equipment_type_id:
            errors['template'] = 'The component template must belong to the CHE equipment type.'
        if template and component_type and template.component_type_id != component_type.pk:
            errors['component_type'] = 'The component type must match its template.'
        if errors:
            raise serializers.ValidationError(errors)
        return attrs


class OperationalStateIntervalSerializer(serializers.ModelSerializer):
    che = serializers.PrimaryKeyRelatedField(read_only=True)
    state_display = serializers.CharField(source='get_state_display', read_only=True)

    class Meta:
        model = OperationalStateInterval
        fields = [
            'id', 'che', 'state', 'state_display', 'started_at', 'ended_at',
            'source', 'reason', 'confidence',
        ]

    def validate(self, attrs):
        attrs = super().validate(attrs)
        started_at = attrs.get('started_at', getattr(self.instance, 'started_at', None))
        ended_at = attrs.get('ended_at', getattr(self.instance, 'ended_at', None))
        if started_at and ended_at and ended_at < started_at:
            raise serializers.ValidationError({'ended_at': 'End must not precede start.'})
        return attrs


class HandlingCycleSerializer(serializers.ModelSerializer):
    che = serializers.PrimaryKeyRelatedField(read_only=True)
    result_display = serializers.CharField(source='get_result_display', read_only=True)

    class Meta:
        model = HandlingCycle
        fields = [
            'id', 'che', 'order_leg', 'container_visit', 'started_at', 'ended_at',
            'result', 'result_display', 'ideal_cycle_seconds', 'start_latitude',
            'start_longitude', 'end_latitude', 'end_longitude', 'metadata',
        ]

    def validate(self, attrs):
        attrs = super().validate(attrs)
        started_at = attrs.get('started_at', getattr(self.instance, 'started_at', None))
        ended_at = attrs.get('ended_at', getattr(self.instance, 'ended_at', None))
        if started_at and ended_at and ended_at < started_at:
            raise serializers.ValidationError({'ended_at': 'End must not precede start.'})
        result = attrs.get('result', getattr(self.instance, 'result', HandlingCycle.IN_PROGRESS))
        if result == HandlingCycle.IN_PROGRESS and ended_at:
            raise serializers.ValidationError({
                'result': 'An ended cycle cannot remain in progress.'
            })
        if result != HandlingCycle.IN_PROGRESS and not ended_at:
            raise serializers.ValidationError({
                'ended_at': 'A completed, failed, or aborted cycle requires an end time.'
            })
        return attrs


####  Che
class CheSerializer(serializers.ModelSerializer):
    equipment_type_details = serializers.SerializerMethodField()
    equipment_type_name = serializers.CharField(source='equipment_type.name', read_only=True)
    equipment_type_code = serializers.CharField(source='equipment_type.equipment_type', read_only=True)
    location_name = serializers.CharField(source='location.name', read_only=True)
    current_block_name = serializers.CharField(source='current_block.name', read_only=True, allow_null=True)
    current_user_name = serializers.CharField(source='current_user.username', read_only=True, allow_null=True)
    tic40_power_state = serializers.SerializerMethodField()
    tic40_activity_state = serializers.SerializerMethodField()
    dispatch_available = serializers.BooleanField(source='is_available', read_only=True)
    active_operational_state = serializers.SerializerMethodField()
    components = CheComponentSerializer(many=True, read_only=True)
    fuel_level = serializers.FloatField(required=False, min_value=0, max_value=100)
    twistlock_closed = serializers.BooleanField(required=False)
    running_hours = serializers.FloatField(required=False, min_value=0)
    running_hours_since_last_maintenance = serializers.FloatField(required=False, min_value=0)

    cblock = BlockSerializer(many=False, read_only=True)
    class Meta:
        model = Che
        fields = [
            'pk', 'name', 'equipment_type', 'equipment_type_name', 'equipment_type_code',
            'equipment_type_details', 'powered_on', 'working', 'is_available',
            'dispatch_available',
            'is_dispatched', 'is_in_maintenance', 'tic40_power_state',
            'tic40_activity_state', 'active_operational_state',
            'current_block', 'current_block_name', 'current_user', 'current_user_name',
            'location', 'location_name', 'twistlock_closed', 'latitude', 'longitude', 'rotation',
            'running_hours', 'running_hours_since_last_maintenance',
            'fuel_level', 'last_maintenance', 'state_updated_at', 'components', 'cblock'
        ]
        read_only_fields = ['state_updated_at']
        extra_kwargs = {'location': {'required': False}}

    def get_equipment_type_details(self, obj):
        return EquipmentTypeSerializer(obj.equipment_type).data

    def get_tic40_power_state(self, obj):
        return 'ON' if obj.powered_on else 'OFF'

    def get_tic40_activity_state(self, obj):
        if not obj.powered_on:
            return 'NOT_APPLICABLE'
        return 'WORKING' if obj.working else 'IDLE'

    def get_active_operational_state(self, obj):
        interval = obj.state_intervals.filter(ended_at__isnull=True).first()
        return interval.state if interval else None

    def validate(self, attrs):
        attrs = super().validate(attrs)
        current = self.instance
        powered_on = attrs.get('powered_on', getattr(current, 'powered_on', False))
        working = attrs.get('working', getattr(current, 'working', False))
        is_available = attrs.get('is_available', getattr(current, 'is_available', False))
        is_in_maintenance = attrs.get(
            'is_in_maintenance', getattr(current, 'is_in_maintenance', False)
        )
        errors = {}
        if working and not powered_on:
            errors['working'] = 'Working equipment must be powered on.'
        if is_available and not powered_on:
            errors['is_available'] = 'Available equipment must be powered on.'
        if is_in_maintenance and working:
            errors['working'] = 'Equipment in maintenance cannot be working.'
        if is_in_maintenance and is_available:
            errors['is_available'] = 'Equipment in maintenance cannot be available.'
        if errors:
            raise serializers.ValidationError(errors)
        return attrs

    def _pop_component_values(self, validated_data):
        names = {
            'fuel_level', 'twistlock_closed', 'running_hours',
            'running_hours_since_last_maintenance',
        }
        return {name: validated_data.pop(name) for name in names if name in validated_data}

    @transaction.atomic
    def create(self, validated_data):
        component_values = self._pop_component_values(validated_data)
        che = super().create(validated_data)
        from .services.che_components import apply_che_component_values

        apply_che_component_values(che, component_values)
        return che

    @transaction.atomic
    def update(self, instance, validated_data):
        component_values = self._pop_component_values(validated_data)
        che = super().update(instance, validated_data)
        from .services.che_components import apply_che_component_values

        apply_che_component_values(che, component_values)
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
            'latitude', 'longitude', 'z_position', 'bill_of_lading', 'booking'
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

class CarrierServiceEquipmentRequirementSerializer(serializers.ModelSerializer):
    equipment_type_name = serializers.CharField(source='equipment_type.name', read_only=True)
    equipment_code = serializers.CharField(source='equipment_type.equipment_type', read_only=True)

    class Meta:
        model = CarrierServiceEquipmentRequirement
        fields = [
            'id', 'equipment_type', 'equipment_type_name', 'equipment_code',
            'equipment_split', 'expected_productivity_per_hour',
            'horizontal_transport_per_unit',
        ]


class CarrierVisitEquipmentRequirementSerializer(serializers.ModelSerializer):
    equipment_type_name = serializers.CharField(source='equipment_type.name', read_only=True)
    equipment_code = serializers.CharField(source='equipment_type.equipment_type', read_only=True)

    class Meta:
        model = CarrierVisitEquipmentRequirement
        fields = [
            'id', 'equipment_type', 'equipment_type_name', 'equipment_code',
            'equipment_split', 'expected_productivity_per_hour',
            'horizontal_transport_per_unit',
        ]


class CarrierVisitSerializer(serializers.ModelSerializer):
    location_name = serializers.CharField(source='location.name', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    visit_type_display = serializers.CharField(source='get_visit_type_display', read_only=True)
    container_count = serializers.SerializerMethodField()
    carrier_service_name = serializers.CharField(source='carrier_service.name', read_only=True)
    planned_berth_name = serializers.CharField(source='planned_berth.name', read_only=True)
    expected_productivity_per_hour = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    calculated_duration_minutes = serializers.IntegerField(read_only=True)
    calculated_duration_display = serializers.CharField(read_only=True)
    equipment_requirements = CarrierVisitEquipmentRequirementSerializer(many=True, read_only=True)

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
            'voyage_number', 'carrier', 'berth', 'planned_berth',
            'berth_start_bollard', 'berth_end_bollard', 'container_count',
            'carrier_service', 'carrier_service_name', 'planned_berth_name',
            'window_start', 'window_end', 'ops_start', 'ops_end',
            'moves_in', 'moves_out', 'moves_import', 'moves_export',
            'moves_ts', 'moves_restow', 'total_moves',
            'primary_equipment_type', 'crane_split', 'expected_productivity_per_crane',
            'expected_productivity_per_hour', 'horizontal_transport_per_crane',
            'calculated_duration_minutes', 'calculated_duration_display',
            'equipment_requirements',
            'created_at', 'last_updated'
        ]
        read_only_fields = [
            'created_at', 'last_updated', 'container_count', 'etd',
            'expected_productivity_per_hour', 'calculated_duration_minutes',
            'calculated_duration_display',
        ]


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
    expected_productivity_per_hour = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    calculated_duration_minutes = serializers.IntegerField(read_only=True)
    calculated_duration_display = serializers.CharField(read_only=True)
    equipment_requirements = CarrierServiceEquipmentRequirementSerializer(many=True, required=False)

    class Meta:
        model = CarrierService
        fields = '__all__'

    @staticmethod
    def _supports_service(equipment_type, service_type, allow_yard=False):
        if equipment_type.is_horizontal_transport:
            return False
        if allow_yard and equipment_type.is_yard_equipment:
            return True
        if service_type in [CarrierService.VESSEL, CarrierService.BARGE]:
            return equipment_type.is_quay_equipment
        if service_type == CarrierService.TRAIN:
            return equipment_type.is_rail_equipment
        if service_type == CarrierService.TRUCK:
            return equipment_type.is_gate_equipment
        return False

    def validate(self, attrs):
        attrs = super().validate(attrs)
        berth = attrs.get('default_berth', getattr(self.instance, 'default_berth', None))
        start = attrs.get('default_start_bollard', getattr(self.instance, 'default_start_bollard', None))
        end = attrs.get('default_end_bollard', getattr(self.instance, 'default_end_bollard', None))
        location = attrs.get('location', getattr(self.instance, 'location', None))
        service_type = attrs.get('carrier_service_type', getattr(self.instance, 'carrier_service_type', CarrierService.VESSEL))
        vessel_length = attrs.get('vessel_length_m', getattr(self.instance, 'vessel_length_m', None))
        primary_equipment = attrs.get('primary_equipment_type', getattr(self.instance, 'primary_equipment_type', None))
        equipment_requirements = attrs.get('equipment_requirements')
        if berth and location and berth.location_id != location.id:
            raise serializers.ValidationError({'default_berth': 'The berth must belong to the service terminal.'})
        if start and (not berth or start.berth_id != berth.id):
            raise serializers.ValidationError({'default_start_bollard': 'The start bollard must belong to the selected berth.'})
        if end and (not berth or end.berth_id != berth.id):
            raise serializers.ValidationError({'default_end_bollard': 'The end bollard must belong to the selected berth.'})
        is_water_service = service_type in [CarrierService.VESSEL, CarrierService.BARGE]
        if is_water_service:
            if self.instance is None and (not berth or not start or not vessel_length):
                raise serializers.ValidationError(
                    'Vessel and barge services require a berth, initial bollard, and vessel length.'
                )
            if vessel_length is not None and vessel_length <= 0:
                raise serializers.ValidationError({'vessel_length_m': 'Vessel length must be greater than zero.'})
            if berth and start and vessel_length:
                try:
                    attrs['default_end_bollard'] = vessel_end_bollard(berth, start, vessel_length)
                except DjangoValidationError as exc:
                    raise serializers.ValidationError({'vessel_length_m': exc.messages})
        elif vessel_length is not None:
            raise serializers.ValidationError({'vessel_length_m': 'Only vessel and barge services have a vessel length.'})
        elif start and end and start.number >= end.number:
            raise serializers.ValidationError('The end bollard must be after the start bollard.')
        if primary_equipment and not self._supports_service(primary_equipment, service_type):
            raise serializers.ValidationError({
                'primary_equipment_type': 'Select non-horizontal equipment that supports this service operation.'
            })
        if primary_equipment and location and not Che.objects.filter(
            location=location, equipment_type=primary_equipment
        ).exists():
            raise serializers.ValidationError({
                'primary_equipment_type': 'This equipment type is not present in the terminal fleet.'
            })
        if equipment_requirements is not None:
            equipment_ids = set()
            for requirement in equipment_requirements:
                equipment_type = requirement['equipment_type']
                if equipment_type.id in equipment_ids:
                    raise serializers.ValidationError({'equipment_requirements': 'Each equipment type may only be assigned once.'})
                equipment_ids.add(equipment_type.id)
                if not self._supports_service(equipment_type, service_type, allow_yard=True):
                    raise serializers.ValidationError({
                        'equipment_requirements': f'{equipment_type.name} does not support this service operation.'
                    })
                if location and not Che.objects.filter(
                    location=location, equipment_type=equipment_type
                ).exists():
                    raise serializers.ValidationError({
                        'equipment_requirements': f'{equipment_type.name} is not present in the terminal fleet.'
                    })
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        requirements = validated_data.pop('equipment_requirements', [])
        service = super().create(validated_data)
        self._replace_requirements(service, requirements)
        return service

    @transaction.atomic
    def update(self, instance, validated_data):
        requirements = validated_data.pop('equipment_requirements', None)
        service = super().update(instance, validated_data)
        if requirements is not None:
            self._replace_requirements(service, requirements)
        return service

    @staticmethod
    def _replace_requirements(service, requirements):
        service.equipment_requirements.all().delete()
        CarrierServiceEquipmentRequirement.objects.bulk_create([
            CarrierServiceEquipmentRequirement(carrier_service=service, **requirement)
            for requirement in requirements
        ])


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
            'location', 'location_name', 'code', 'latitude', 'longitude',
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


class BollardSerializer(serializers.ModelSerializer):
    class Meta:
        model = Bollard
        fields = [
            'id', 'number', 'name', 'distance_along_berth_m',
            'spacing_from_previous_m', 'latitude', 'longitude',
        ]


class BerthSerializer(serializers.ModelSerializer):
    bollards = BollardSerializer(many=True, read_only=True)
    bollard_spacings = serializers.ListField(
        child=serializers.FloatField(min_value=0.01),
        write_only=True,
        required=False,
    )

    class Meta:
        model = Berth
        fields = [
            'id', 'location', 'area', 'name', 'latitude', 'longitude',
            'heading_deg', 'length_m', 'width_m', 'water_side',
            'bollard_direction', 'bollard_count',
            'default_bollard_spacing_m', 'start_margin_m', 'end_margin_m',
            'bollard_spacings', 'bollards',
        ]
        extra_kwargs = {
            'location': {'required': False},
            'area': {'read_only': True},
            'name': {'required': False},
        }

    def validate(self, attrs):
        attrs = super().validate(attrs)
        count = int(attrs.get('bollard_count', getattr(self.instance, 'bollard_count', 15)))
        spacing = float(attrs.get(
            'default_bollard_spacing_m',
            getattr(self.instance, 'default_bollard_spacing_m', 20.0),
        ))
        length = float(attrs.get('length_m', getattr(self.instance, 'length_m', 300.0)))
        start_margin = float(attrs.get('start_margin_m', getattr(self.instance, 'start_margin_m', 0.0)))
        end_margin = float(attrs.get('end_margin_m', getattr(self.instance, 'end_margin_m', 0.0)))
        spacings = attrs.get('bollard_spacings')
        if spacings is not None and len(spacings) != count - 1:
            raise serializers.ValidationError({'bollard_spacings': f'Provide exactly {count - 1} spacing values.'})
        occupied = start_margin + end_margin + sum(spacings if spacings is not None else [spacing] * (count - 1))
        if occupied > length + 0.001:
            raise serializers.ValidationError('Bollard spacings and margins exceed the berth length.')
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        spacings = validated_data.pop('bollard_spacings', None)
        berth = super().create(validated_data)
        return sync_berth_bollards(berth, spacings)

    @transaction.atomic
    def update(self, instance, validated_data):
        spacings = validated_data.pop('bollard_spacings', None)
        berth = super().update(instance, validated_data)
        return sync_berth_bollards(berth, spacings)


class AreaSerializer(serializers.ModelSerializer):
    location_name = serializers.CharField(source='location.name', read_only=True)
    area_type_display = serializers.CharField(source='get_area_type_display', read_only=True)
    yard_grid = serializers.JSONField(write_only=True, required=False)
    block_config = serializers.SerializerMethodField()
    parking_settings = serializers.JSONField(write_only=True, required=False)
    parking_config = serializers.SerializerMethodField()
    berth_settings = serializers.JSONField(write_only=True, required=False)
    berth_config = serializers.SerializerMethodField()

    def validate_coordinates(self, value):
        if not isinstance(value, list) or len(value) < 3:
            raise serializers.ValidationError('An area requires at least three coordinates.')
        self._validate_geographic_points(value)
        return value

    @staticmethod
    def _validate_geographic_points(points):
        for point in points:
            if not isinstance(point, list) or len(point) < 2:
                raise serializers.ValidationError('Every coordinate must be [longitude, latitude].')
            try:
                longitude, latitude = float(point[0]), float(point[1])
            except (TypeError, ValueError):
                raise serializers.ValidationError('Coordinates must be numeric.')
            if not -180 <= longitude <= 180 or not -90 <= latitude <= 90:
                raise serializers.ValidationError('Coordinates must be valid longitude and latitude values.')

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

    def get_berth_config(self, obj):
        try:
            berth = obj.berth_config
        except Berth.DoesNotExist:
            return None
        return BerthSerializer(berth, context=self.context).data

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

    def _save_berth(self, area, settings):
        try:
            berth = area.berth_config
        except Berth.DoesNotExist:
            berth = None
        if area.area_type != Area.BERTH:
            if berth:
                berth.delete()
            return area
        if settings is None and berth:
            return area
        if settings is None:
            raise serializers.ValidationError({'berth_settings': 'Berth configuration is required.'})
        serializer = BerthSerializer(
            instance=berth,
            data={**settings, 'location': area.location_id, 'name': area.name},
            context=self.context,
        )
        serializer.is_valid(raise_exception=True)
        berth = serializer.save(area=area)
        area.coordinates = berth_polygon(
            berth.latitude, berth.longitude, berth.heading_deg,
            berth.length_m, berth.width_m,
        )
        area.area = berth.length_m * berth.width_m
        area.save(update_fields=['coordinates', 'area'])
        return area

    @transaction.atomic
    def create(self, validated_data):
        grid = validated_data.pop('yard_grid', None)
        parking = validated_data.pop('parking_settings', None)
        berth = validated_data.pop('berth_settings', None)
        area = super().create(validated_data)
        self._save_yard_block(area, grid)
        self._save_parking(area, parking)
        return self._save_berth(area, berth)

    @transaction.atomic
    def update(self, instance, validated_data):
        grid = validated_data.pop('yard_grid', None)
        parking = validated_data.pop('parking_settings', None)
        berth = validated_data.pop('berth_settings', None)
        area = super().update(instance, validated_data)
        self._save_yard_block(area, grid)
        self._save_parking(area, parking)
        return self._save_berth(area, berth)

    class Meta:
        model = Area
        fields = [
            'id', 'location', 'location_name', 'name', 'area_type', 'area_type_display',
            'area', 'is_visible', 'coordinates', 'yard_grid', 'block_config',
            'parking_settings', 'parking_config', 'berth_settings', 'berth_config'
        ]
        extra_kwargs = {'location': {'required': False}}


class EquipmentTypeSerializer(serializers.ModelSerializer):
    equipment_type_display = serializers.CharField(source='get_equipment_type_display', read_only=True)
    energy_source_display = serializers.CharField(source='get_energy_source_display', read_only=True)
    component_profiles = EquipmentTypeComponentSerializer(many=True, read_only=True)
    driving_speed_full_load_kmh = serializers.FloatField(required=False, min_value=0)
    driving_speed_empty_kmh = serializers.FloatField(required=False, min_value=0)
    lifting_speed_full_load_m_per_min = serializers.FloatField(required=False, min_value=0)
    lifting_speed_empty_m_per_min = serializers.FloatField(required=False, min_value=0)
    power_rating_kw = serializers.FloatField(required=False, min_value=0)
    engine_type = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    fuel_tank_capacity = serializers.FloatField(required=False, min_value=0)
    turning_radius_m = serializers.FloatField(required=False, min_value=0)

    COMPONENT_PROFILE_FIELDS = {
        'driving_speed_full_load_kmh', 'driving_speed_empty_kmh',
        'lifting_speed_full_load_m_per_min', 'lifting_speed_empty_m_per_min',
        'power_rating_kw', 'engine_type', 'fuel_tank_capacity', 'turning_radius_m',
    }

    class Meta:
        model = EquipmentType
        fields = [
            'id', 'name', 'equipment_type', 'equipment_type_display',
            'is_horizontal_transport', 'is_yard_equipment', 'is_quay_equipment',
            'is_rail_equipment', 'is_gate_equipment',
            'length_m', 'width_m', 'height_m', 'max_lift_weight_kg',
            'is_twin_capeable', 'stacking_capacity',
            'driving_speed_full_load_kmh', 'driving_speed_empty_kmh',
            'lifting_speed_full_load_m_per_min', 'lifting_speed_empty_m_per_min',
            'turning_radius_m', 'energy_source', 'energy_source_display',
            'power_rating_kw', 'engine_type', 'fuel_tank_capacity',
            'minor_maintenance_interval_hours', 'major_maintenance_interval_hours',
            'is_automated', 'component_profiles',
        ]

    def _pop_component_defaults(self, validated_data):
        return {
            name: validated_data.pop(name)
            for name in self.COMPONENT_PROFILE_FIELDS if name in validated_data
        }

    @transaction.atomic
    def create(self, validated_data):
        component_defaults = self._pop_component_defaults(validated_data)
        equipment_type = EquipmentType(**validated_data)
        for name, value in component_defaults.items():
            setattr(equipment_type, name, value)
        equipment_type.save()
        return equipment_type

    @transaction.atomic
    def update(self, instance, validated_data):
        component_defaults = self._pop_component_defaults(validated_data)
        for name, value in validated_data.items():
            setattr(instance, name, value)
        for name, value in component_defaults.items():
            setattr(instance, name, value)
        instance.save()
        return instance


class RoutingNodeSerializer(serializers.ModelSerializer):
    class Meta:
        model = RoutingNode
        fields = ['id', 'location', 'name', 'latitude', 'longitude']


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
                raise serializers.ValidationError('Every road point must be [longitude, latitude].')
            try:
                longitude, latitude = float(point[0]), float(point[1])
            except (TypeError, ValueError):
                raise serializers.ValidationError('Road point coordinates must be numeric.')
            if not -180 <= longitude <= 180 or not -90 <= latitude <= 90:
                raise serializers.ValidationError('Road points must contain valid longitude and latitude values.')
        return value

    def _sync_endpoint_nodes(self, road):
        first, last = road.points[0], road.points[-1]
        if road.start_node_id is None:
            road.start_node = RoutingNode.objects.create(
                location=road.location, name=f'{road.name} start',
                longitude=first[0], latitude=first[1]
            )
        else:
            road.start_node.longitude, road.start_node.latitude = first[0], first[1]
            road.start_node.name = f'{road.name} start'
            road.start_node.save(update_fields=['name', 'longitude', 'latitude'])
        if road.end_node_id is None:
            road.end_node = RoutingNode.objects.create(
                location=road.location, name=f'{road.name} end',
                longitude=last[0], latitude=last[1]
            )
        else:
            road.end_node.longitude, road.end_node.latitude = last[0], last[1]
            road.end_node.name = f'{road.name} end'
            road.end_node.save(update_fields=['name', 'longitude', 'latitude'])
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


class ProcessHierarchySerializer(serializers.ModelSerializer):
    process_area_display = serializers.CharField(source='get_process_area_display', read_only=True)
    parent_name = serializers.CharField(source='parent.name', read_only=True, allow_null=True)

    class Meta:
        model = ProcessHierarchy
        fields = [
            'id', 'name', 'code', 'process_area', 'process_area_display',
            'parent', 'parent_name', 'description', 'display_order', 'is_active',
        ]


class ProcessVersionSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = ProcessVersion
        fields = ['id', 'version_number', 'version_label', 'status', 'is_current', 'updated_at', 'published_at']


class BusinessProcessSerializer(serializers.ModelSerializer):
    hierarchy_name = serializers.CharField(source='hierarchy.name', read_only=True, allow_null=True)
    location_name = serializers.CharField(source='location.name', read_only=True, allow_null=True)
    trigger_type_display = serializers.CharField(source='get_trigger_type_display', read_only=True)
    latest_version = serializers.SerializerMethodField()

    class Meta:
        model = BusinessProcess
        fields = [
            'id', 'name', 'code', 'hierarchy', 'hierarchy_name', 'location',
            'location_name', 'description', 'owner_role', 'trigger_type',
            'trigger_type_display', 'input_schema', 'output_schema',
            'is_active', 'created_at', 'updated_at', 'latest_version',
        ]
        read_only_fields = ['created_at', 'updated_at', 'latest_version']

    def get_latest_version(self, obj):
        version = obj.versions.filter(is_current=True).first() or obj.versions.order_by('-version_number').first()
        if not version:
            return None
        return ProcessVersionSummarySerializer(version).data


class ProcessVersionSerializer(serializers.ModelSerializer):
    version_number = serializers.IntegerField(required=False, min_value=1)
    business_process_code = serializers.CharField(source='business_process.code', read_only=True)
    business_process_name = serializers.CharField(source='business_process.name', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    created_by_username = serializers.CharField(source='created_by.username', read_only=True, allow_null=True)

    class Meta:
        model = ProcessVersion
        fields = [
            'id', 'business_process', 'business_process_code', 'business_process_name',
            'version_number', 'version_label', 'status', 'status_display', 'is_current',
            'nodered_flow', 'input_signals', 'output_signals', 'sop_markdown',
            'mqtt_announcement_topic', 'created_by', 'created_by_username',
            'created_at', 'updated_at', 'published_at',
        ]
        read_only_fields = ['created_by', 'created_at', 'updated_at', 'published_at']
        extra_kwargs = {'version_number': {'required': False}}
        validators = []

    def validate_nodered_flow(self, value):
        if value is not None and not isinstance(value, (list, dict)):
            raise serializers.ValidationError('Node-RED flow must be a JSON array or object.')
        return value

    def validate_input_signals(self, value):
        if value is not None and not isinstance(value, list):
            raise serializers.ValidationError('Input signals must be a JSON array.')
        return value

    def validate_output_signals(self, value):
        if value is not None and not isinstance(value, list):
            raise serializers.ValidationError('Output signals must be a JSON array.')
        return value

    def validate(self, attrs):
        attrs = super().validate(attrs)
        business_process = attrs.get('business_process', getattr(self.instance, 'business_process', None))
        version_number = attrs.get('version_number', getattr(self.instance, 'version_number', None))
        if not version_number and business_process:
            latest = business_process.versions.order_by('-version_number').first()
            attrs['version_number'] = (latest.version_number + 1) if latest else 1
            version_number = attrs['version_number']
        if business_process and version_number:
            duplicate = ProcessVersion.objects.filter(
                business_process=business_process,
                version_number=version_number,
            )
            if self.instance:
                duplicate = duplicate.exclude(pk=self.instance.pk)
            if duplicate.exists():
                raise serializers.ValidationError(
                    {'version_number': 'This process already has that version number.'}
                )
        return attrs

    def create(self, validated_data):
        request = self.context.get('request')
        if request and request.user and request.user.is_authenticated:
            validated_data.setdefault('created_by', request.user)
        return super().create(validated_data)


class BusinessProcessExecutionSerializer(serializers.ModelSerializer):
    business_process_code = serializers.CharField(source='business_process.code', read_only=True)
    business_process_name = serializers.CharField(source='business_process.name', read_only=True)
    process_version_label = serializers.CharField(source='process_version.version_label', read_only=True)
    location_name = serializers.CharField(source='location.name', read_only=True, allow_null=True)
    requested_by_username = serializers.CharField(source='requested_by.username', read_only=True, allow_null=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)

    class Meta:
        model = BusinessProcessExecution
        fields = [
            'id', 'business_process', 'business_process_code', 'business_process_name',
            'process_version', 'process_version_label', 'location', 'location_name',
            'status', 'status_display', 'trigger_source', 'trigger_payload',
            'result_payload', 'error_message', 'requested_by', 'requested_by_username',
            'started_at', 'completed_at',
        ]
        read_only_fields = ['requested_by', 'started_at', 'completed_at']

    def validate(self, attrs):
        attrs = super().validate(attrs)
        process = attrs.get('business_process', getattr(self.instance, 'business_process', None))
        version = attrs.get('process_version', getattr(self.instance, 'process_version', None))
        if process and version and version.business_process_id != process.id:
            raise serializers.ValidationError('Process version must belong to the selected business process.')
        return attrs

    def create(self, validated_data):
        request = self.context.get('request')
        if request and request.user and request.user.is_authenticated:
            validated_data.setdefault('requested_by', request.user)
        return super().create(validated_data)


####  Location
class LocationSerializer(serializers.ModelSerializer):
    blocks = BlockSerializer(many=True, read_only=True)
    ches = CheSerializer(many=True, read_only=True)
    areas = AreaSerializer(many=True, read_only=True)
    berths = BerthSerializer(many=True, read_only=True)
    timezone_utc_offset_minutes = serializers.SerializerMethodField()
    local_datetime = serializers.SerializerMethodField()

    def _local_now(self, obj):
        return django_timezone.now().astimezone(ZoneInfo(obj.timezone or 'UTC'))

    def get_timezone_utc_offset_minutes(self, obj):
        return int(self._local_now(obj).utcoffset().total_seconds() // 60)

    def get_local_datetime(self, obj):
        return self._local_now(obj).isoformat()

    class Meta:
        model = Location
        fields = [
            'pk', 'name', 'location_type',
            'latitude', 'longitude', 'map_origin_latitude', 'map_origin_longitude',
            'map_width_m', 'map_height_m', 'map_units_per_meter',
            'default_zoom', 'angle', 'timezone', 'timezone_utc_offset_minutes', 'local_datetime',
            'blocks', 'ches', 'areas', 'berths'
        ]
