extends Node

# requests
signal deselect_all_units
signal setup_and_spawn_unit(unit, transform, player)
signal place_structure(structure_prototype)
signal schedule_navigation_rebake(domain)

# notifications
signal terrain_targeted(position)
signal unit_spawned(unit)
signal unit_targeted(unit)
signal unit_selected(unit)
signal unit_deselected(unit)
signal unit_died(unit)
signal unit_action_changed(topic: String, message: String)
signal unit_status_changed(unit_type, new_action)
signal unit_action_updated(unit)

signal selected_location_id_update(selected_location_id)

signal broker_initialized

# HTTP Requests
signal che_list_received
signal block_list_received
signal container_visits_list_received

# MQTTManager Signals
signal mqtt_spv1_broker_connected_init
signal mqtt_spv1_broker_connected
signal mqtt_spv1_broker_disconnected
signal mqtt_spv1_broker_connection_failed

# Che-specific MQTT signals
signal che_mqtt_birth(che_name: String)
signal che_mqtt_death(che_name: String)
signal che_mqtt_data(che_name: String, data: Dictionary)
