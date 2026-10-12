from django.contrib.auth.models import User, Group
from django.db import models, transaction
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers, generics
from django.utils import timezone as django_timezone
from zoneinfo import ZoneInfo
from .models import (
    AccessPolicy, Battery, BerthSegment, BusinessProcess,
    BusinessProcessExecution, CarrierService, CarrierServiceEquipmentRequirement,
    CarrierVisit, CarrierVisitEquipmentRequirement, Che, CheComponent,
    CheOperatingScope, ComponentType, ContainerOwner, ContainerUnit,
    ContainerVisit, CurrentSignalValue, EnergyTank, Engine, EquipmentType,
    EquipmentTypeComponent, HandlingCycle, InterfacePosition, ISOType, LayoutAsset,
    LayoutPreset, LayoutVersion, LinearAxis, LinearMarker, Location, MotionAxis,
    OperationalPosition, OperationalStateInterval, Order, OrderLeg,
    OrderLegRouteEdge, OrderRoutePlan, Pile, PileBooking, PointOfWork, Position,
    ProcessHierarchy, ProcessVersion, RouteEdge, RouteEdgeRequirement, RouteNode,
    ServiceCargoRoute, SignalBinding, SignalDefinition, Spreader, StorageGrid,
    TransferInterface, VisitCargoRoute, WorkQueue,
)
from .services import (
    geographic_distance_m, publish_layout, sync_axis_graph, sync_berth_bollards,
    sync_interface_positions, sync_yard_grid, validate_layout, vessel_end_bollard,
)
from .services.layout import (
    geometry_bounds_center, move_asset_dependents, resolved_interface_point,
    resolved_route_node_point, sync_block_crane_rail, polyline_length_m,
)
from .services.che_rails import (
    align_che_pose, assign_che_to_rail, che_rail_assignment, che_rail_motion,
    validate_che_rail_assignment,
)


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
        block_name = obj.pile.storage_grid.name
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
        block_name = obj.storage_grid.name
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
    block_name = serializers.CharField(source='storage_grid.name', read_only=True)

    class Meta:
        model = Pile
        fields = [
            'pk', 'storage_grid', 'block_name', 'bay', 'bay_name', 'row', 'row_name', 'tiers',
            'allows_in', 'allows_out', 'is_restricted', 'allows_20', 'allows_40', 'allows_45',
            'latitude', 'longitude', '__str__', 'positions'
        ]
        
    def partial_update(self, instance, validated_data):
        
        pile = Pile.objects.get(pk=instance.id)
        Pile.objects.filter(pk=instance.id)\
                           .update(**validated_data)
        return pile
        
#### Storage grid
class StorageGridSerializer(serializers.ModelSerializer):
    location_name = serializers.CharField(source='layout_asset.layout_version.location.name', read_only=True)
    grid_kind_display = serializers.CharField(source='get_grid_kind_display', read_only=True)
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
        model = StorageGrid
        fields = ['pk', 'name', 'layout_asset', 'location_name', 'grid_kind', 'grid_kind_display',
                 'max_bays', 'max_rows', 'max_tier', 'latitude', 'longitude',
                 'heading_deg', 'slot_heading_deg',
                 'pile_length_m', 'pile_width_m', 'pile_height_m',
                 'initial_bay', 'initial_row', 'bay_order', 'row_order',
                 'bay_naming', 'row_naming', 'bay_use_heading_axis',
                 'padding_top_m', 'padding_bottom_m', 'padding_left_m', 'padding_right_m',
                 'bay_spacing_m', 'row_spacing_m', 'is_heapzone',
                 'total_piles', 'total_positions_20', 'total_positions_40', 'total_positions_45',
                 'free_positions_20', 'free_positions_40', 'free_positions_45', 'piles']
        extra_kwargs = {'layout_asset': {'required': False}}

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
    rail_motion = serializers.SerializerMethodField()
    rail_assignment = serializers.SerializerMethodField()
    rail_axis = serializers.PrimaryKeyRelatedField(
        queryset=LinearAxis.objects.all(), required=False, allow_null=True, write_only=True,
    )
    rail_position_m = serializers.FloatField(required=False, allow_null=True, write_only=True)
    rail_min_position_m = serializers.FloatField(required=False, allow_null=True, write_only=True)
    rail_max_position_m = serializers.FloatField(required=False, allow_null=True, write_only=True)
    equipment_type_details = serializers.SerializerMethodField()
    equipment_type_name = serializers.CharField(source='equipment_type.name', read_only=True)
    equipment_type_code = serializers.CharField(source='equipment_type.equipment_type', read_only=True)
    location_name = serializers.CharField(source='location.name', read_only=True)
    current_asset_name = serializers.CharField(source='current_asset.name', read_only=True, allow_null=True)
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

    class Meta:
        model = Che
        fields = [
            'pk', 'name', 'equipment_type', 'equipment_type_name', 'equipment_type_code',
            'equipment_type_details', 'powered_on', 'working', 'is_available',
            'dispatch_available',
            'is_dispatched', 'is_in_maintenance', 'tic40_power_state',
            'tic40_activity_state', 'active_operational_state',
            'current_asset', 'current_asset_name', 'current_user', 'current_user_name',
            'location', 'location_name', 'twistlock_closed', 'latitude', 'longitude', 'rotation',
            'running_hours', 'running_hours_since_last_maintenance',
            'fuel_level', 'last_maintenance', 'state_updated_at', 'components',
            'rail_motion', 'rail_assignment', 'rail_axis', 'rail_position_m',
            'rail_min_position_m', 'rail_max_position_m',
        ]
        read_only_fields = ['state_updated_at']
        extra_kwargs = {'location': {'required': False}}

    def get_equipment_type_details(self, obj):
        return EquipmentTypeSerializer(obj.equipment_type).data

    def get_rail_motion(self, obj):
        return che_rail_motion(obj)

    def get_rail_assignment(self, obj):
        return che_rail_assignment(obj)

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
        current_asset = attrs.get('current_asset', getattr(current, 'current_asset', None))
        view = self.context.get('view')
        location_id = getattr(current, 'location_id', None) or (
            getattr(view, 'kwargs', {}).get('location_pk') if view else None
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
        if current_asset and location_id and current_asset.layout_version.location_id != int(location_id):
            errors['current_asset'] = 'Assigned area must belong to the CHE terminal.'
        elif current_asset and current_asset.asset_kind not in LayoutAsset.CHE_PLACEMENT_KINDS:
            errors['current_asset'] = (
                'CHE may only be assigned to a quay, yard block, parking area, or rail area.'
            )
        rail_fields = {
            'rail_axis', 'rail_position_m', 'rail_min_position_m', 'rail_max_position_m',
        }
        if rail_fields & attrs.keys():
            equipment_type = attrs.get('equipment_type', getattr(current, 'equipment_type', None))
            if not equipment_type:
                errors['equipment_type'] = 'Select an equipment type before assigning a rail.'
            axis = attrs.get('rail_axis')
            assignment = che_rail_assignment(current) if current else None
            if axis is None and current:
                axis = LinearAxis.objects.filter(pk=assignment['axis_id']).first() if assignment else None
            elif axis and assignment and assignment['axis_id'] != axis.pk:
                assignment = None
            if equipment_type:
                probe = Che(
                    pk=getattr(current, 'pk', None),
                    equipment_type=equipment_type,
                    current_asset=current_asset,
                    location_id=int(location_id or attrs.get('location').pk),
                    latitude=attrs.get('latitude', getattr(current, 'latitude', None)),
                    longitude=attrs.get('longitude', getattr(current, 'longitude', None)),
                )
                try:
                    validate_che_rail_assignment(
                        probe,
                        axis,
                        attrs.get('rail_position_m', assignment['position_m'] if assignment else None),
                        attrs.get('rail_min_position_m', assignment['min_position_m'] if assignment else None),
                        attrs.get('rail_max_position_m', assignment['max_position_m'] if assignment else None),
                    )
                except DjangoValidationError as exc:
                    errors.update(exc.message_dict)
        if errors:
            raise serializers.ValidationError(errors)
        if {'current_asset', 'equipment_type', 'latitude', 'longitude', 'rotation'} & attrs.keys():
            equipment_type = attrs.get('equipment_type', getattr(current, 'equipment_type', None))
            if equipment_type and (location_id or attrs.get('location')):
                probe = Che(pk=getattr(current, 'pk', None), equipment_type=equipment_type,
                            current_asset=current_asset, location_id=int(location_id or attrs['location'].pk),
                            latitude=attrs.get('latitude', getattr(current, 'latitude', None)),
                            longitude=attrs.get('longitude', getattr(current, 'longitude', None)))
                aligned = align_che_pose(probe, force=current is None or 'current_asset' in attrs or 'equipment_type' in attrs)
                if aligned:
                    attrs.update(aligned)
        return attrs

    def _pop_component_values(self, validated_data):
        names = {
            'fuel_level', 'twistlock_closed', 'running_hours',
            'running_hours_since_last_maintenance',
        }
        return {name: validated_data.pop(name) for name in names if name in validated_data}

    def _pop_rail_values(self, validated_data):
        names = {
            'rail_axis', 'rail_position_m', 'rail_min_position_m', 'rail_max_position_m',
        }
        return {name: validated_data.pop(name) for name in names if name in validated_data}

    def _save_rail_values(self, che, values):
        if not values:
            return
        axis = values.get('rail_axis')
        assignment = che_rail_assignment(che)
        if axis is None:
            axis = LinearAxis.objects.filter(pk=assignment['axis_id']).first() if assignment else None
        elif assignment and assignment['axis_id'] != axis.pk:
            assignment = None
        assign_che_to_rail(
            che,
            axis,
            values.get('rail_position_m', assignment['position_m'] if assignment else None),
            values.get('rail_min_position_m', assignment['min_position_m'] if assignment else None),
            values.get('rail_max_position_m', assignment['max_position_m'] if assignment else None),
        )

    @transaction.atomic
    def create(self, validated_data):
        component_values = self._pop_component_values(validated_data)
        rail_values = self._pop_rail_values(validated_data)
        che = super().create(validated_data)
        from .services.che_components import apply_che_component_values

        apply_che_component_values(che, component_values)
        self._save_rail_values(che, rail_values)
        return che

    @transaction.atomic
    def update(self, instance, validated_data):
        component_values = self._pop_component_values(validated_data)
        rail_values = self._pop_rail_values(validated_data)
        che = super().update(instance, validated_data)
        from .services.che_components import apply_che_component_values

        apply_che_component_values(che, component_values)
        self._save_rail_values(che, rail_values)
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
    current_che_name = serializers.CharField(source='current_che.name', read_only=True, allow_null=True)

    class Meta:
        model = ContainerVisit
        fields = [
            'pk', 'category', 'category_display', 'container_number', 'container_number_name',
            'ib_carrier', 'ib_carrier_name', 'ob_carrier', 'ob_carrier_name',
            'location', 'location_name', 'position', 'eta', 'etd', 'weight',
            'pod', 'pod_name', 'pod2', 'pod2_name', 'requires_power', 'is_imdg',
            'latitude', 'longitude', 'z_position', 'bill_of_lading', 'booking',
            'status', 'current_che', 'current_che_name', 'last_updated',
        ]
        read_only_fields = ['current_che', 'last_updated']

    def validate(self, attrs):
        attrs = super().validate(attrs)
        status_value = attrs.get('status', getattr(self.instance, 'status', ContainerVisit.PREADVISED))
        current_che = getattr(self.instance, 'current_che', None) if self.instance else None
        if status_value == ContainerVisit.ON_EQUIPMENT and not current_che:
            raise serializers.ValidationError({
                'status': 'Use the execution pickup service to place a container on equipment.'
            })
        if status_value != ContainerVisit.ON_EQUIPMENT and current_che:
            raise serializers.ValidationError({
                'status': 'Use the execution drop service to remove a container from equipment.'
            })
        return attrs

class OrderLegRouteEdgeSerializer(serializers.ModelSerializer):
    route_edge_code = serializers.CharField(source='route_edge.code', read_only=True)
    edge_kind = serializers.CharField(source='route_edge.edge_kind', read_only=True)
    movement_mode = serializers.CharField(source='route_edge.movement_mode', read_only=True)

    class Meta:
        model = OrderLegRouteEdge
        fields = [
            'id', 'sequence', 'route_edge', 'route_edge_code',
            'edge_kind', 'movement_mode',
        ]


class OrderLegSerializer(serializers.ModelSerializer):
    start_position_code = serializers.CharField(source='start_position.code', read_only=True)
    end_position_code = serializers.CharField(source='end_position.code', read_only=True)
    route_edges = OrderLegRouteEdgeSerializer(many=True, read_only=True)

    class Meta:
        model = OrderLeg
        fields = [
            'id', 'order', 'route_plan', 'sequence', 'leg_kind', 'status', 'che',
            'equipment_type', 'distance', 'start_position', 'end_position',
            'start_position_code', 'end_position_code', 'route_edges',
            'planned_start', 'planned_end', 'actual_start', 'actual_end',
        ]


class OrderRoutePlanSerializer(serializers.ModelSerializer):
    legs = OrderLegSerializer(many=True, read_only=True)

    class Meta:
        model = OrderRoutePlan
        fields = [
            'id', 'order', 'layout_version', 'version', 'status', 'calculated_at',
            'cost', 'reason', 'legs',
        ]
        read_only_fields = ['calculated_at']

class OrderSerializer(serializers.ModelSerializer):
    order_legs = OrderLegSerializer(many=True, read_only=True)
    route_plans = OrderRoutePlanSerializer(many=True, read_only=True)
    from_position_code = serializers.CharField(source='from_position.code', read_only=True)
    to_position_code = serializers.CharField(source='to_position.code', read_only=True)

    class Meta:
        model = Order
        fields = [
            'id', 'order_type', 'status', 'priority', 'container_number', 'work_queue',
            'etc', 'atc', 'released_at', 'completed_at',
            'from_position', 'from_position_code', 'to_position', 'to_position_code',
            'order_legs', 'route_plans',
        ]
        read_only_fields = ['status', 'atc', 'released_at', 'completed_at']

    def validate(self, attrs):
        attrs = super().validate(attrs)
        queue = attrs.get('work_queue', getattr(self.instance, 'work_queue', None))
        container = attrs.get('container_number', getattr(self.instance, 'container_number', None))
        start = attrs.get('from_position', getattr(self.instance, 'from_position', None))
        finish = attrs.get('to_position', getattr(self.instance, 'to_position', None))
        errors = {}
        if self.instance is None and not start:
            errors['from_position'] = 'A routed order requires a start position.'
        if self.instance is None and not finish:
            errors['to_position'] = 'A routed order requires an end position.'
        if start and finish and start.layout_version_id != finish.layout_version_id:
            errors['to_position'] = 'Start and end positions must use the same layout version.'
        if start and finish and start.pk == finish.pk:
            errors['to_position'] = 'Start and end positions must be different.'
        if queue and queue.location_id:
            for field, position in [('from_position', start), ('to_position', finish)]:
                if position and position.layout_version.location_id != queue.location_id:
                    errors[field] = 'Position belongs to another terminal.'
            if container and container.location_id != queue.location_id:
                errors['container_number'] = 'Container visit belongs to another terminal.'
        if errors:
            raise serializers.ValidationError(errors)
        return attrs
        
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

    def validate(self, attrs):
        attrs = super().validate(attrs)
        berth = attrs.get('planned_berth', getattr(self.instance, 'planned_berth', None))
        start = attrs.get('berth_start_bollard', getattr(self.instance, 'berth_start_bollard', None))
        end = attrs.get('berth_end_bollard', getattr(self.instance, 'berth_end_bollard', None))
        location = attrs.get('location', getattr(self.instance, 'location', None))
        if berth and location and berth.quay_asset.layout_version.location_id != location.pk:
            raise serializers.ValidationError({'planned_berth': 'The berth must belong to the visit terminal.'})
        for field, marker in [
            ('berth_start_bollard', start), ('berth_end_bollard', end),
        ]:
            if marker and not self._marker_is_on_berth(marker, berth):
                raise serializers.ValidationError({field: 'The bollard must belong to the selected berth segment.'})
        return attrs

    @staticmethod
    def _marker_is_on_berth(marker, berth):
        if not berth or marker.axis_id != berth.quay_face_axis_id:
            return False
        low = min(berth.start_chainage_m, berth.end_chainage_m)
        high = max(berth.start_chainage_m, berth.end_chainage_m)
        return low <= marker.chainage_m <= high


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
        if berth and location and berth.quay_asset.layout_version.location_id != location.id:
            raise serializers.ValidationError({'default_berth': 'The berth must belong to the service terminal.'})
        if start and not CarrierVisitSerializer._marker_is_on_berth(start, berth):
            raise serializers.ValidationError({'default_start_bollard': 'The start bollard must belong to the selected berth.'})
        if end and not CarrierVisitSerializer._marker_is_on_berth(end, berth):
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
        elif start and end and start.sequence >= end.sequence:
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
            'id', 'name', 'pow_type', 'pow_type_display', 'related_ches',
            'related_ches_names', 'location', 'location_name', 'code',
            'latitude', 'longitude', 'operational_position', 'route_node',
            'max_throughput', 'is_active',
        ]

    def validate(self, attrs):
        attrs = super().validate(attrs)
        location = attrs.get('location', getattr(self.instance, 'location', None))
        position = attrs.get('operational_position', getattr(self.instance, 'operational_position', None))
        node = attrs.get('route_node', getattr(self.instance, 'route_node', None))
        if position and location and position.layout_version.location_id != location.pk:
            raise serializers.ValidationError({'operational_position': 'Position belongs to another terminal.'})
        if node and location and node.layout_version.location_id != location.pk:
            raise serializers.ValidationError({'route_node': 'Route node belongs to another terminal.'})
        return attrs


class GeoJSONGeometryMixin:
    geometry_types = {'Point', 'LineString', 'Polygon'}

    def validate_geometry(self, value):
        if not isinstance(value, dict) or value.get('type') not in self.geometry_types:
            raise serializers.ValidationError('Use a GeoJSON Point, LineString, or Polygon geometry.')
        coordinates = value.get('coordinates')
        if not isinstance(coordinates, list) or not coordinates:
            raise serializers.ValidationError('Geometry coordinates cannot be empty.')
        return value

    @staticmethod
    def _validate_points(points):
        if not isinstance(points, list) or len(points) < 2:
            raise serializers.ValidationError('An axis requires at least two coordinate points.')
        for point in points:
            if not isinstance(point, list) or len(point) < 2:
                raise serializers.ValidationError('Every point must be [longitude, latitude].')
            longitude, latitude = float(point[0]), float(point[1])
            if not -180 <= longitude <= 180 or not -90 <= latitude <= 90:
                raise serializers.ValidationError('Coordinates must use valid WGS-84 longitude/latitude values.')
        return points


class LinearMarkerSerializer(serializers.ModelSerializer):
    number = serializers.IntegerField(source='sequence', read_only=True)
    distance_along_berth_m = serializers.FloatField(source='chainage_m', read_only=True)
    spacing_from_previous_m = serializers.SerializerMethodField()

    class Meta:
        model = LinearMarker
        fields = [
            'id', 'axis', 'marker_kind', 'sequence', 'number', 'code', 'name',
            'chainage_m', 'distance_along_berth_m', 'lateral_offset_m',
            'spacing_from_previous_m', 'latitude', 'longitude', 'metadata',
        ]

    def get_spacing_from_previous_m(self, obj):
        return obj.metadata.get('spacing_from_previous_m')


class AccessPolicySerializer(serializers.ModelSerializer):
    layout_version = serializers.PrimaryKeyRelatedField(read_only=True)
    allowed_che_type_names = serializers.SerializerMethodField()

    class Meta:
        model = AccessPolicy
        fields = [
            'id', 'layout_version', 'code', 'name', 'allowed_che_types',
            'allowed_che_type_names', 'allow_loaded', 'allow_empty',
            'max_width_m', 'max_height_m', 'max_weight_t', 'rules',
        ]
        extra_kwargs = {'layout_version': {'required': False}}

    def get_allowed_che_type_names(self, obj):
        return list(obj.allowed_che_types.values_list('name', flat=True))


class LinearAxisSerializer(GeoJSONGeometryMixin, serializers.ModelSerializer):
    markers = LinearMarkerSerializer(many=True, read_only=True)
    generated_edge_count = serializers.IntegerField(source='route_edges.count', read_only=True)

    class Meta:
        model = LinearAxis
        fields = [
            'id', 'asset', 'code', 'name', 'axis_kind', 'points', 'direction',
            'lane_count', 'capacity', 'width_m', 'speed_limit_kph',
            'access_policy', 'is_enabled', 'metadata', 'markers',
            'generated_edge_count',
        ]
        extra_kwargs = {'asset': {'required': False}}

    def validate_points(self, value):
        return self._validate_points(value)

    def validate(self, attrs):
        attrs = super().validate(attrs)
        asset = attrs.get('asset', getattr(self.instance, 'asset', self.context.get('asset')))
        policy = attrs.get('access_policy', getattr(self.instance, 'access_policy', None))
        axis_kind = attrs.get('axis_kind', getattr(self.instance, 'axis_kind', None))
        if policy and asset and policy.layout_version_id != asset.layout_version_id:
            raise serializers.ValidationError({'access_policy': 'Policy belongs to another layout version.'})
        if self.instance and self.instance.road_interfaces.exists() and axis_kind != LinearAxis.ROAD_CENTERLINE:
            raise serializers.ValidationError({'axis_kind': 'A road used by transfer interfaces must remain a road centerline.'})
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        axis = super().create(validated_data)
        try:
            return sync_axis_graph(axis)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.messages)

    @transaction.atomic
    def update(self, instance, validated_data):
        previous_points = [list(point) for point in instance.points or []]
        axis = super().update(instance, validated_data)
        try:
            return sync_axis_graph(axis, previous_points=previous_points)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.messages)


class BerthSegmentSerializer(serializers.ModelSerializer):
    bollards = serializers.SerializerMethodField()
    location = serializers.IntegerField(source='quay_asset.layout_version.location_id', read_only=True)
    heading_deg = serializers.FloatField(source='quay_asset.heading_deg', read_only=True)
    length_m = serializers.FloatField(read_only=True)
    width_m = serializers.SerializerMethodField()
    bollard_count = serializers.IntegerField(write_only=True, required=False, default=15, min_value=2)
    default_bollard_spacing_m = serializers.FloatField(write_only=True, required=False, default=20.0, min_value=0.01)
    start_margin_m = serializers.FloatField(write_only=True, required=False, default=0.0, min_value=0)
    end_margin_m = serializers.FloatField(write_only=True, required=False, default=0.0, min_value=0)
    bollard_spacings = serializers.ListField(
        child=serializers.FloatField(min_value=0.01), write_only=True, required=False,
    )

    class Meta:
        model = BerthSegment
        fields = [
            'id', 'quay_asset', 'quay_face_axis', 'code', 'name',
            'start_chainage_m', 'end_chainage_m', 'water_side',
            'bollard_direction', 'is_active', 'location', 'heading_deg',
            'length_m', 'width_m', 'bollard_count',
            'default_bollard_spacing_m', 'start_margin_m', 'end_margin_m',
            'bollard_spacings', 'bollards',
        ]
        extra_kwargs = {
            'quay_asset': {'required': False},
            'quay_face_axis': {'required': False},
        }

    def get_bollards(self, obj):
        return LinearMarkerSerializer(obj.bollards, many=True).data

    def get_width_m(self, obj):
        return float(obj.quay_asset.metadata.get('width_m', 0.0))

    def validate(self, attrs):
        attrs = super().validate(attrs)
        quay = attrs.get('quay_asset', getattr(self.instance, 'quay_asset', self.context.get('quay_asset')))
        axis = attrs.get('quay_face_axis', getattr(self.instance, 'quay_face_axis', self.context.get('quay_face_axis')))
        if quay and quay.asset_kind != LayoutAsset.QUAY:
            raise serializers.ValidationError({'quay_asset': 'A berth segment must belong to a quay.'})
        if quay and axis and axis.asset_id != quay.pk:
            raise serializers.ValidationError({'quay_face_axis': 'The quay face axis belongs to another asset.'})
        if axis and axis.axis_kind != LinearAxis.QUAY_FACE:
            raise serializers.ValidationError({'quay_face_axis': 'Select an axis with quay-face kind.'})
        return attrs

    def _bollard_values(self, validated_data):
        return {
            key: validated_data.pop(key)
            for key in [
                'bollard_count', 'default_bollard_spacing_m', 'start_margin_m',
                'end_margin_m', 'bollard_spacings',
            ]
            if key in validated_data
        }

    @transaction.atomic
    def create(self, validated_data):
        values = self._bollard_values(validated_data)
        berth = super().create(validated_data)
        sync_berth_bollards(
            berth, values.get('bollard_spacings'), values.get('bollard_count'),
            values.get('default_bollard_spacing_m'), values.get('start_margin_m'),
            values.get('end_margin_m'),
        )
        return berth

    @transaction.atomic
    def update(self, instance, validated_data):
        values = self._bollard_values(validated_data)
        berth = super().update(instance, validated_data)
        if values:
            sync_berth_bollards(
                berth, values.get('bollard_spacings'), values.get('bollard_count'),
                values.get('default_bollard_spacing_m'), values.get('start_margin_m'),
                values.get('end_margin_m'),
            )
        return berth


class InterfacePositionSerializer(serializers.ModelSerializer):
    operational_position_id = serializers.IntegerField(source='operational_position.id', read_only=True)

    class Meta:
        model = InterfacePosition
        fields = [
            'id', 'transfer_interface', 'sequence', 'code', 'name', 'latitude',
            'longitude', 'heading_deg', 'capacity', 'is_enabled', 'lane_index', 'position_index',
            'operational_position_id',
        ]

    def to_representation(self, instance):
        result = super().to_representation(instance)
        point = resolved_interface_point(instance)
        result.update(longitude=point[0] if point else None, latitude=point[1] if point else None)
        return result


class TransferInterfaceSerializer(GeoJSONGeometryMixin, serializers.ModelSerializer):
    positions = InterfacePositionSerializer(many=True, read_only=True)
    position_count = serializers.IntegerField(write_only=True, required=False, min_value=1)

    class Meta:
        model = TransferInterface
        fields = [
            'id', 'asset', 'storage_grid', 'axis', 'road_axis', 'access_policy', 'code', 'name',
            'interface_kind', 'side', 'geometry', 'bay_start', 'bay_end',
            'row_start', 'row_end', 'capacity', 'cargo_may_rest',
            'requires_vehicle_present', 'is_enabled', 'metadata',
            'position_count', 'positions', 'lane_count', 'lane_spacing_m',
            'position_reference', 'reference_che', 'longitudinal_offset_m',
            'lateral_offset_m', 'position_spacing_m',
        ]
        extra_kwargs = {'asset': {'required': False}}

    def validate(self, attrs):
        attrs = super().validate(attrs)
        asset = attrs.get('asset', getattr(self.instance, 'asset', self.context.get('asset')))
        grid = attrs.get('storage_grid', getattr(self.instance, 'storage_grid', None))
        axis = attrs.get('axis', getattr(self.instance, 'axis', None))
        road_axis = attrs.get('road_axis', getattr(self.instance, 'road_axis', None))
        policy = attrs.get('access_policy', getattr(self.instance, 'access_policy', None))
        if grid and asset and grid.layout_asset.layout_version_id != asset.layout_version_id:
            raise serializers.ValidationError({'storage_grid': 'Grid belongs to another layout version.'})
        if axis and asset and axis.asset.layout_version_id != asset.layout_version_id:
            raise serializers.ValidationError({'axis': 'Axis belongs to another layout version.'})
        if road_axis and asset and road_axis.asset.layout_version_id != asset.layout_version_id:
            raise serializers.ValidationError({'road_axis': 'Road belongs to another layout version.'})
        if road_axis and road_axis.axis_kind != LinearAxis.ROAD_CENTERLINE:
            raise serializers.ValidationError({'road_axis': 'Select a road-centerline axis.'})
        if policy and asset and policy.layout_version_id != asset.layout_version_id:
            raise serializers.ValidationError({'access_policy': 'Policy belongs to another layout version.'})
        import math
        for field in ('lane_spacing_m', 'position_spacing_m', 'longitudinal_offset_m', 'lateral_offset_m'):
            value = attrs.get(field, getattr(self.instance, field, 3.5 if field == 'lane_spacing_m' else 6.5 if field == 'position_spacing_m' else 0))
            if not math.isfinite(value) or (field.endswith('spacing_m') and value <= 0):
                raise serializers.ValidationError({field: 'Supply a finite value; spacing must be positive.'})
        if attrs.get('lane_count', getattr(self.instance, 'lane_count', 1)) < 1:
            raise serializers.ValidationError({'lane_count': 'At least one lane is required.'})
        reference = attrs.get('position_reference', getattr(self.instance, 'position_reference', 'fixed'))
        che = attrs.get('reference_che', getattr(self.instance, 'reference_che', None))
        if (reference == 'che') != bool(che):
            raise serializers.ValidationError({'reference_che': 'Select a CHE only for equipment-relative positions.'})
        if che and asset and che.location_id != asset.layout_version.location_id:
            raise serializers.ValidationError({'reference_che': 'Equipment belongs to another terminal.'})
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        count = validated_data.pop('position_count', None)
        interface = super().create(validated_data)
        try:
            sync_interface_positions(interface, count)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.messages)
        return interface

    @transaction.atomic
    def update(self, instance, validated_data):
        count = validated_data.pop('position_count', None)
        changed = set(validated_data)
        interface = super().update(instance, validated_data)
        if count is not None or changed:
            try:
                sync_interface_positions(interface, count)
            except DjangoValidationError as exc:
                raise serializers.ValidationError(exc.messages)
        return interface


class StorageGridSummarySerializer(serializers.ModelSerializer):
    layout_asset = serializers.PrimaryKeyRelatedField(read_only=True)
    pile_count = serializers.IntegerField(source='piles.count', read_only=True)
    position_count = serializers.SerializerMethodField()

    class Meta:
        model = StorageGrid
        fields = [
            'id', 'layout_asset', 'grid_kind', 'name', 'max_bays', 'max_rows',
            'max_tier', 'latitude', 'longitude', 'heading_deg',
            'slot_heading_deg', 'pile_length_m', 'pile_width_m', 'pile_height_m',
            'initial_bay', 'initial_row', 'bay_order', 'row_order',
            'bay_naming', 'row_naming', 'bay_use_heading_axis',
            'padding_top_m', 'padding_bottom_m', 'padding_left_m',
            'padding_right_m', 'bay_spacing_m', 'row_spacing_m', 'is_heapzone',
            'pile_count', 'position_count',
        ]
        extra_kwargs = {'layout_asset': {'required': False}, 'name': {'required': False}}

    def get_position_count(self, obj):
        return Position.objects.filter(pile__storage_grid=obj).count()


class LayoutAssetSerializer(GeoJSONGeometryMixin, serializers.ModelSerializer):
    layout_version = serializers.PrimaryKeyRelatedField(read_only=True)
    storage_grid_settings = serializers.JSONField(write_only=True, required=False)
    storage_grid = StorageGridSummarySerializer(read_only=True)
    linear_axes = LinearAxisSerializer(many=True, read_only=True)
    transfer_interfaces = TransferInterfaceSerializer(many=True, read_only=True)
    berth_segments = BerthSegmentSerializer(many=True, read_only=True)
    asset_kind_display = serializers.CharField(source='get_asset_kind_display', read_only=True)

    class Meta:
        model = LayoutAsset
        fields = [
            'id', 'layout_version', 'parent', 'access_policy', 'code', 'name', 'asset_kind',
            'asset_kind_display', 'geometry', 'heading_deg', 'elevation_m',
            'is_visible', 'is_active', 'metadata', 'storage_grid_settings',
            'storage_grid', 'linear_axes', 'transfer_interfaces',
            'berth_segments',
        ]
        extra_kwargs = {'layout_version': {'required': False}}

    def validate(self, attrs):
        attrs = super().validate(attrs)
        layout = getattr(self.instance, 'layout_version', self.context.get('layout_version'))
        parent = attrs.get('parent', getattr(self.instance, 'parent', None))
        policy = attrs.get('access_policy', getattr(self.instance, 'access_policy', None))
        if parent and layout and parent.layout_version_id != layout.pk:
            raise serializers.ValidationError({'parent': 'Parent belongs to another layout version.'})
        if policy and layout and policy.layout_version_id != layout.pk:
            raise serializers.ValidationError({'access_policy': 'Policy belongs to another layout version.'})
        if parent and policy and parent.layout_version_id != policy.layout_version_id:
            raise serializers.ValidationError({'access_policy': 'Policy and parent must use the same layout version.'})
        cursor, seen = parent, set()
        while cursor:
            if cursor.pk in seen or (self.instance and cursor.pk == self.instance.pk):
                raise serializers.ValidationError({'parent': 'Asset hierarchy cannot contain cycles.'})
            seen.add(cursor.pk)
            cursor = cursor.parent
        return attrs

    def _save_grid(self, asset, settings):
        try:
            existing = asset.storage_grid
        except StorageGrid.DoesNotExist:
            existing = None
        if asset.asset_kind != LayoutAsset.YARD_BLOCK:
            if existing:
                existing.delete()
            return
        if settings is None and existing:
            return
        if settings is None:
            raise serializers.ValidationError({
                'storage_grid_settings': 'A yard block requires storage grid settings.'
            })
        payload = {**settings, 'name': settings.get('name') or asset.name}
        serializer = StorageGridSummarySerializer(existing, data=payload, context=self.context)
        serializer.is_valid(raise_exception=True)
        grid = serializer.save(layout_asset=asset)
        try:
            sync_yard_grid(grid)
        except DjangoValidationError as exc:
            raise serializers.ValidationError({'storage_grid_settings': exc.messages})

    def _sync_terminal_origin(self, asset):
        if asset.asset_kind != LayoutAsset.TERMINAL:
            return
        try:
            longitude, latitude = geometry_bounds_center(asset.geometry)
        except DjangoValidationError as exc:
            raise serializers.ValidationError({'geometry': exc.messages})
        location = asset.layout_version.location
        location.map_origin_latitude = latitude
        location.map_origin_longitude = longitude
        location.save(update_fields=['map_origin_latitude', 'map_origin_longitude'])

    @transaction.atomic
    def create(self, validated_data):
        settings = validated_data.pop('storage_grid_settings', None)
        asset = super().create(validated_data)
        self._save_grid(asset, settings)
        sync_block_crane_rail(asset)
        self._sync_terminal_origin(asset)
        return asset

    @transaction.atomic
    def update(self, instance, validated_data):
        settings = validated_data.pop('storage_grid_settings', None)
        old_geometry, old_heading = instance.geometry, instance.heading_deg
        road_axes = list(instance.linear_axes.filter(axis_kind=LinearAxis.ROAD_CENTERLINE)) \
            if instance.asset_kind == LayoutAsset.ROAD else []
        previous_road_points = {
            axis.pk: [list(point) for point in axis.points or []]
            for axis in road_axes
        }
        asset = super().update(instance, validated_data)
        try:
            move_asset_dependents(
                asset,
                old_geometry,
                old_heading,
                skip_root_road_axes=asset.asset_kind == LayoutAsset.ROAD,
            )
            if asset.asset_kind == LayoutAsset.ROAD and asset.geometry.get('type') == 'LineString':
                for axis in road_axes:
                    axis.points = asset.geometry.get('coordinates', [])
                    axis.save(update_fields=['points'])
                    sync_axis_graph(axis, previous_points=previous_road_points[axis.pk])
        except DjangoValidationError as exc:
            raise serializers.ValidationError({'geometry': exc.messages})
        self._save_grid(asset, settings)
        sync_block_crane_rail(asset)
        self._sync_terminal_origin(asset)
        return asset


class LayoutVersionSerializer(serializers.ModelSerializer):
    location = serializers.PrimaryKeyRelatedField(read_only=True)
    asset_count = serializers.IntegerField(source='assets.count', read_only=True)
    node_count = serializers.IntegerField(source='route_nodes.count', read_only=True)
    edge_count = serializers.IntegerField(source='route_edges.count', read_only=True)

    class Meta:
        model = LayoutVersion
        fields = [
            'id', 'location', 'version', 'name', 'status', 'is_current',
            'based_on', 'notes', 'created_by', 'created_at', 'updated_at',
            'published_at', 'asset_count', 'node_count', 'edge_count',
        ]
        read_only_fields = ['version', 'created_by', 'created_at', 'updated_at', 'published_at']
        extra_kwargs = {'location': {'required': False}}


class OperationalPositionSerializer(serializers.ModelSerializer):
    layout_version = serializers.PrimaryKeyRelatedField(read_only=True)

    def to_representation(self, instance):
        result = super().to_representation(instance)
        if instance.interface_position_id:
            point = resolved_interface_point(instance.interface_position)
            result.update(longitude=point[0] if point else None, latitude=point[1] if point else None)
        return result

    class Meta:
        model = OperationalPosition
        fields = '__all__'
        extra_kwargs = {'layout_version': {'required': False}}

    def validate(self, attrs):
        attrs = super().validate(attrs)
        layout = getattr(self.instance, 'layout_version', self.context.get('layout_version'))
        related = {
            'asset': attrs.get('asset', getattr(self.instance, 'asset', None)),
            'axis': attrs.get('axis', getattr(self.instance, 'axis', None)),
            'stack_position': attrs.get('stack_position', getattr(self.instance, 'stack_position', None)),
            'interface_position': attrs.get('interface_position', getattr(self.instance, 'interface_position', None)),
        }
        layout_ids = {
            'asset': lambda value: value.layout_version_id,
            'axis': lambda value: value.asset.layout_version_id,
            'stack_position': lambda value: value.pile.storage_grid.layout_asset.layout_version_id,
            'interface_position': lambda value: value.transfer_interface.asset.layout_version_id,
        }
        for field, value in related.items():
            if value and layout and layout_ids[field](value) != layout.pk:
                raise serializers.ValidationError({field: 'Object belongs to another layout version.'})
        latitude = attrs.get('latitude', getattr(self.instance, 'latitude', None))
        longitude = attrs.get('longitude', getattr(self.instance, 'longitude', None))
        if (latitude is None) != (longitude is None):
            raise serializers.ValidationError({'latitude': 'Latitude and longitude must be supplied together.'})
        return attrs


class RouteNodeSerializer(serializers.ModelSerializer):
    layout_version = serializers.PrimaryKeyRelatedField(read_only=True)

    def to_representation(self, instance):
        result = super().to_representation(instance)
        result['point'] = resolved_route_node_point(instance)
        return result

    class Meta:
        model = RouteNode
        fields = '__all__'
        read_only_fields = ['transfer_interface', 'is_generated']
        extra_kwargs = {'layout_version': {'required': False}}

    def validate_point(self, value):
        if not isinstance(value, dict) or value.get('type') != 'Point':
            raise serializers.ValidationError('A route node requires GeoJSON Point geometry.')
        return value

    def validate(self, attrs):
        attrs = super().validate(attrs)
        layout = getattr(self.instance, 'layout_version', self.context.get('layout_version'))
        asset = attrs.get('asset', getattr(self.instance, 'asset', None))
        position = attrs.get('operational_position', getattr(self.instance, 'operational_position', None))
        transfer_interface = attrs.get('transfer_interface', getattr(self.instance, 'transfer_interface', None))
        if asset and layout and asset.layout_version_id != layout.pk:
            raise serializers.ValidationError({'asset': 'Asset belongs to another layout version.'})
        if position and layout and position.layout_version_id != layout.pk:
            raise serializers.ValidationError({'operational_position': 'Position belongs to another layout version.'})
        if transfer_interface and layout and transfer_interface.layout_version.pk != layout.pk:
            raise serializers.ValidationError({'transfer_interface': 'Interface belongs to another layout version.'})
        return attrs

    @transaction.atomic
    def update(self, instance, validated_data):
        node = super().update(instance, validated_data)
        connected_edges = RouteEdge.objects.filter(
            models.Q(from_node=node) | models.Q(to_node=node),
            is_generated=False,
        ).select_related('from_node', 'to_node')
        for edge in connected_edges:
            edge.distance_m = geographic_distance_m(
                edge.from_node.point['coordinates'], edge.to_node.point['coordinates'],
            )
            edge.save(update_fields=['distance_m'])
        return node


class RouteEdgeRequirementSerializer(serializers.ModelSerializer):
    class Meta:
        model = RouteEdgeRequirement
        fields = ['id', 'route_edge', 'component_type', 'role', 'minimum_quantity']
        extra_kwargs = {'route_edge': {'required': False}}


class RouteEdgeSerializer(serializers.ModelSerializer):
    layout_version = serializers.PrimaryKeyRelatedField(read_only=True)
    requirements = RouteEdgeRequirementSerializer(many=True, required=False)

    def to_representation(self, instance):
        result = super().to_representation(instance)
        first, last = resolved_route_node_point(instance.from_node), resolved_route_node_point(instance.to_node)
        result['distance_m'] = geographic_distance_m(first['coordinates'], last['coordinates']) if first and last else None
        return result

    class Meta:
        model = RouteEdge
        fields = [
            'id', 'layout_version', 'code', 'name', 'from_node', 'to_node',
            'edge_kind', 'movement_mode', 'source_axis', 'access_policy',
            'distance_m', 'nominal_duration_seconds', 'capacity', 'base_cost',
            'is_generated', 'is_enabled', 'metadata', 'requirements',
        ]
        read_only_fields = ['is_generated']
        extra_kwargs = {
            'layout_version': {'required': False},
            'code': {'required': False, 'allow_blank': True},
        }

    @staticmethod
    def _derived_code(first, second):
        return f'{first.code[:45]}__TO__{second.code[:45]}'

    def validate(self, attrs):
        attrs = super().validate(attrs)
        layout = attrs.get('layout_version', getattr(self.instance, 'layout_version', self.context.get('layout_version')))
        first = attrs.get('from_node', getattr(self.instance, 'from_node', None))
        second = attrs.get('to_node', getattr(self.instance, 'to_node', None))
        source_axis = attrs.get('source_axis', getattr(self.instance, 'source_axis', None))
        policy = attrs.get('access_policy', getattr(self.instance, 'access_policy', None))
        if first and second and first.pk == second.pk:
            raise serializers.ValidationError({'to_node': 'An edge must connect two different nodes.'})
        for field, value in [('from_node', first), ('to_node', second)]:
            if value and layout and value.layout_version_id != layout.pk:
                raise serializers.ValidationError({field: 'Node belongs to another layout version.'})
        if first and second and first.layout_version_id != second.layout_version_id:
            raise serializers.ValidationError({'to_node': 'Both nodes must belong to the same layout version.'})
        if source_axis and layout and source_axis.asset.layout_version_id != layout.pk:
            raise serializers.ValidationError({'source_axis': 'Axis belongs to another layout version.'})
        if policy and layout and policy.layout_version_id != layout.pk:
            raise serializers.ValidationError({'access_policy': 'Policy belongs to another layout version.'})
        if first and second:
            attrs['code'] = self._derived_code(first, second)
            if not attrs.get('name'):
                attrs['name'] = f'{first.name} to {second.name}'[:140]
            duplicate = RouteEdge.objects.filter(layout_version=layout, code=attrs['code'])
            if self.instance:
                duplicate = duplicate.exclude(pk=self.instance.pk)
            if duplicate.exists():
                raise serializers.ValidationError({'to_node': 'This routing connection already exists.'})
        return attrs

    def _save_requirements(self, edge, requirements):
        edge.requirements.all().delete()
        RouteEdgeRequirement.objects.bulk_create([
            RouteEdgeRequirement(route_edge=edge, **requirement)
            for requirement in requirements
        ])

    @transaction.atomic
    def create(self, validated_data):
        requirements = validated_data.pop('requirements', [])
        edge = super().create(validated_data)
        self._save_requirements(edge, requirements)
        return edge

    @transaction.atomic
    def update(self, instance, validated_data):
        requirements = validated_data.pop('requirements', None)
        edge = super().update(instance, validated_data)
        if requirements is not None:
            self._save_requirements(edge, requirements)
        return edge


class CheOperatingScopeSerializer(serializers.ModelSerializer):
    class Meta:
        model = CheOperatingScope
        fields = '__all__'

    def validate(self, attrs):
        attrs = super().validate(attrs)
        che = attrs.get('che', getattr(self.instance, 'che', None))
        equipment_type = attrs.get('equipment_type', getattr(self.instance, 'equipment_type', None))
        asset = attrs.get('asset', getattr(self.instance, 'asset', None))
        axis = attrs.get('axis', getattr(self.instance, 'axis', None))
        service_lane_axis = attrs.get('service_lane_axis', getattr(self.instance, 'service_lane_axis', None))
        min_chainage = attrs.get('min_chainage_m', getattr(self.instance, 'min_chainage_m', None))
        max_chainage = attrs.get('max_chainage_m', getattr(self.instance, 'max_chainage_m', None))
        bay_start = attrs.get('bay_start', getattr(self.instance, 'bay_start', None))
        bay_end = attrs.get('bay_end', getattr(self.instance, 'bay_end', None))
        row_start = attrs.get('row_start', getattr(self.instance, 'row_start', None))
        row_end = attrs.get('row_end', getattr(self.instance, 'row_end', None))
        errors = {}
        if bool(che) == bool(equipment_type):
            errors['che'] = 'Select exactly one CHE or equipment type.'
        if not asset and not axis:
            errors['asset'] = 'Select an asset or axis for this operating scope.'
        if asset and axis and asset.layout_version_id != axis.asset.layout_version_id:
            errors['axis'] = 'Axis and asset must belong to the same layout version.'
        topology = asset or (axis.asset if axis else None)
        if service_lane_axis:
            if service_lane_axis.axis_kind != LinearAxis.ROAD_CENTERLINE:
                errors['service_lane_axis'] = 'Select a road-centerline lane.'
            elif topology and service_lane_axis.asset.layout_version_id != topology.layout_version_id:
                errors['service_lane_axis'] = 'Service lane belongs to another layout version.'
            elif topology and service_lane_axis.asset_id != topology.id:
                errors['service_lane_axis'] = 'Service lane must belong to the scoped layout asset.'
        if che and (asset or axis):
            layout_location_id = asset.layout_version.location_id if asset else axis.asset.layout_version.location_id
            if che.location_id != layout_location_id:
                errors['che'] = 'CHE and operating scope must belong to the same terminal.'
        if min_chainage is not None and max_chainage is not None and min_chainage > max_chainage:
            errors['max_chainage_m'] = 'Maximum chainage must be at least minimum chainage.'
        if (min_chainage is not None or max_chainage is not None) and not axis:
            errors['axis'] = 'Chainage limits require a movement axis.'
        if axis and len(axis.points or []) >= 2:
            length = polyline_length_m(axis.points)
            if min_chainage is not None and not 0 <= min_chainage <= length:
                errors['min_chainage_m'] = f'Minimum chainage must be between 0 and {length:.2f} m.'
            if max_chainage is not None and not 0 <= max_chainage <= length:
                errors['max_chainage_m'] = f'Maximum chainage must be between 0 and {length:.2f} m.'
        if bay_start is not None and bay_end is not None and bay_start > bay_end:
            errors['bay_end'] = 'End bay must be at least start bay.'
        if row_start is not None and row_end is not None and row_start > row_end:
            errors['row_end'] = 'End row must be at least start row.'
        if errors:
            raise serializers.ValidationError(errors)
        return attrs


class LayoutPresetSerializer(serializers.ModelSerializer):
    class Meta:
        model = LayoutPreset
        fields = '__all__'


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
    ches = CheSerializer(many=True, read_only=True)
    layout_versions = LayoutVersionSerializer(many=True, read_only=True)
    current_layout = serializers.SerializerMethodField()
    timezone_utc_offset_minutes = serializers.SerializerMethodField()
    local_datetime = serializers.SerializerMethodField()

    def _local_now(self, obj):
        return django_timezone.now().astimezone(ZoneInfo(obj.timezone or 'UTC'))

    def get_timezone_utc_offset_minutes(self, obj):
        return int(self._local_now(obj).utcoffset().total_seconds() // 60)

    def get_local_datetime(self, obj):
        return self._local_now(obj).isoformat()

    def get_current_layout(self, obj):
        layout = obj.layout_versions.filter(is_current=True).first()
        return LayoutVersionSerializer(layout).data if layout else None

    class Meta:
        model = Location
        fields = [
            'pk', 'name', 'location_type',
            'latitude', 'longitude', 'map_origin_latitude', 'map_origin_longitude',
            'map_width_m', 'map_height_m', 'map_units_per_meter',
            'default_zoom', 'angle', 'timezone', 'timezone_utc_offset_minutes', 'local_datetime',
            'ches', 'layout_versions', 'current_layout'
        ]
