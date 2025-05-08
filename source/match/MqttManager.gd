extends Control

var receivedmessagecount = 0
var _selected_location = ""
var _mqtt_initialized = false
var _mqtt_connected = false
var _ignition_connected = false

func _ready():
	# Connect MQTT signals
	MatchSignals.broker_connected.connect(_on_mqtt_broker_connected)
	MatchSignals.broker_connection_failed.connect(_on_mqtt_broker_connection_failed)
	MatchSignals.broker_disconnected.connect(_on_mqtt_broker_disconnected)
	MatchSignals.broker_received_message.connect(_on_mqtt_received_message)
	MatchSignals.broker_new_message_to_publish.connect(_on_new_message_to_publish)
	
	MatchSignals.unit_spawned.connect(_on_unit_spawned)
	MatchSignals.unit_action_changed.connect(_on_unit_action_changed)
	MatchSignals.unit_status_changed.connect(_on_unit_status_changed)

func connect_to_broker(brokerurl= "localhost",  brokerport="1884",
					protocol = "tcp://", 
					lastwilltopic = "terminal_simulator/connection_status", 
					lastwillmessage = "disconnected", 
					lastwillretain = false, 
					brokeruser = "godot_client", 
					brokerpswd = "password"):
	#randomize()
	##$MQTT.client_id = "s%d" % randi()
	$MQTT.client_id = "godot_client"
	
	if lastwilltopic:
		$MQTT.set_last_will(lastwilltopic, 
							lastwillmessage, 
							lastwillretain)
	else:
		$MQTT.set_last_will("", "", false)

	if brokeruser:
		$MQTT.set_user_pass(brokeruser,
							brokerpswd)
	else:
		$MQTT.set_user_pass(null, null)

	print("connecting...")
	var retval = $MQTT.connect_to_broker("%s%s:%s" % [protocol, brokerurl, brokerport])
	if not retval:
		print("mqtt connection error.")
		return false;
	else:
		return true;


func publish(publishtopic, publishmessage, publishretain = false):
	var qos = 0
	$MQTT.publish(publishtopic, publishmessage, publishretain, qos)

func subscribe(topic):
	var qos = 0
	$MQTT.subscribe(topic, qos)
	

func unsubscribe(topic):
	$MQTT.unsubscribe(topic)
	
func _subscribe_to_location(location):
	if not location:
		return
		
	var base_topic = "MiniTOS/%s" % location
	subscribe("%s/ches/#" % base_topic)
	subscribe("%s/blocks/#" % base_topic)
	subscribe("%s/containers/#" % base_topic)
	_mqtt_initialized = true

func _unsubscribe_from_location(location):
	if not location:
		return
		
	var base_topic = "MiniTOS/%s" % location
	unsubscribe("%s/ches/#" % base_topic)
	unsubscribe("%s/blocks/#" % base_topic)
	unsubscribe("%s/containers/#" % base_topic)
	_mqtt_initialized = false

func _on_new_message_to_publish(publishtopic, publishmessage, publishretain = false):
	publish(publishtopic, publishmessage, publishretain)

func _on_mqtt_received_message(topic, message):
	print("Received message on topic: ", topic, " Message: ", message)
	var json_message = JSON.parse_string(message)
	# Handle messages
	if topic.begins_with("unsAv1.0/LocationData/SelectedLocationId"):
		print("SelectedLocationId received: ",json_message["value"])
		if json_message is Dictionary:
			if "value" in json_message:
				_handle_location_selection(json_message["value"])
		return
		
	match topic:
		"MiniTOS/ConnectionStatus":
			_handle_ignition_connection(message)
		_:
			_handle_terminal_data(topic, message)


func _handle_location_selection(location):
	_selected_location = str(location)  # Convert location to string
	if _selected_location:
		# Unsubscribe from previous location topics if any
		#if _mqtt_initialized:
			#_unsubscribe_from_location(_selected_location)
		
		# Subscribe to new location topics
		#_subscribe_to_location(_selected_location)
		
		# Request initial data for the new location
		MatchSignals.selected_location_id_update.emit(_selected_location)
	else:
		print("No terminal location selected. Please select a location in Ignition.")

func _handle_ignition_connection(status):
	_ignition_connected = (status == "connected")
	if not _ignition_connected:
		print("Ignition client is not connected. Please ensure Ignition is running and connected to the MQTT broker.")

func _handle_terminal_data(topic, message):
	var topic_parts = topic.split("/")
	if topic_parts.size() < 4:
		return
		
	var category = topic_parts[2]
	var action = topic_parts[3]
	
	match category:
		"che":
			_handle_che_message(action, message)
		"blocks":
			_handle_blocks_message(action, message)
		"containers":
			_handle_containers_message(action, message)

func _handle_che_message(action, message):
	match action:
		"data":
			# Parse CHE data and update game state
			var che_data = JSON.parse_string(message)
			#if che_data:
			#	_setup_che_with_data(che_data)

func _handle_blocks_message(action, message):
	match action:
		"data":
			# Parse Block data and update game state
			var blocks_data = JSON.parse_string(message)
			#if blocks_data:
			#	_setup_yard_with_data(blocks_data)

func _handle_containers_message(action, message):
	match action:
		"data":
			# Parse Container data and update game state
			var containers_data = JSON.parse_string(message)
			#if containers_data:
			#	_setup_containers_with_data(containers_data)

func _on_mqtt_broker_connected():
	print("MQTT Broker connected.")
	subscribe("unsAv1.0/LocationData/SelectedLocationId")
	publish("terminal_simulator/connection_status", "connected", false)
	print("MQTT Broker Initialized.")
	MatchSignals.broker_initialized.emit()

func _on_mqtt_broker_connection_failed():
	print("MQTT Broker connection failed.")


func _on_mqtt_broker_disconnected():
	print("MQTT Broker disconnected.")

	
func _on_mqtt_publish_acknowledge(pid):
	print("Publish acknosledged: %s." % pid)

func _on_unit_spawned(unit):
	publish("terminal_simulator/unit_spawned", "new unit spawned: %s" % unit._get_type(), false)
	
func _on_unit_action_changed(topic, mqtt_update_data, retain):
	#var message = "new action for %s: %s" % [mqtt_update_data, action]
	publish(topic, mqtt_update_data, retain)
	

func _on_unit_status_changed(unit_type, state):
	#var message = "new status for %s: %s" % [unit_type, state]
	#publish("terminal_simulator/status_changed", message, false)
	pass
