extends SceneTree

const MQTTNodeClient = preload("res://source/terminal-sandbox/MQTTNodeClient.gd")
const SparkPlugManager = preload("res://source/sparkplugB/SparkPlugMQTTManager.gd")
const TerminalWorld3D = preload("res://source/terminal-sandbox/TerminalWorld3D.gd")
const CheIntegrationPolicy = preload("res://source/terminal-sandbox/CheIntegrationPolicy.gd")
const MinitosClient = preload("res://source/terminal-sandbox/MinitosClient.gd")
const TTScene = preload("res://source/terminal-sandbox/che/TT.tscn")

var _failed := false


class FakeSparkplugManager:
	extends Node
	var calls: Array = []

	func is_broker_connected():
		return true

	func publish_device_birth(device_id: String, metrics: Array = []):
		calls.append(["DBIRTH", device_id, metrics])
		return true

	func publish_device_data(device_id: String, metrics: Array):
		calls.append(["DDATA", device_id, metrics])
		return true

	func publish_device_death(device_id: String):
		calls.append(["DDEATH", device_id, []])
		return true


class FakeMQTT:
	extends Node
	signal broker_connected
	signal broker_disconnected
	signal broker_connection_failed
	signal publish_acknowledge(packet_id: int)

	var packets: Array = []
	var last_will: Array = []
	var connected_url := ""
	var client_id := ""
	var binarymessages := false
	var verbose_level := 0
	var pinginterval := 30

	func set_last_will(topic: String, payload: PackedByteArray, retain := false, qos := 0):
		last_will = [topic, payload, retain, qos]

	func connect_to_broker(url: String):
		connected_url = url
		return true

	func set_user_pass(_username, _password):
		pass

	func disconnect_from_server():
		pass

	func cleanupsockets():
		pass

	func publish(topic: String, payload: PackedByteArray, retain := false, qos := 0):
		packets.append([topic, payload, retain, qos])
		return 0


func _initialize():
	_test_mqtt_node_che_lifecycle()
	_test_node_session_birth_and_death_certificate()
	_test_mqtt_cadence()
	_test_power_transition_contract()
	_test_move_order_state_contract()
	_test_sparkplug_topics_and_payloads()
	_test_canvas_viewport_coordinate_conversion()
	_test_che_moves_to_world_target()
	_test_che_finishes_when_step_crosses_arrival_radius()
	_test_che_zero_profile_still_translates()
	_test_layout_api_adapter()
	if _failed:
		quit(1)
	else:
		print("Terminal integration checks passed")
		quit(0)


func _test_mqtt_node_che_lifecycle():
	var client = MQTTNodeClient.new()
	var fake = FakeSparkplugManager.new()
	client._manager = fake
	client._connected = true
	var che = {
		"pk": 4,
		"name": "SC-04",
		"powered_on": true,
		"working": false,
		"latitude": 41.352,
		"longitude": 2.171,
		"fuel_level": 87.5,
	}
	_assert(client.publish_che_data(che), "First DDATA should be accepted")
	_assert(fake.calls.size() == 2, "First DDATA must publish DBIRTH followed by DDATA")
	_assert(fake.calls[0][0] == "DBIRTH" and fake.calls[1][0] == "DDATA", "CHE birth/data order is invalid")
	_assert(client.publish_che_data(che), "Subsequent DDATA should be accepted")
	_assert(fake.calls.size() == 3 and fake.calls[2][0] == "DDATA", "Subsequent telemetry must not repeat DBIRTH")
	_assert(client.publish_che_death(che), "DDEATH should be accepted for a born CHE")
	_assert(fake.calls.back()[0] == "DDEATH", "CHE death did not publish DDEATH")
	_assert(client._format_broker_url("broker.example", 8883, "") == "ssl://broker.example:8883", "Port 8883 must default to TLS")
	client.free()
	fake.free()


func _test_sparkplug_topics_and_payloads():
	var manager = SparkPlugManager.new()
	var mqtt = FakeMQTT.new()
	manager.mqtt = mqtt
	manager.group_id = "GROUP"
	manager.node_id = "NODE"
	manager._state = 2
	manager.publish_device_birth("CHE_4", [{"name": "State/PoweredOn", "type": "Boolean", "value": true}])
	manager.publish_device_data("CHE_4", [{"name": "Position/Latitude", "type": "Double", "value": 41.352}])
	manager.publish_device_death("CHE_4")
	_assert(mqtt.packets.size() == 3, "Expected one Sparkplug packet per lifecycle event")
	_assert(mqtt.packets[0][0] == "spBv1.0/GROUP/DBIRTH/NODE/CHE_4", "DBIRTH topic is invalid")
	_assert(mqtt.packets[1][0] == "spBv1.0/GROUP/DDATA/NODE/CHE_4", "DDATA topic is invalid")
	_assert(mqtt.packets[2][0] == "spBv1.0/GROUP/DDEATH/NODE/CHE_4", "DDEATH topic is invalid")
	for packet in mqtt.packets:
		_assert((packet[1] as PackedByteArray).size() > 0, "Sparkplug payload must not be empty")
	manager.free()
	mqtt.free()


func _test_node_session_birth_and_death_certificate():
	var manager = SparkPlugManager.new()
	var mqtt = FakeMQTT.new()
	manager.mqtt = mqtt
	manager.add_child(mqtt)
	get_root().add_child(manager)
	manager.group_id = "GROUP"
	manager.node_id = "NODE"
	manager.broker_url = "ssl://broker.example:8883"
	manager.birth_death_sequence = 7
	manager.connect_to_broker()
	_assert(mqtt.connected_url == manager.broker_url, "Sparkplug manager must connect to the configured broker URL")
	_assert(mqtt.last_will.size() == 4, "Broker connection must include a death certificate")
	_assert(mqtt.last_will[0] == "spBv1.0/GROUP/NDEATH/NODE", "Death certificate must use the NDEATH topic")
	_assert((mqtt.last_will[1] as PackedByteArray).size() > 0, "Death certificate payload must include bdSeq")
	_assert(not _protobuf_top_level_fields(mqtt.last_will[1]).has(3), "NDEATH last will must omit the seq field")
	manager._on_transport_connected()
	_assert(mqtt.packets.size() == 1, "Broker connection must immediately emit NBIRTH")
	_assert(mqtt.packets[0][0] == "spBv1.0/GROUP/NBIRTH/NODE", "NBIRTH topic is invalid")
	_assert(manager.publish_node_death(), "Intentional NDEATH should be queued before disconnect")
	_assert(mqtt.packets[1][0] == "spBv1.0/GROUP/NDEATH/NODE", "Intentional NDEATH topic is invalid")
	_assert(mqtt.packets[1][3] == 1, "Intentional NDEATH must use QoS 1")
	_assert(not _protobuf_top_level_fields(mqtt.packets[1][1]).has(3), "Intentional NDEATH must omit the seq field")
	manager.free()


func _test_mqtt_cadence():
	_assert(not CheIntegrationPolicy.should_publish_data(true, true, 1000, 2999), "WORKING telemetry must wait two seconds")
	_assert(CheIntegrationPolicy.should_publish_data(true, true, 1000, 3000), "WORKING telemetry must publish every two seconds")
	_assert(not CheIntegrationPolicy.should_publish_data(true, false, 1000, 20999), "IDLE telemetry must wait twenty seconds")
	_assert(CheIntegrationPolicy.should_publish_data(true, false, 1000, 21000), "IDLE telemetry must publish every twenty seconds")
	_assert(not CheIntegrationPolicy.should_publish_data(false, false, 0, 999999), "Powered-off CHE must not publish DDATA")


func _test_power_transition_contract():
	_assert(CheIntegrationPolicy.transition_events(false, true) == ["DBIRTH", "DDATA"], "Turning a CHE on must emit DBIRTH and DDATA")
	_assert(CheIntegrationPolicy.transition_events(true, true) == ["DDATA"], "Activity changes must emit DDATA")
	_assert(CheIntegrationPolicy.transition_events(true, false) == ["DDEATH"], "Turning a CHE off must emit DDEATH")
	_assert(CheIntegrationPolicy.transition_events(false, false).is_empty(), "An already-off CHE must remain silent")


func _test_move_order_state_contract():
	var started = CheIntegrationPolicy.move_started_state()
	_assert(started.working and started.is_dispatched and not started.is_available, "A move order must set Working and Dispatched")
	var completed = CheIntegrationPolicy.move_completed_state()
	_assert(not completed.working and not completed.is_dispatched, "Arrival must return the CHE to Idle and Not Dispatched")


func _test_che_moves_to_world_target():
	var root = Node3D.new()
	get_root().add_child(root)
	var movement = TTScene.instantiate()
	root.add_child(movement)
	var completion = [false]
	movement.movement_finished.connect(func(): completion[0] = true)
	movement.configure_terminal_che({"name": "TT01"}, Vector3(6.0, 2.0, 3.0), {
		"speed_mps": 5.5,
		"acceleration_mps2": 1.2,
		"turning_radius_m": 5.0,
	}, 1.0, StandardMaterial3D.new())
	var target = Vector3(20.0, 0.0, 0.0)
	movement.move_to(target)
	for _step in range(2400):
		movement._physics_process(1.0 / 60.0)
		if not movement.is_moving():
			break
	_assert(not movement.is_moving(), "CHE movement controller must reach its commanded world target")
	_assert(completion[0], "Reaching the 50 cm arrival range must emit movement_finished")
	_assert(Vector2(movement.position.x, movement.position.z).distance_to(Vector2(target.x, target.z)) <= 0.5, "CHE must finish within 50 cm of the clicked X/Z destination")
	var visual = movement.get_node("Geometry/Body")
	_assert(visual.get_parent().get_parent() == movement, "CHE visual must be parented to the moving unit/root")
	_assert(abs(movement.rotation.y) > 0.2, "CHE must visibly rotate toward a target outside its initial heading")
	_assert(is_equal_approx(visual.rotation.y, 0.0), "CHE visual local rotation must stay relative to the moving collider/root")
	root.queue_free()


func _test_che_finishes_when_step_crosses_arrival_radius():
	var root = Node3D.new()
	get_root().add_child(root)
	var movement = TTScene.instantiate()
	root.add_child(movement)
	var completion = [false]
	movement.movement_finished.connect(func(): completion[0] = true)
	movement.configure_terminal_che({"name": "TT01"}, Vector3(6.0, 2.0, 3.0), {
		"speed_mps": 20.0,
		"acceleration_mps2": 40.0,
		"turn_speed_deg_s": 360.0,
	}, 1.0, StandardMaterial3D.new())
	movement.position = Vector3(0.0, 0.0, -2.0)
	movement.rotation.y = PI
	var target = Vector3(0.0, 0.0, 0.0)
	movement.move_to(target)
	for _step in range(30):
		movement._physics_process(0.1)
		if not movement.is_moving():
			break
	_assert(not movement.is_moving(), "CHE movement must stop when a frame segment crosses the 50 cm arrival radius")
	_assert(completion[0], "Crossing the arrival radius must emit movement_finished")
	_assert(Vector2(movement.position.x, movement.position.z).distance_to(Vector2(target.x, target.z)) <= 0.5, "CHE must snap into the commanded destination when completing")
	root.queue_free()


func _test_che_zero_profile_still_translates():
	var root = Node3D.new()
	get_root().add_child(root)
	var movement = TTScene.instantiate()
	root.add_child(movement)
	movement.configure_terminal_che({"name": "TT01"}, Vector3(6.0, 2.0, 3.0), {
		"speed_mps": 0.0,
		"acceleration_mps2": 0.0,
		"turn_speed_deg_s": 90.0,
	}, 1.0, StandardMaterial3D.new())
	movement.move_to(Vector3(0.0, 0.0, -10.0))
	for _step in range(20):
		movement._physics_process(0.1)
	var motion_state: Dictionary = movement.debug_motion_state()
	_assert(movement.position.distance_to(Vector3.ZERO) > 0.01, "CHE must translate even when API equipment speed/acceleration values are zero")
	_assert(float(motion_state.get("speed_mps", 0.0)) >= 0.5, "Movement profile must clamp speed to a useful minimum")
	_assert(float(motion_state.get("acceleration_mps2", 0.0)) >= 0.25, "Movement profile must clamp acceleration to a useful minimum")
	root.queue_free()


func _test_canvas_viewport_coordinate_conversion():
	var world = TerminalWorld3D.new()
	world.size = Vector2(1000, 500)
	world._viewport = SubViewport.new()
	world._viewport.size = Vector2i(2000, 1000)
	var converted: Vector2 = world._canvas_to_viewport_position(Vector2(250, 125))
	_assert(converted.is_equal_approx(Vector2(500, 250)), "Screen clicks must be scaled into viewport coordinates")
	var round_trip: Vector2 = world._viewport_to_canvas_position(converted)
	_assert(round_trip.is_equal_approx(Vector2(250, 125)), "Viewport projection must round-trip to HUD coordinates")
	world.map_origin_latitude = 41.35
	world.map_origin_longitude = 2.17
	world.map_angle_deg = 71.4
	world.map_units_per_meter = 1.5
	var geographic = world.world_to_geographic(world._geographic_to_world(41.351, 2.172))
	_assert(is_equal_approx(float(geographic.latitude), 41.351), "Destination latitude must round-trip through world coordinates")
	_assert(is_equal_approx(float(geographic.longitude), 2.172), "Destination longitude must round-trip through world coordinates")
	world._viewport.free()
	world.free()


func _test_layout_api_adapter():
	var client = MinitosClient.new()
	var assets = client._layout_assets_as_areas([{
		"id": 4,
		"code": "A01",
		"name": "Block A01",
		"asset_kind": "yard_block",
		"geometry": {
			"type": "Polygon",
			"coordinates": [[[-3.0, 40.0], [-2.99, 40.0], [-2.99, 40.01], [-3.0, 40.0]]],
		},
		"storage_grid": {"id": 8, "max_bays": 10, "max_rows": 6},
	}])
	_assert(assets.size() == 1, "Layout asset adapter must retain polygon assets")
	_assert(assets[0].area_type == "yard block", "Layout asset kinds must map to existing world area types")
	_assert(assets[0].block_config.max_bays == 10, "Storage-grid settings must reach TerminalWorld3D")
	var roads = client._linear_axes_as_roads([{
		"id": 5, "name": "Main road", "axis_kind": "road_centerline",
		"points": [[-3.0, 40.0], [-2.99, 40.01]], "lane_count": 2,
	}, {
		"id": 6, "name": "QC rail", "axis_kind": "crane_rail",
		"points": [[-3.0, 40.0], [-3.0, 40.01]],
	}])
	_assert(roads.size() == 1, "Only world-visible travel axes should map to legacy road rendering")
	_assert(roads[0].lane_count == 2, "Axis lane configuration must survive the adapter")
	client.free()


func _assert(condition: bool, message: String):
	if condition:
		return
	push_error(message)
	_failed = true


func _protobuf_top_level_fields(payload: PackedByteArray) -> Array[int]:
	var fields: Array[int] = []
	var offset := 0
	while offset < payload.size():
		var key_result = _read_varint(payload, offset)
		var key = int(key_result[0])
		offset = int(key_result[1])
		fields.append(key >> 3)
		match key & 0x07:
			0:
				offset = int(_read_varint(payload, offset)[1])
			1:
				offset += 8
			2:
				var length_result = _read_varint(payload, offset)
				offset = int(length_result[1]) + int(length_result[0])
			5:
				offset += 4
			_:
				return fields
	return fields


func _read_varint(payload: PackedByteArray, offset: int) -> Array:
	var value := 0
	var shift := 0
	while offset < payload.size():
		var byte = int(payload[offset])
		offset += 1
		value |= (byte & 0x7f) << shift
		if byte & 0x80 == 0:
			break
		shift += 7
	return [value, offset]
