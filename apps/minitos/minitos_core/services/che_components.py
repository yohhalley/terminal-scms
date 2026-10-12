from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from ..models import (
    Battery,
    CheComponent,
    ComponentType,
    CurrentSignalValue,
    EnergyTank,
    Engine,
    EquipmentType,
    EquipmentTypeComponent,
    MotionAxis,
    OperationalStateInterval,
    SignalBinding,
    SignalDefinition,
    Spreader,
)


COMPONENT_CATALOG = {
    ComponentType.CHE: {
        'name': 'CHE', 'tic40_subject': 'che',
        'description': 'Root subject representing the complete cargo handling equipment.',
    },
    ComponentType.DRIVE: {
        'name': 'Drive', 'tic40_subject': 'drive', 'is_motion_component': True,
    },
    ComponentType.HOIST: {
        'name': 'Hoist', 'tic40_subject': 'hoist', 'is_motion_component': True,
    },
    ComponentType.TROLLEY: {
        'name': 'Trolley', 'tic40_subject': 'trolley', 'is_motion_component': True,
    },
    ComponentType.GANTRY: {
        'name': 'Gantry', 'tic40_subject': 'gantry', 'is_motion_component': True,
    },
    ComponentType.BOOM: {
        'name': 'Boom', 'tic40_subject': 'boom', 'is_motion_component': True,
    },
    ComponentType.SPREADER: {
        'name': 'Spreader', 'tic40_subject': 'spreader',
    },
    ComponentType.POWER_SOURCE: {
        'name': 'Energy supply', 'tic40_subject': 'energysupply',
        'is_energy_component': True, 'supports_multiple': True,
    },
    ComponentType.ENERGY_STORE: {
        'name': 'Energy storage system', 'tic40_subject': 'energystoragesystem',
        'is_energy_component': True, 'supports_multiple': True,
    },
    ComponentType.OTHER: {
        'name': 'Other component', 'tic40_subject': 'component',
        'supports_multiple': True,
    },
}


SIGNAL_CATALOG = [
    # Root CHE facts and pose.
    (ComponentType.CHE, 'che.on.status', 'On', 'che|on|status|actual', 'boolean', '', 'state', True),
    (ComponentType.CHE, 'che.working.status', 'Working', 'che|working|status|actual', 'boolean', '', 'state', True),
    (ComponentType.CHE, 'che.available.status', 'Dispatch available', '', 'boolean', '', 'state', False),
    (ComponentType.CHE, 'che.dispatched.status', 'Dispatched', '', 'boolean', '', 'state', False),
    (ComponentType.CHE, 'che.maintenance.status', 'In maintenance', '', 'boolean', '', 'state', False),
    (ComponentType.CHE, 'che.communication.online', 'Communications online', '', 'boolean', '', 'state', False),
    (ComponentType.CHE, 'che.location.latitude', 'Latitude', 'che|location|coordinate|actual', 'float', 'degree', 'current', False),
    (ComponentType.CHE, 'che.location.longitude', 'Longitude', 'che|location|coordinate|actual', 'float', 'degree', 'current', False),
    (ComponentType.CHE, 'che.heading.angle', 'Heading', 'che|location|angle|actual', 'float', 'degree', 'current', False),
    (ComponentType.CHE, 'che.on.total_hours', 'CHE operating hours', 'che|on|totaltimer|actual', 'float', 'hour', 'cumulative', False),
    # Movement components.
    (ComponentType.DRIVE, 'drive.driving.speed', 'Driving speed', 'che|drive|driving|speed|actual', 'float', 'meter_per_second', 'current', True),
    (ComponentType.DRIVE, 'drive.steering.angle', 'Steering angle', '', 'float', 'degree', 'current', False),
    (ComponentType.DRIVE, 'drive.distance.total', 'Distance travelled', '', 'float', 'meter', 'cumulative', False),
    (ComponentType.HOIST, 'hoist.hoisting.speed', 'Hoisting speed', 'che|hoist|hoisting|speed|actual', 'float', 'meter_per_second', 'current', True),
    (ComponentType.HOIST, 'hoist.position.height', 'Hoist height', 'che|hoist|location|height|actual', 'float', 'meter', 'current', False),
    (ComponentType.HOIST, 'hoist.load.weight', 'Hoist load weight', '', 'float', 'kilogram', 'current', False),
    (ComponentType.TROLLEY, 'trolley.trolleying.speed', 'Trolley speed', 'che|trolley|trolleying|speed|actual', 'float', 'meter_per_second', 'current', True),
    (ComponentType.TROLLEY, 'trolley.position', 'Trolley position', '', 'float', 'meter', 'current', False),
    (ComponentType.GANTRY, 'gantry.gantrying.speed', 'Gantry speed', 'che|gantry|gantrying|speed|actual', 'float', 'meter_per_second', 'current', True),
    (ComponentType.GANTRY, 'gantry.position', 'Gantry position', '', 'float', 'meter', 'current', False),
    (ComponentType.BOOM, 'boom.booming.speed', 'Boom speed', 'che|boom|booming|speed|actual', 'float', 'meter_per_second', 'current', True),
    (ComponentType.BOOM, 'boom.position.angle', 'Boom angle', '', 'float', 'degree', 'current', False),
    # Load handling.
    (ComponentType.SPREADER, 'spreader.locked.status', 'Twistlocks locked', 'che|spreader|locked|status|actual', 'boolean', '', 'state', False),
    (ComponentType.SPREADER, 'spreader.landed.status', 'Spreader landed', 'che|spreader|landed|status|actual', 'boolean', '', 'state', False),
    (ComponentType.SPREADER, 'spreader.container.present', 'Container present', '', 'boolean', '', 'state', False),
    (ComponentType.SPREADER, 'spreader.size', 'Spreader size', 'che|spreader|size|actual', 'integer', 'foot', 'current', False),
    # Power source.
    (ComponentType.POWER_SOURCE, 'energysupply.running.status', 'Energy supply running', 'che|energysupply|running|status|actual', 'boolean', '', 'state', True),
    (ComponentType.POWER_SOURCE, 'energysupply.running.speed', 'Energy supply speed', 'che|energysupply|running|speed|actual', 'float', 'rpm', 'current', False),
    (ComponentType.POWER_SOURCE, 'energysupply.working.load', 'Energy supply load', 'che|energysupply|working|load|actual', 'float', 'percent', 'current', False),
    (ComponentType.POWER_SOURCE, 'energysupply.running.total_hours', 'Energy supply hours', 'che|energysupply|on|totaltimer|actual', 'float', 'hour', 'cumulative', False),
    (ComponentType.POWER_SOURCE, 'energysupply.fuel.consumption_rate', 'Fuel consumption rate', '', 'float', 'litre_per_hour', 'current', False),
    # Fuel and battery energy stores.
    (ComponentType.ENERGY_STORE, 'energystoragesystem.energy.level', 'Energy level', 'che|energystoragesystem|energy|level|actual', 'float', 'percent', 'current', False),
    (ComponentType.ENERGY_STORE, 'energystoragesystem.energy.consumed.total', 'Energy consumed', 'che|energy|totalconsumed', 'float', '', 'cumulative', False),
    (ComponentType.ENERGY_STORE, 'battery.state_of_health', 'Battery state of health', '', 'float', 'percent', 'current', False),
    (ComponentType.ENERGY_STORE, 'battery.voltage', 'Battery voltage', '', 'float', 'volt', 'current', False),
    (ComponentType.ENERGY_STORE, 'battery.current', 'Battery current', 'che|energytank|energy|current|actual', 'float', 'ampere', 'current', False),
    (ComponentType.ENERGY_STORE, 'battery.power', 'Battery power', '', 'float', 'kilowatt', 'current', False),
    (ComponentType.ENERGY_STORE, 'battery.temperature', 'Battery temperature', '', 'float', 'celsius', 'current', False),
    (ComponentType.ENERGY_STORE, 'battery.charging.status', 'Battery charging', '', 'boolean', '', 'state', False),
]


MOTION_COMPONENTS_BY_EQUIPMENT = {
    EquipmentType.TT: [ComponentType.DRIVE],
    EquipmentType.AVG: [ComponentType.DRIVE],
    EquipmentType.SC: [ComponentType.DRIVE, ComponentType.HOIST, ComponentType.SPREADER],
    EquipmentType.AS: [ComponentType.DRIVE, ComponentType.HOIST, ComponentType.SPREADER],
    EquipmentType.RS: [ComponentType.DRIVE, ComponentType.BOOM, ComponentType.HOIST, ComponentType.SPREADER],
    EquipmentType.TH: [ComponentType.DRIVE, ComponentType.BOOM, ComponentType.HOIST, ComponentType.SPREADER],
    EquipmentType.EH: [ComponentType.DRIVE, ComponentType.BOOM, ComponentType.HOIST, ComponentType.SPREADER],
    EquipmentType.FL: [ComponentType.DRIVE, ComponentType.HOIST],
    EquipmentType.RTG: [ComponentType.GANTRY, ComponentType.TROLLEY, ComponentType.HOIST, ComponentType.SPREADER],
    EquipmentType.ARTG: [ComponentType.GANTRY, ComponentType.TROLLEY, ComponentType.HOIST, ComponentType.SPREADER],
    EquipmentType.RGC: [ComponentType.GANTRY, ComponentType.TROLLEY, ComponentType.HOIST, ComponentType.SPREADER],
    EquipmentType.RMG: [ComponentType.GANTRY, ComponentType.TROLLEY, ComponentType.HOIST, ComponentType.SPREADER],
    EquipmentType.ARMG: [ComponentType.GANTRY, ComponentType.TROLLEY, ComponentType.HOIST, ComponentType.SPREADER],
    EquipmentType.STS: [ComponentType.GANTRY, ComponentType.TROLLEY, ComponentType.HOIST, ComponentType.BOOM, ComponentType.SPREADER],
}


MOBILE_EQUIPMENT = {
    EquipmentType.TT, EquipmentType.AVG, EquipmentType.SC, EquipmentType.AS,
    EquipmentType.RS, EquipmentType.TH, EquipmentType.EH, EquipmentType.FL,
    EquipmentType.RTG, EquipmentType.ARTG,
}

MOTION_SIGNAL_CODES = {
    'drive.driving.speed', 'hoist.hoisting.speed', 'trolley.trolleying.speed',
    'gantry.gantrying.speed', 'boom.booming.speed',
}


def ensure_component_catalog():
    result = {}
    for code, defaults in COMPONENT_CATALOG.items():
        component_type, _ = ComponentType.objects.update_or_create(code=code, defaults=defaults)
        result[code] = component_type
    return result


def ensure_signal_catalog(component_types=None):
    component_types = component_types or ensure_component_catalog()
    for component_code, code, name, path, data_type, unit, value_kind, oee in SIGNAL_CATALOG:
        SignalDefinition.objects.update_or_create(
            code=code,
            defaults={
                'component_type': component_types[component_code],
                'name': name,
                'tic40_path': path,
                'data_type': data_type,
                'unit': unit,
                'value_kind': value_kind,
                'standard_version': '2026.019',
                'is_required_for_oee': oee,
            },
        )


def _pending_defaults(equipment_type):
    return dict(getattr(equipment_type, '_pending_component_defaults', {}))


def _motion_configuration(equipment_type, component_code, pending):
    existing = equipment_type.component_profiles.filter(role=component_code).first()
    config = dict(existing.default_configuration) if existing else {}
    if component_code == ComponentType.DRIVE:
        empty = pending.get('driving_speed_empty_kmh', config.get('maximum_speed_empty_m_s', 0) * 3.6)
        loaded = pending.get('driving_speed_full_load_kmh', config.get('maximum_speed_loaded_m_s', 0) * 3.6)
        config.update({
            'maximum_speed_empty_m_s': float(empty or 0) / 3.6,
            'maximum_speed_loaded_m_s': float(loaded or 0) / 3.6,
            'turning_radius_m': float(pending.get('turning_radius_m', config.get('turning_radius_m', 0)) or 0),
        })
    elif component_code == ComponentType.HOIST:
        empty = pending.get('lifting_speed_empty_m_per_min', config.get('maximum_speed_empty_m_s', 0) * 60)
        loaded = pending.get('lifting_speed_full_load_m_per_min', config.get('maximum_speed_loaded_m_s', 0) * 60)
        config.update({
            'maximum_speed_empty_m_s': float(empty or 0) / 60,
            'maximum_speed_loaded_m_s': float(loaded or 0) / 60,
        })
    config.setdefault('motion_deadband_m_s', 0.01)
    config['_managed_by_minitos'] = True
    return config


def _profile_specs(equipment_type):
    pending = _pending_defaults(equipment_type)
    specs = [{
        'role': 'equipment', 'component_code': ComponentType.CHE,
        'display_name': 'Equipment', 'parent_role': '', 'configuration': {'_managed_by_minitos': True},
    }]
    for component_code in MOTION_COMPONENTS_BY_EQUIPMENT.get(equipment_type.equipment_type, []):
        configuration = _motion_configuration(equipment_type, component_code, pending)
        if component_code == ComponentType.SPREADER:
            configuration.update({
                'twin_capable': equipment_type.is_twin_capeable,
                'supported_sizes_ft': [20, 40, 45],
            })
        specs.append({
            'role': component_code,
            'component_code': component_code,
            'display_name': COMPONENT_CATALOG[component_code]['name'],
            'parent_role': 'equipment',
            'configuration': configuration,
        })

    energy_source = equipment_type.energy_source
    if energy_source in {EquipmentType.Diesel, EquipmentType.Gas, EquipmentType.Hybrid}:
        existing = equipment_type.component_profiles.filter(role='engine').first()
        engine_config = dict(existing.default_configuration) if existing else {}
        engine_config.update({
            'engine_type': pending.get('engine_type', engine_config.get('engine_type', '')) or '',
            'fuel_type': energy_source.lower(),
            'rated_power_kw': float(pending.get('power_rating_kw', engine_config.get('rated_power_kw', 0)) or 0),
            '_managed_by_minitos': True,
        })
        specs.append({
            'role': 'engine', 'component_code': ComponentType.POWER_SOURCE,
            'display_name': 'Engine', 'parent_role': 'equipment', 'configuration': engine_config,
        })
        existing = equipment_type.component_profiles.filter(role='fuel_tank').first()
        tank_config = dict(existing.default_configuration) if existing else {}
        tank_config.update({
            'fuel_type': energy_source.lower(),
            'capacity_l': float(pending.get('fuel_tank_capacity', tank_config.get('capacity_l', 0)) or 0),
            '_managed_by_minitos': True,
        })
        specs.append({
            'role': 'fuel_tank', 'component_code': ComponentType.ENERGY_STORE,
            'display_name': 'Fuel tank', 'parent_role': 'engine', 'configuration': tank_config,
        })

    if energy_source in {EquipmentType.Electric, EquipmentType.Hybrid}:
        existing = equipment_type.component_profiles.filter(role='electric_power_source').first()
        power_config = dict(existing.default_configuration) if existing else {}
        power_config.update({
            'rated_power_kw': float(pending.get('power_rating_kw', power_config.get('rated_power_kw', 0)) or 0),
            '_managed_by_minitos': True,
        })
        specs.append({
            'role': 'electric_power_source', 'component_code': ComponentType.POWER_SOURCE,
            'display_name': 'Electric power source', 'parent_role': 'equipment',
            'configuration': power_config,
        })
        if equipment_type.equipment_type in MOBILE_EQUIPMENT:
            existing = equipment_type.component_profiles.filter(role='battery').first()
            battery_config = dict(existing.default_configuration) if existing else {}
            legacy_capacity = pending.get('fuel_tank_capacity')
            if legacy_capacity is not None:
                battery_config['capacity_kwh'] = float(legacy_capacity or 0)
            battery_config.setdefault('capacity_kwh', 0.0)
            battery_config['_managed_by_minitos'] = True
            specs.append({
                'role': 'battery', 'component_code': ComponentType.ENERGY_STORE,
                'display_name': 'Battery', 'parent_role': 'electric_power_source',
                'configuration': battery_config,
            })
    return specs


@transaction.atomic
def sync_equipment_type_components(equipment_type):
    component_types = ensure_component_catalog()
    ensure_signal_catalog(component_types)
    desired_roles = set()
    for spec in _profile_specs(equipment_type):
        desired_roles.add(spec['role'])
        profile, created = EquipmentTypeComponent.objects.get_or_create(
            equipment_type=equipment_type,
            role=spec['role'],
            defaults={
                'component_type': component_types[spec['component_code']],
                'display_name': spec['display_name'],
                'parent_role': spec['parent_role'],
                'default_configuration': spec['configuration'],
            },
        )
        if not created:
            profile.component_type = component_types[spec['component_code']]
            profile.display_name = spec['display_name']
            profile.parent_role = spec['parent_role']
            profile.default_configuration = spec['configuration']
            profile.is_active = True
            profile.save(update_fields=[
                'component_type', 'display_name', 'parent_role',
                'default_configuration', 'is_active',
            ])
    for profile in equipment_type.component_profiles.exclude(role__in=desired_roles):
        if profile.default_configuration.get('_managed_by_minitos'):
            profile.is_active = False
            profile.save(update_fields=['is_active'])
    if hasattr(equipment_type, '_pending_component_defaults'):
        del equipment_type._pending_component_defaults


def _create_typed_component_details(component, configuration):
    code = component.component_type.code
    if code in {
        ComponentType.DRIVE, ComponentType.HOIST, ComponentType.TROLLEY,
        ComponentType.GANTRY, ComponentType.BOOM,
    }:
        MotionAxis.objects.get_or_create(
            component=component,
            defaults={
                'maximum_speed_empty_m_s': configuration.get('maximum_speed_empty_m_s', 0),
                'maximum_speed_loaded_m_s': configuration.get('maximum_speed_loaded_m_s', 0),
                'maximum_acceleration_m_s2': configuration.get('maximum_acceleration_m_s2', 0),
                'maximum_deceleration_m_s2': configuration.get('maximum_deceleration_m_s2', 0),
                'motion_deadband_m_s': configuration.get('motion_deadband_m_s', 0.01),
                'minimum_position_m': configuration.get('minimum_position_m'),
                'maximum_position_m': configuration.get('maximum_position_m'),
                'turning_radius_m': configuration.get('turning_radius_m', 0),
            },
        )
    elif component.role == 'engine':
        Engine.objects.get_or_create(
            component=component,
            defaults={
                'engine_type': configuration.get('engine_type', ''),
                'fuel_type': configuration.get('fuel_type', 'diesel'),
                'rated_power_kw': configuration.get('rated_power_kw', 0),
                'nominal_rpm': configuration.get('nominal_rpm', 0),
            },
        )
    elif component.role == 'battery':
        Battery.objects.get_or_create(
            component=component,
            defaults={
                'chemistry': configuration.get('chemistry', ''),
                'capacity_kwh': configuration.get('capacity_kwh', 0),
                'nominal_voltage_v': configuration.get('nominal_voltage_v', 0),
                'max_charge_power_kw': configuration.get('max_charge_power_kw', 0),
                'max_discharge_power_kw': configuration.get('max_discharge_power_kw', 0),
                'minimum_soc_percent': configuration.get('minimum_soc_percent', 0),
                'maximum_soc_percent': configuration.get('maximum_soc_percent', 100),
            },
        )
    elif component.role == 'fuel_tank':
        EnergyTank.objects.get_or_create(
            component=component,
            defaults={
                'fuel_type': configuration.get('fuel_type', 'diesel'),
                'capacity_l': configuration.get('capacity_l', 0),
            },
        )
    elif code == ComponentType.SPREADER:
        Spreader.objects.get_or_create(
            component=component,
            defaults={
                'twin_capable': configuration.get('twin_capable', component.che.equipment_type.is_twin_capeable),
                'tandem_capable': configuration.get('tandem_capable', False),
                'supported_sizes_ft': configuration.get('supported_sizes_ft', [20, 40, 45]),
            },
        )


@transaction.atomic
def sync_che_components(che):
    sync_equipment_type_components(che.equipment_type)
    profiles = list(che.equipment_type.component_profiles.filter(is_active=True).select_related('component_type'))
    components = {}
    desired_roles = set()
    for profile in profiles:
        for index in range(1, profile.quantity + 1):
            role = profile.role if profile.quantity == 1 else f'{profile.role}_{index}'
            desired_roles.add(role)
            display_name = profile.display_name if profile.quantity == 1 else f'{profile.display_name} {index}'
            component, _ = CheComponent.objects.update_or_create(
                che=che,
                role=role,
                defaults={
                    'component_type': profile.component_type,
                    'template': profile,
                    'name': display_name,
                    'is_active': profile.is_active,
                },
            )
            components[role] = component
            _create_typed_component_details(component, profile.default_configuration)
    for profile in profiles:
        if not profile.parent_role:
            continue
        for index in range(1, profile.quantity + 1):
            role = profile.role if profile.quantity == 1 else f'{profile.role}_{index}'
            component = components[role]
            parent = components.get(profile.parent_role)
            if component.parent_id != (parent.pk if parent else None):
                component.parent = parent
                component.save(update_fields=['parent'])
    che.components.filter(template__isnull=False).exclude(role__in=desired_roles).update(
        is_active=False
    )
    return components


def set_current_signal(component, signal_code, value, source_protocol=SignalBinding.SIMULATOR,
                       source_timestamp=None, quality=CurrentSignalValue.GOOD, sequence=None):
    definition = SignalDefinition.objects.get(code=signal_code)
    if definition.component_type_id != component.component_type_id:
        raise ValueError(f'{signal_code} is not valid for component type {component.component_type.code}.')
    value = validate_signal_value(definition, value)
    binding, _ = SignalBinding.objects.get_or_create(
        component=component,
        signal_definition=definition,
        defaults={'source_protocol': source_protocol, 'source_metric': signal_code},
    )
    if binding.source_protocol != source_protocol:
        binding.source_protocol = source_protocol
        binding.save(update_fields=['source_protocol'])
    current, _ = CurrentSignalValue.objects.update_or_create(
        binding=binding,
        defaults={
            'value': value,
            'source_timestamp': source_timestamp or timezone.now(),
            'quality': quality,
            'sequence': sequence,
        },
    )
    return current


def validate_signal_value(definition, value):
    if value is None:
        return None
    if definition.data_type == SignalDefinition.BOOLEAN:
        if not isinstance(value, bool):
            raise ValidationError('A boolean signal requires true or false.')
        return value
    if definition.data_type == SignalDefinition.INTEGER:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValidationError('An integer signal requires a whole number.')
        return value
    if definition.data_type == SignalDefinition.FLOAT:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValidationError('A float signal requires a numeric value.')
        return float(value)
    if definition.data_type == SignalDefinition.STRING:
        if not isinstance(value, str):
            raise ValidationError('A string signal requires text.')
        return value
    return value


def evaluate_che_activity(che, source='signal-evaluator', timestamp=None):
    motion_values = CurrentSignalValue.objects.filter(
        binding__component__che=che,
        binding__signal_definition__code__in=MOTION_SIGNAL_CODES,
        binding__is_enabled=True,
        quality=CurrentSignalValue.GOOD,
    ).select_related('binding__component__motion_axis')
    motion_active = False
    for current in motion_values:
        try:
            speed = abs(float(current.value or 0))
        except (TypeError, ValueError):
            continue
        component = current.binding.component
        deadband = (
            component.motion_axis.motion_deadband_m_s
            if hasattr(component, 'motion_axis') else 0.01
        )
        if speed > deadband:
            motion_active = True
            break
    working = bool(che.powered_on and (che.is_dispatched or motion_active))
    if che.working != working:
        type(che).objects.filter(pk=che.pk).update(working=working, state_updated_at=timestamp or timezone.now())
        che.working = working
        sync_che_summary_signals(che, source_protocol=SignalBinding.SIMULATOR)
    return transition_operational_state(che, source=source, timestamp=timestamp)


@transaction.atomic
def record_signal_value(binding, value, source_timestamp=None,
                        quality=CurrentSignalValue.GOOD, sequence=None):
    value = validate_signal_value(binding.signal_definition, value)
    che = binding.component.che
    code = binding.signal_definition.code
    if code == 'che.working.status' and value and (
        not che.powered_on or che.is_in_maintenance
    ):
        raise ValidationError('Working equipment must be powered on and outside maintenance.')
    if code == 'che.available.status' and value and (
        not che.powered_on or che.is_in_maintenance
    ):
        raise ValidationError('Available equipment must be powered on and outside maintenance.')
    timestamp = source_timestamp or timezone.now()
    current, _ = CurrentSignalValue.objects.update_or_create(
        binding=binding,
        defaults={
            'value': value,
            'source_timestamp': timestamp,
            'quality': quality,
            'sequence': sequence,
        },
    )
    root_updates = {
        'che.on.status': 'powered_on',
        'che.working.status': 'working',
        'che.available.status': 'is_available',
        'che.dispatched.status': 'is_dispatched',
        'che.maintenance.status': 'is_in_maintenance',
        'che.location.latitude': 'latitude',
        'che.location.longitude': 'longitude',
        'che.heading.angle': 'rotation',
    }
    if code in root_updates:
        updates = {root_updates[code]: value, 'state_updated_at': timestamp}
        if code == 'che.on.status' and not value:
            updates.update({'working': False, 'is_available': False})
        if code == 'che.maintenance.status' and value:
            updates.update({'working': False, 'is_available': False})
        type(che).objects.filter(pk=che.pk).update(**updates)
        for name, updated_value in updates.items():
            setattr(che, name, updated_value)
        if code in {'che.dispatched.status', 'che.on.status'}:
            evaluate_che_activity(che, timestamp=timestamp)
        else:
            transition_operational_state(che, source='canonical-signal', timestamp=timestamp)
    elif code in MOTION_SIGNAL_CODES:
        evaluate_che_activity(che, timestamp=timestamp)
    elif code == 'spreader.locked.status':
        Spreader.objects.filter(component=binding.component).update(twistlocks_locked=value)
    return current


def _component_for_legacy_value(che, name):
    roles = {
        'fuel_level': ['fuel_tank', 'battery'],
        'twistlock_closed': ['spreader'],
        'running_hours': ['engine', 'electric_power_source', 'equipment'],
        'running_hours_since_last_maintenance': ['engine'],
    }[name]
    for role in roles:
        component = che.components.filter(role=role, is_active=True).first()
        if component:
            return component
    return None


def get_che_legacy_value(che, name, default):
    component = _component_for_legacy_value(che, name)
    if not component:
        return default
    if name == 'running_hours_since_last_maintenance' and hasattr(component, 'engine'):
        return float(component.engine.hours_since_maintenance)
    signal_codes = {
        'fuel_level': 'energystoragesystem.energy.level',
        'twistlock_closed': 'spreader.locked.status',
        'running_hours': (
            'energysupply.running.total_hours'
            if component.component_type.code == ComponentType.POWER_SOURCE
            else 'che.on.total_hours'
        ),
    }
    code = signal_codes.get(name)
    if not code:
        return default
    current = CurrentSignalValue.objects.filter(
        binding__component=component, binding__signal_definition__code=code
    ).first()
    return current.value if current and current.value is not None else default


def get_equipment_type_legacy_value(equipment_type, name, default):
    profile_and_key = {
        'driving_speed_full_load_kmh': ('drive', 'maximum_speed_loaded_m_s', 3.6),
        'driving_speed_empty_kmh': ('drive', 'maximum_speed_empty_m_s', 3.6),
        'lifting_speed_full_load_m_per_min': ('hoist', 'maximum_speed_loaded_m_s', 60.0),
        'lifting_speed_empty_m_per_min': ('hoist', 'maximum_speed_empty_m_s', 60.0),
        'turning_radius_m': ('drive', 'turning_radius_m', 1.0),
        'engine_type': ('engine', 'engine_type', 1.0),
        'fuel_tank_capacity': (
            'battery' if equipment_type.energy_source == EquipmentType.Electric else 'fuel_tank',
            'capacity_kwh' if equipment_type.energy_source == EquipmentType.Electric else 'capacity_l',
            1.0,
        ),
    }
    if name == 'power_rating_kw':
        profile = equipment_type.component_profiles.filter(
            role__in=['engine', 'electric_power_source'], is_active=True
        ).first()
        return profile.default_configuration.get('rated_power_kw', default) if profile else default
    role, key, factor = profile_and_key[name]
    profile = equipment_type.component_profiles.filter(role=role, is_active=True).first()
    if not profile:
        return default
    value = profile.default_configuration.get(key, default)
    return value if isinstance(value, str) or value is None else float(value) * factor


def derive_operational_state(che):
    if che.is_in_maintenance:
        return OperationalStateInterval.MAINTENANCE
    if not che.powered_on:
        return OperationalStateInterval.OFF
    if che.working:
        return OperationalStateInterval.WORKING
    return OperationalStateInterval.IDLE


def transition_operational_state(che, source='derived', timestamp=None):
    timestamp = timestamp or timezone.now()
    state = derive_operational_state(che)
    current = che.state_intervals.filter(ended_at__isnull=True).first()
    if current and current.state == state:
        return current
    if current:
        current.ended_at = timestamp
        current.save(update_fields=['ended_at'])
    return OperationalStateInterval.objects.create(
        che=che, state=state, started_at=timestamp, source=source
    )


def sync_che_summary_signals(che, source_protocol=SignalBinding.REST):
    components = {component.role: component for component in che.components.filter(is_active=True)}
    root = components.get('equipment')
    if not root:
        return
    values = {
        'che.on.status': che.powered_on,
        'che.working.status': che.working,
        'che.available.status': che.is_available,
        'che.dispatched.status': che.is_dispatched,
        'che.maintenance.status': che.is_in_maintenance,
        'che.location.latitude': che.latitude,
        'che.location.longitude': che.longitude,
        'che.heading.angle': che.rotation,
    }
    for code, value in values.items():
        if value is not None:
            set_current_signal(root, code, value, source_protocol=source_protocol)
    for role in ['engine', 'electric_power_source']:
        power_source = components.get(role)
        if power_source:
            set_current_signal(
                power_source, 'energysupply.running.status', che.powered_on,
                source_protocol=source_protocol,
            )


def apply_che_component_values(che, values, source_protocol=SignalBinding.REST):
    if not values:
        return
    sync_che_components(che)
    if 'fuel_level' in values:
        component = _component_for_legacy_value(che, 'fuel_level')
        if component:
            set_current_signal(
                component, 'energystoragesystem.energy.level', float(values['fuel_level']),
                source_protocol=source_protocol,
            )
    if 'twistlock_closed' in values:
        component = _component_for_legacy_value(che, 'twistlock_closed')
        if component:
            locked = bool(values['twistlock_closed'])
            Spreader.objects.filter(component=component).update(twistlocks_locked=locked)
            set_current_signal(
                component, 'spreader.locked.status', locked,
                source_protocol=source_protocol,
            )
    if 'running_hours' in values:
        component = _component_for_legacy_value(che, 'running_hours')
        if component:
            code = (
                'energysupply.running.total_hours'
                if component.component_type.code == ComponentType.POWER_SOURCE
                else 'che.on.total_hours'
            )
            set_current_signal(component, code, float(values['running_hours']), source_protocol=source_protocol)
            Engine.objects.filter(component=component).update(running_hours=Decimal(str(values['running_hours'])))
    if 'running_hours_since_last_maintenance' in values:
        component = _component_for_legacy_value(che, 'running_hours_since_last_maintenance')
        if component:
            Engine.objects.filter(component=component).update(
                hours_since_maintenance=Decimal(str(values['running_hours_since_last_maintenance']))
            )


def apply_pending_che_component_values(che):
    values = dict(getattr(che, '_pending_component_values', {}))
    apply_che_component_values(che, values)
    if hasattr(che, '_pending_component_values'):
        del che._pending_component_values
