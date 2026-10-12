# MiniTOS model relationships

Generated from the live Django model registry. Regenerate after model changes with:

```shell
python manage.py generate_model_diagram --output docs/MODEL_RELATIONSHIPS.md
```

```mermaid
erDiagram
    ISOType {
        BigAutoField id PK
        CharField iso_type
        CharField length
        CharField height
        BooleanField is_reefer
        BooleanField is_oog
    }
    ContainerOwner {
        BigAutoField id PK
        CharField name
        CharField code
        BooleanField is_shippingline
    }
    ContainerUnit {
        ForeignKey iso_type FK
        ForeignKey owner FK
        CharField container_number PK
        BooleanField is_active
        BooleanField is_in_terminal
    }
    ContainerVisit {
        BigAutoField id PK
        CharField category
        ForeignKey container_number FK
        ForeignKey ib_carrier FK
        ForeignKey ob_carrier FK
        ForeignKey location FK
        DateField eta
        DateField etd
        IntegerField weight
        ForeignKey pod FK
        ForeignKey pod2 FK
        BooleanField requires_power
        BooleanField is_imdg
        FloatField latitude
        FloatField longitude
        FloatField z_position
        CharField bill_of_lading
        CharField booking
        CharField status
        DateTimeField last_updated
    }
    Location {
        BigAutoField id PK
        CharField name
        CharField location_type
        FloatField latitude
        FloatField longitude
        FloatField map_origin_latitude
        FloatField map_origin_longitude
        FloatField map_width_m
        FloatField map_height_m
        FloatField map_units_per_meter
        FloatField default_zoom
        FloatField angle
        CharField timezone
    }
    LayoutVersion {
        BigAutoField id PK
        ForeignKey location FK
        PositiveIntegerField version
        CharField name
        CharField status
        BooleanField is_current
        ForeignKey based_on FK
        TextField notes
        ForeignKey created_by FK
        DateTimeField created_at
        DateTimeField updated_at
        DateTimeField published_at
    }
    LayoutAsset {
        BigAutoField id PK
        ForeignKey layout_version FK
        ForeignKey parent FK
        ForeignKey access_policy FK
        CharField code
        CharField name
        CharField asset_kind
        JSONField geometry
        FloatField heading_deg
        FloatField elevation_m
        BooleanField is_visible
        BooleanField is_active
        JSONField metadata
    }
    AccessPolicy {
        BigAutoField id PK
        ForeignKey layout_version FK
        CharField code
        CharField name
        BooleanField allow_loaded
        BooleanField allow_empty
        FloatField max_width_m
        FloatField max_height_m
        FloatField max_weight_t
        JSONField rules
    }
    LinearAxis {
        BigAutoField id PK
        ForeignKey asset FK
        CharField code
        CharField name
        CharField axis_kind
        JSONField points
        CharField direction
        PositiveSmallIntegerField lane_count
        PositiveIntegerField capacity
        FloatField width_m
        FloatField speed_limit_kph
        ForeignKey access_policy FK
        BooleanField is_enabled
        JSONField metadata
    }
    LinearMarker {
        BigAutoField id PK
        ForeignKey axis FK
        CharField marker_kind
        PositiveIntegerField sequence
        CharField code
        CharField name
        FloatField chainage_m
        FloatField lateral_offset_m
        FloatField latitude
        FloatField longitude
        JSONField metadata
    }
    BerthSegment {
        BigAutoField id PK
        ForeignKey quay_asset FK
        ForeignKey quay_face_axis FK
        CharField code
        CharField name
        FloatField start_chainage_m
        FloatField end_chainage_m
        CharField water_side
        CharField bollard_direction
        BooleanField is_active
    }
    StorageGrid {
        BigAutoField id PK
        OneToOneField layout_asset FK
        CharField grid_kind
        CharField name
        IntegerField max_bays
        IntegerField max_rows
        IntegerField max_tier
        FloatField latitude
        FloatField longitude
        FloatField heading_deg
        FloatField slot_heading_deg
        FloatField pile_length_m
        FloatField pile_width_m
        FloatField pile_height_m
        CharField initial_bay
        CharField initial_row
        CharField bay_order
        CharField row_order
        CharField bay_naming
        CharField row_naming
        BooleanField bay_use_heading_axis
        FloatField padding_top_m
        FloatField padding_bottom_m
        FloatField padding_left_m
        FloatField padding_right_m
        FloatField bay_spacing_m
        FloatField row_spacing_m
        BooleanField is_heapzone
    }
    Pile {
        BigAutoField id PK
        ForeignKey storage_grid FK
        IntegerField bay
        IntegerField row
        IntegerField tiers
        BooleanField allows_in
        BooleanField allows_out
        BooleanField is_restricted
        BooleanField allows_20
        BooleanField allows_40
        BooleanField allows_45
        FloatField latitude
        FloatField longitude
        FloatField margin_top_m
        FloatField margin_bottom_m
        FloatField margin_left_m
        FloatField margin_right_m
    }
    Position {
        BigAutoField id PK
        OneToOneField container_visit FK
        ForeignKey pile FK
        BooleanField is_reserved
        BooleanField is_occupied
        BooleanField is_powered
        IntegerField tier
    }
    TransferInterface {
        BigAutoField id PK
        ForeignKey asset FK
        ForeignKey storage_grid FK
        ForeignKey axis FK
        ForeignKey access_policy FK
        CharField code
        CharField name
        CharField interface_kind
        CharField side
        JSONField geometry
        PositiveIntegerField bay_start
        PositiveIntegerField bay_end
        PositiveIntegerField row_start
        PositiveIntegerField row_end
        PositiveIntegerField capacity
        BooleanField cargo_may_rest
        BooleanField requires_vehicle_present
        BooleanField is_enabled
        JSONField metadata
    }
    InterfacePosition {
        BigAutoField id PK
        ForeignKey transfer_interface FK
        PositiveIntegerField sequence
        CharField code
        CharField name
        FloatField latitude
        FloatField longitude
        FloatField heading_deg
        PositiveIntegerField capacity
        BooleanField is_enabled
    }
    OperationalPosition {
        BigAutoField id PK
        ForeignKey layout_version FK
        CharField code
        CharField name
        CharField position_kind
        ForeignKey asset FK
        OneToOneField stack_position FK
        OneToOneField interface_position FK
        ForeignKey axis FK
        FloatField chainage_m
        FloatField latitude
        FloatField longitude
        FloatField elevation_m
        JSONField logical_address
        BooleanField is_active
    }
    CheOperatingScope {
        BigAutoField id PK
        ForeignKey che FK
        ForeignKey equipment_type FK
        ForeignKey asset FK
        ForeignKey axis FK
        FloatField min_chainage_m
        FloatField max_chainage_m
        PositiveIntegerField bay_start
        PositiveIntegerField bay_end
        PositiveIntegerField row_start
        PositiveIntegerField row_end
        BooleanField can_reposition
        BooleanField is_enabled
    }
    RouteNode {
        BigAutoField id PK
        ForeignKey layout_version FK
        CharField code
        CharField name
        CharField node_kind
        JSONField point
        ForeignKey asset FK
        OneToOneField operational_position FK
        BooleanField is_generated
        BooleanField is_enabled
        JSONField metadata
    }
    RouteEdge {
        BigAutoField id PK
        ForeignKey layout_version FK
        CharField code
        CharField name
        ForeignKey from_node FK
        ForeignKey to_node FK
        CharField edge_kind
        CharField movement_mode
        ForeignKey source_axis FK
        ForeignKey access_policy FK
        FloatField distance_m
        FloatField nominal_duration_seconds
        PositiveIntegerField capacity
        FloatField base_cost
        BooleanField is_generated
        BooleanField is_enabled
        JSONField metadata
    }
    RouteEdgeRequirement {
        BigAutoField id PK
        ForeignKey route_edge FK
        ForeignKey component_type FK
        CharField role
        PositiveSmallIntegerField minimum_quantity
    }
    LayoutPreset {
        BigAutoField id PK
        CharField code
        PositiveIntegerField version
        CharField name
        TextField description
        CharField root_asset_kind
        JSONField definition
        BooleanField is_system
        BooleanField is_active
    }
    WorkQueue {
        BigAutoField id PK
        CharField name
        BooleanField is_active
        CharField description
        ForeignKey current_user FK
        ForeignKey location FK
    }
    Order {
        BigAutoField id PK
        CharField order_type
        ForeignKey container_number FK
        ForeignKey work_queue FK
        DateTimeField etc
        DateTimeField atc
        ForeignKey from_position FK
        ForeignKey to_position FK
    }
    OrderRoutePlan {
        BigAutoField id PK
        ForeignKey order FK
        ForeignKey layout_version FK
        PositiveIntegerField version
        CharField status
        DateTimeField calculated_at
        FloatField cost
        TextField reason
    }
    OrderLeg {
        BigAutoField id PK
        ForeignKey order FK
        ForeignKey route_plan FK
        PositiveIntegerField sequence
        CharField leg_kind
        CharField status
        ForeignKey che FK
        ForeignKey equipment_type FK
        FloatField distance
        ForeignKey start_position FK
        ForeignKey end_position FK
        DateTimeField planned_start
        DateTimeField planned_end
        DateTimeField actual_start
        DateTimeField actual_end
    }
    OrderLegRouteEdge {
        BigAutoField id PK
        ForeignKey order_leg FK
        ForeignKey route_edge FK
        PositiveIntegerField sequence
    }
    Che {
        BigAutoField id PK
        BooleanField powered_on
        BooleanField working
        BooleanField is_available
        BooleanField is_dispatched
        BooleanField is_in_maintenance
        CharField name
        ForeignKey equipment_type FK
        ForeignKey current_asset FK
        ForeignKey current_user FK
        FloatField latitude
        FloatField longitude
        FloatField rotation
        ForeignKey location FK
        DateTimeField last_maintenance
        DateTimeField state_updated_at
    }
    EquipmentType {
        BigAutoField id PK
        CharField name
        BooleanField is_horizontal_transport
        BooleanField is_yard_equipment
        BooleanField is_quay_equipment
        BooleanField is_rail_equipment
        BooleanField is_gate_equipment
        FloatField length_m
        FloatField width_m
        FloatField height_m
        FloatField max_lift_weight_kg
        BooleanField is_twin_capeable
        CharField energy_source
        IntegerField minor_maintenance_interval_hours
        IntegerField major_maintenance_interval_hours
        BooleanField is_automated
        IntegerField stacking_capacity
        CharField equipment_type
    }
    ComponentType {
        BigAutoField id PK
        CharField code
        CharField name
        CharField tic40_subject
        TextField description
        BooleanField is_motion_component
        BooleanField is_energy_component
        BooleanField supports_multiple
    }
    EquipmentTypeComponent {
        BigAutoField id PK
        ForeignKey equipment_type FK
        ForeignKey component_type FK
        CharField role
        CharField display_name
        CharField parent_role
        BooleanField is_required
        BooleanField is_active
        PositiveSmallIntegerField quantity
        JSONField default_configuration
    }
    CheComponent {
        BigAutoField id PK
        ForeignKey che FK
        ForeignKey component_type FK
        ForeignKey template FK
        ForeignKey parent FK
        CharField role
        CharField name
        CharField manufacturer
        CharField model
        CharField serial_number
        DateTimeField installed_at
        DateTimeField removed_at
        BooleanField is_active
        JSONField metadata
    }
    Engine {
        BigAutoField id PK
        OneToOneField component FK
        CharField engine_type
        CharField fuel_type
        FloatField rated_power_kw
        FloatField nominal_rpm
        DecimalField running_hours
        DecimalField hours_since_maintenance
    }
    Battery {
        BigAutoField id PK
        OneToOneField component FK
        CharField chemistry
        FloatField capacity_kwh
        FloatField nominal_voltage_v
        FloatField max_charge_power_kw
        FloatField max_discharge_power_kw
        FloatField minimum_soc_percent
        FloatField maximum_soc_percent
    }
    EnergyTank {
        BigAutoField id PK
        OneToOneField component FK
        CharField fuel_type
        FloatField capacity_l
    }
    MotionAxis {
        BigAutoField id PK
        OneToOneField component FK
        FloatField maximum_speed_empty_m_s
        FloatField maximum_speed_loaded_m_s
        FloatField maximum_acceleration_m_s2
        FloatField maximum_deceleration_m_s2
        FloatField motion_deadband_m_s
        FloatField minimum_position_m
        FloatField maximum_position_m
        FloatField turning_radius_m
        DecimalField cumulative_motion_hours
        DecimalField cumulative_distance_m
    }
    Spreader {
        BigAutoField id PK
        OneToOneField component FK
        BooleanField twin_capable
        BooleanField tandem_capable
        JSONField supported_sizes_ft
        PositiveSmallIntegerField current_size_ft
        BooleanField twistlocks_locked
        BooleanField landed
        BooleanField container_present
    }
    SignalDefinition {
        BigAutoField id PK
        ForeignKey component_type FK
        CharField code
        CharField name
        CharField tic40_path
        CharField data_type
        CharField unit
        CharField value_kind
        CharField standard_version
        TextField description
        BooleanField is_required_for_oee
    }
    SignalBinding {
        BigAutoField id PK
        ForeignKey component FK
        ForeignKey signal_definition FK
        CharField source_protocol
        CharField source_metric
        CharField source_topic
        FloatField scale_factor
        FloatField offset
        BooleanField is_enabled
    }
    CurrentSignalValue {
        BigAutoField id PK
        OneToOneField binding FK
        JSONField value
        DateTimeField source_timestamp
        DateTimeField recorded_at
        CharField quality
        PositiveBigIntegerField sequence
    }
    OperationalStateInterval {
        BigAutoField id PK
        ForeignKey che FK
        CharField state
        DateTimeField started_at
        DateTimeField ended_at
        CharField source
        CharField reason
        FloatField confidence
    }
    HandlingCycle {
        BigAutoField id PK
        ForeignKey che FK
        ForeignKey order_leg FK
        ForeignKey container_visit FK
        DateTimeField started_at
        DateTimeField ended_at
        CharField result
        PositiveIntegerField ideal_cycle_seconds
        FloatField start_latitude
        FloatField start_longitude
        FloatField end_latitude
        FloatField end_longitude
        JSONField metadata
    }
    CarrierService {
        BigAutoField id PK
        ForeignKey location FK
        CharField name
        CharField carrier_service_type
        DateField start_date
        DateField end_date
        CharField end_type
        IntegerField end_after_occurrences
        CharField repeat
        IntegerField repeat_daily
        IntegerField repeat_weekly
        CharField on_weekdays
        IntegerField repeat_monthly
        IntegerField repeat_yearly
        CharField on_monthly_pattern
        IntegerField on_monthly_day
        CharField on_monthly_week
        TimeField window_start_time
        TimeField window_end_time
        CharField window_start_weekday
        CharField window_end_weekday
        IntegerField window_start_monthday
        IntegerField window_end_monthday
        IntegerField moves_in
        IntegerField moves_out
        FloatField vessel_length_m
        ForeignKey default_berth FK
        ForeignKey default_start_bollard FK
        ForeignKey default_end_bollard FK
        ForeignKey primary_equipment_type FK
        DecimalField crane_split
        DecimalField expected_productivity_per_crane
        DecimalField horizontal_transport_per_crane
        CharField periodicity
    }
    CarrierServiceEquipmentRequirement {
        BigAutoField id PK
        ForeignKey carrier_service FK
        ForeignKey equipment_type FK
        DecimalField equipment_split
        DecimalField expected_productivity_per_hour
        DecimalField horizontal_transport_per_unit
    }
    CarrierVisit {
        BigAutoField id PK
        CharField name
        CharField visit_type
        CharField status
        ForeignKey location FK
        DateTimeField eta
        DateTimeField etd
        DateTimeField ata
        DateTimeField atd
        CharField vessel_name
        CharField voyage_number
        CharField carrier
        CharField berth
        ForeignKey planned_berth FK
        ForeignKey berth_start_bollard FK
        ForeignKey berth_end_bollard FK
        DateTimeField created_at
        DateTimeField last_updated
        ForeignKey carrier_service FK
        DateTimeField window_start
        DateTimeField ops_start
        DateTimeField ops_end
        DateTimeField window_end
        IntegerField moves_in
        IntegerField moves_out
        IntegerField moves_import
        IntegerField moves_export
        IntegerField moves_ts
        IntegerField moves_restow
        IntegerField total_moves
        ForeignKey primary_equipment_type FK
        DecimalField crane_split
        DecimalField expected_productivity_per_crane
        DecimalField horizontal_transport_per_crane
    }
    CarrierVisitEquipmentRequirement {
        BigAutoField id PK
        ForeignKey carrier_visit FK
        ForeignKey equipment_type FK
        DecimalField equipment_split
        DecimalField expected_productivity_per_hour
        DecimalField horizontal_transport_per_unit
    }
    POD {
        BigAutoField id PK
        CharField name
        CharField uncode
        CharField country
    }
    ServiceCargoRoute {
        BigAutoField id PK
        ForeignKey inbound_carrier_service FK
        ForeignKey outbound_carrier_service FK
        ForeignKey pod FK
        CharField container_type
        ForeignKey iso_type FK
        CharField hazard_codes
        BooleanField is_reefer
        IntegerField container_count
        DecimalField expected_dwell_time_days
    }
    VisitCargoRoute {
        BigAutoField id PK
        ForeignKey inbound_carrier_visit FK
        ForeignKey inbound_carrier_service FK
        ForeignKey outbound_carrier_visit FK
        ForeignKey outbound_carrier_service FK
        ForeignKey pod FK
        CharField container_type
        ForeignKey iso_type FK
        CharField hazard_codes
        BooleanField is_reefer
        IntegerField container_count
        DecimalField expected_dwell_time_days
    }
    PileBooking {
        BigAutoField id PK
        ForeignKey pile FK
        ForeignKey visit_cargo_route FK
        IntegerField tier_limit
        DateTimeField start_booking_time
        DateTimeField end_booking_time
    }
    PointOfWork {
        BigAutoField id PK
        CharField name
        CharField pow_type
        ForeignKey location FK
        CharField code
        FloatField latitude
        FloatField longitude
        ForeignKey operational_position FK
        ForeignKey route_node FK
        IntegerField max_throughput
        BooleanField is_active
    }
    ProcessHierarchy {
        BigAutoField id PK
        CharField name
        CharField code
        CharField process_area
        ForeignKey parent FK
        TextField description
        PositiveIntegerField display_order
        BooleanField is_active
    }
    BusinessProcess {
        BigAutoField id PK
        CharField name
        CharField code
        ForeignKey hierarchy FK
        ForeignKey location FK
        TextField description
        CharField owner_role
        CharField trigger_type
        JSONField input_schema
        JSONField output_schema
        BooleanField is_active
        DateTimeField created_at
        DateTimeField updated_at
    }
    ProcessVersion {
        BigAutoField id PK
        ForeignKey business_process FK
        PositiveIntegerField version_number
        CharField version_label
        CharField status
        BooleanField is_current
        JSONField nodered_flow
        JSONField input_signals
        JSONField output_signals
        TextField sop_markdown
        CharField mqtt_announcement_topic
        ForeignKey created_by FK
        DateTimeField created_at
        DateTimeField updated_at
        DateTimeField published_at
    }
    BusinessProcessExecution {
        BigAutoField id PK
        ForeignKey business_process FK
        ForeignKey process_version FK
        ForeignKey location FK
        CharField status
        CharField trigger_source
        JSONField trigger_payload
        JSONField result_payload
        TextField error_message
        ForeignKey requested_by FK
        DateTimeField started_at
        DateTimeField completed_at
    }
    AccessPolicy o|--o{ LayoutAsset : "access_policy"
    AccessPolicy o|--o{ LinearAxis : "access_policy"
    AccessPolicy o|--o{ RouteEdge : "access_policy"
    AccessPolicy o|--o{ TransferInterface : "access_policy"
    AccessPolicy }o--o{ EquipmentType : "allowed_che_types"
    BerthSegment o|--o{ CarrierService : "default_berth"
    BerthSegment o|--o{ CarrierVisit : "planned_berth"
    BusinessProcess ||--o{ BusinessProcessExecution : "business_process"
    BusinessProcess ||--o{ ProcessVersion : "business_process"
    CarrierService o|--o{ CarrierVisit : "carrier_service"
    CarrierService ||--o{ CarrierServiceEquipmentRequirement : "carrier_service"
    CarrierService ||--o{ ServiceCargoRoute : "inbound_carrier_service"
    CarrierService ||--o{ ServiceCargoRoute : "outbound_carrier_service"
    CarrierService ||--o{ VisitCargoRoute : "inbound_carrier_service"
    CarrierService ||--o{ VisitCargoRoute : "outbound_carrier_service"
    CarrierVisit o|--o{ ContainerVisit : "ib_carrier"
    CarrierVisit o|--o{ ContainerVisit : "ob_carrier"
    CarrierVisit o|--o{ VisitCargoRoute : "inbound_carrier_visit"
    CarrierVisit o|--o{ VisitCargoRoute : "outbound_carrier_visit"
    CarrierVisit ||--o{ CarrierVisitEquipmentRequirement : "carrier_visit"
    Che o|--o{ CheOperatingScope : "che"
    Che o|--o{ OrderLeg : "che"
    Che ||--o{ CheComponent : "che"
    Che ||--o{ HandlingCycle : "che"
    Che ||--o{ OperationalStateInterval : "che"
    CheComponent o|--o{ CheComponent : "parent"
    CheComponent ||--o{ SignalBinding : "component"
    CheComponent ||--o| Battery : "component"
    CheComponent ||--o| EnergyTank : "component"
    CheComponent ||--o| Engine : "component"
    CheComponent ||--o| MotionAxis : "component"
    CheComponent ||--o| Spreader : "component"
    ComponentType ||--o{ CheComponent : "component_type"
    ComponentType ||--o{ EquipmentTypeComponent : "component_type"
    ComponentType ||--o{ RouteEdgeRequirement : "component_type"
    ComponentType ||--o{ SignalDefinition : "component_type"
    ContainerOwner ||--o{ ContainerUnit : "owner"
    ContainerUnit o|--o{ ContainerVisit : "container_number"
    ContainerVisit o|--o{ HandlingCycle : "container_visit"
    ContainerVisit o|--o{ Order : "container_number"
    ContainerVisit ||--o| Position : "container_visit"
    EquipmentType o|--o{ CarrierService : "primary_equipment_type"
    EquipmentType o|--o{ CarrierVisit : "primary_equipment_type"
    EquipmentType o|--o{ CheOperatingScope : "equipment_type"
    EquipmentType o|--o{ OrderLeg : "equipment_type"
    EquipmentType ||--o{ CarrierServiceEquipmentRequirement : "equipment_type"
    EquipmentType ||--o{ CarrierVisitEquipmentRequirement : "equipment_type"
    EquipmentType ||--o{ Che : "equipment_type"
    EquipmentType ||--o{ EquipmentTypeComponent : "equipment_type"
    EquipmentTypeComponent o|--o{ CheComponent : "template"
    ISOType o|--o{ ContainerUnit : "iso_type"
    ISOType ||--o{ ServiceCargoRoute : "iso_type"
    ISOType ||--o{ VisitCargoRoute : "iso_type"
    InterfacePosition ||--o| OperationalPosition : "interface_position"
    LayoutAsset o|--o{ Che : "current_asset"
    LayoutAsset o|--o{ CheOperatingScope : "asset"
    LayoutAsset o|--o{ LayoutAsset : "parent"
    LayoutAsset o|--o{ OperationalPosition : "asset"
    LayoutAsset o|--o{ RouteNode : "asset"
    LayoutAsset ||--o{ BerthSegment : "quay_asset"
    LayoutAsset ||--o{ LinearAxis : "asset"
    LayoutAsset ||--o{ TransferInterface : "asset"
    LayoutAsset ||--o| StorageGrid : "layout_asset"
    LayoutVersion o|--o{ LayoutVersion : "based_on"
    LayoutVersion ||--o{ AccessPolicy : "layout_version"
    LayoutVersion ||--o{ LayoutAsset : "layout_version"
    LayoutVersion ||--o{ OperationalPosition : "layout_version"
    LayoutVersion ||--o{ OrderRoutePlan : "layout_version"
    LayoutVersion ||--o{ RouteEdge : "layout_version"
    LayoutVersion ||--o{ RouteNode : "layout_version"
    LinearAxis o|--o{ CheOperatingScope : "axis"
    LinearAxis o|--o{ OperationalPosition : "axis"
    LinearAxis o|--o{ RouteEdge : "source_axis"
    LinearAxis o|--o{ TransferInterface : "axis"
    LinearAxis ||--o{ BerthSegment : "quay_face_axis"
    LinearAxis ||--o{ LinearMarker : "axis"
    LinearMarker o|--o{ CarrierService : "default_end_bollard"
    LinearMarker o|--o{ CarrierService : "default_start_bollard"
    LinearMarker o|--o{ CarrierVisit : "berth_end_bollard"
    LinearMarker o|--o{ CarrierVisit : "berth_start_bollard"
    Location o|--o{ BusinessProcess : "location"
    Location o|--o{ BusinessProcessExecution : "location"
    Location o|--o{ WorkQueue : "location"
    Location ||--o{ CarrierService : "location"
    Location ||--o{ CarrierVisit : "location"
    Location ||--o{ Che : "location"
    Location ||--o{ ContainerVisit : "location"
    Location ||--o{ LayoutVersion : "location"
    Location ||--o{ PointOfWork : "location"
    OperationalPosition o|--o{ Order : "from_position"
    OperationalPosition o|--o{ Order : "to_position"
    OperationalPosition o|--o{ OrderLeg : "end_position"
    OperationalPosition o|--o{ OrderLeg : "start_position"
    OperationalPosition o|--o{ PointOfWork : "operational_position"
    OperationalPosition ||--o| RouteNode : "operational_position"
    Order o|--o{ OrderLeg : "order"
    Order ||--o{ OrderRoutePlan : "order"
    OrderLeg o|--o{ HandlingCycle : "order_leg"
    OrderLeg ||--o{ OrderLegRouteEdge : "order_leg"
    OrderRoutePlan o|--o{ OrderLeg : "route_plan"
    POD o|--o{ ContainerVisit : "pod"
    POD o|--o{ ContainerVisit : "pod2"
    POD o|--o{ ServiceCargoRoute : "pod"
    POD o|--o{ VisitCargoRoute : "pod"
    Pile ||--o{ PileBooking : "pile"
    Pile ||--o{ Position : "pile"
    PointOfWork }o--o{ Che : "related_ches"
    Position ||--o| OperationalPosition : "stack_position"
    ProcessHierarchy o|--o{ BusinessProcess : "hierarchy"
    ProcessHierarchy o|--o{ ProcessHierarchy : "parent"
    ProcessVersion ||--o{ BusinessProcessExecution : "process_version"
    RouteEdge ||--o{ OrderLegRouteEdge : "route_edge"
    RouteEdge ||--o{ RouteEdgeRequirement : "route_edge"
    RouteNode o|--o{ PointOfWork : "route_node"
    RouteNode ||--o{ RouteEdge : "from_node"
    RouteNode ||--o{ RouteEdge : "to_node"
    SignalBinding ||--o| CurrentSignalValue : "binding"
    SignalDefinition ||--o{ SignalBinding : "signal_definition"
    StorageGrid o|--o{ TransferInterface : "storage_grid"
    StorageGrid ||--o{ Pile : "storage_grid"
    TransferInterface ||--o{ InterfacePosition : "transfer_interface"
    VisitCargoRoute ||--o{ PileBooking : "visit_cargo_route"
    WorkQueue o|--o{ Order : "work_queue"
```
