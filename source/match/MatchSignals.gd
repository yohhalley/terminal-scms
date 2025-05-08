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

# mqtt
signal broker_connected
signal broker_connection_failed
signal broker_disconnected
signal publish_acknowledge
signal broker_received_message
signal broker_new_message_to_publish
signal selected_location_id_update(selected_location_id)

signal broker_initialized

# HTTP Requests
signal che_list_received
signal block_list_received
signal container_visits_list_received
