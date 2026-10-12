extends Node

signal broker_initialized
signal broker_connected
signal broker_disconnected
signal broker_connection_failed
signal broker_received_message(topic: String, message: String)
signal publish_acknowledged

@export var broker_url: String = "tcp://127.0.0.1:1883/"
@export var group_id: String = "MINI-TERMINAL"
@export var node_id: String = "GODOT-SIM"
@export var device_id_prefix: String = "CHE_"
@export var client_id: String = "godot-sparkplugb-client"

var sequence_number: int = 0
var mqtt

func _ready():
	mqtt = $MQTT
	mqtt.client_id = client_id
	mqtt.verbose_level = 1
	mqtt.binarymessages = false
	mqtt.pinginterval = 30

	_connect_mqtt_signals()
	broker_initialized.emit()

func _connect_mqtt_signals():
	# Connect MQTT signals to handlers
	mqtt.received_message.connect(_on_message_received)
	mqtt.broker_connected.connect(_on_connected_init)
	mqtt.broker_connected.connect(_on_connected)
	mqtt.broker_disconnected.connect(_on_disconnected)
	mqtt.broker_connection_failed.connect(_on_connection_failed)
	mqtt.publish_acknowledge.connect(_on_publish_ack)

	#Connect Che event signals to publish messages
	MatchSignals.che_mqtt_birth.connect(_publish_dbirth)
	MatchSignals.che_mqtt_death.connect(_publish_ddeath)
	MatchSignals.che_mqtt_data.connect(_publish_ddata)

func connect_to_broker():
	var will_topic = _make_topic("NDEATH")
	var will_payload = _build_payload()
	mqtt.set_last_will(will_topic, will_payload, false, 0)
	mqtt.connect_to_broker(broker_url)
	print("Connecting to MQTT broker:", broker_url)

func _make_topic(message_type: String, device_id := "") -> String:
	if device_id == "":
		return "spBv1.0/%s/%s/%s" % [group_id, node_id, message_type]
	else:
		return "spBv1.0/%s/%s/%s%s/%s" % [group_id, node_id, device_id_prefix, device_id, message_type]

func _build_payload() -> String:
	var payload = {
		"timestamp": Time.get_unix_time_from_system() * 1000,
		"seq": sequence_number
	}
	sequence_number += 1
	return JSON.stringify(payload)

func _publish_nbirth():
	var topic = _make_topic("NBIRTH")
	mqtt.publish(topic, _build_payload(), false, 0)
	print("NBIRTH published:", topic)

func _publish_ndeath():
	var topic = _make_topic("NDEATH")
	mqtt.publish(topic, _build_payload(), false, 0)
	print("NDEATH published:", topic)

func _publish_dbirth(device_id: String):
	var topic = _make_topic("DBIRTH", device_id)
	mqtt.publish(topic, _build_payload(), false, 0)
	print("DBIRTH published:", topic)

func _publish_ddeath(device_id: String):
	var topic = _make_topic("DDEATH", device_id)
	mqtt.publish(topic, _build_payload(), false, 0)
	print("DDEATH published:", topic)

func _publish_ddata(device_id: String, custom_payload: Dictionary):
	var topic = _make_topic("DDATA", device_id)
	var payload = JSON.stringify(custom_payload)
	mqtt.publish(topic, payload, false, 0)
	print("DDATA published:", topic)

# Signal handlers
func _on_message_received(topic: String, message: String):
	broker_received_message.emit(topic, message)
	print("MQTTManager - Message received:", topic, message)

func _on_connected_init():
	MatchSignals.mqtt_spv1_broker_connected_init.emit()
	mqtt.broker_connected.disconnect(_on_connected_init)
	print("MQTTManager - SPv1.0 Broker connected initialized. Continue setup.")

func _on_connected():
	broker_connected.emit()
	print("MQTTManager - Connected to broker.")
	_publish_nbirth()

func _on_disconnected():
	broker_disconnected.emit()
	print("MQTTManager - Disconnected from broker.")

func _on_connection_failed():
	broker_connection_failed.emit()
	print("MQTTManager - Connection to broker failed.")

func _on_publish_ack(id):
	publish_acknowledged.emit(id)
	print("MQTTManager - Publish acknowledged:", id)
