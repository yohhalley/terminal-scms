extends SceneTree

const MQTTNodeClient = preload("res://source/terminal-sandbox/MQTTNodeClient.gd")
const MQTTTransport = preload("res://addons/mqtt/mqtt.gd")
const SparkPlugManager = preload("res://source/sparkplugB/SparkPlugMQTTManager.gd")

var _client := MQTTNodeClient.new()
var _observer = MQTTTransport.new()
var _elapsed := 0.0
var _connected_at := -1.0
var _lifecycle_published := false
var _disconnect_requested := false
var _failed := false
var _host := "127.0.0.1"
var _port := 1883
var _received_topics: Array[String] = []
var _saw_powered_off_data := false

const EXPECTED_TOPICS: Array[String] = [
	"spBv1.0/MINI-TERMINAL/NBIRTH/GODOT-SANDBOX",
	"spBv1.0/MINI-TERMINAL/DBIRTH/GODOT-SANDBOX/CHE_999",
	"spBv1.0/MINI-TERMINAL/DDATA/GODOT-SANDBOX/CHE_999",
	"spBv1.0/MINI-TERMINAL/DDEATH/GODOT-SANDBOX/CHE_999",
	"spBv1.0/MINI-TERMINAL/NDEATH/GODOT-SANDBOX",
]


func _initialize():
	get_root().add_child(_client)
	_client.broker_checked.connect(_on_broker_checked)
	_client.message_rejected.connect(_on_message_rejected)
	var configured_host = OS.get_environment("MQTT_TEST_HOST")
	if not configured_host.is_empty():
		_host = configured_host
	var configured_port = int(OS.get_environment("MQTT_TEST_PORT"))
	if configured_port > 0:
		_port = configured_port
	_observer.client_id = "godot-terminal-sandbox-test-observer"
	_observer.binarymessages = true
	_observer.verbose_level = 1
	_observer.broker_connected.connect(_on_observer_connected)
	_observer.broker_connection_failed.connect(func(): _fail("Observer could not connect to MQTT broker"))
	_observer.subscribe_acknowledge.connect(_on_observer_subscribed)
	_observer.received_message.connect(_on_observer_message)
	get_root().add_child(_observer)
	print("Connecting live MQTT integration check to %s:%s" % [_host, _port])
	_observer.connect_to_broker("tcp://%s:%s" % [_host, _port])


func _process(delta):
	_elapsed += delta
	if _elapsed >= 10.0:
		_fail("Timed out waiting for the local MQTT lifecycle")
		return
	if _connected_at < 0.0:
		return
	var connected_elapsed = _elapsed - _connected_at
	if not _lifecycle_published and connected_elapsed >= 0.25:
		_lifecycle_published = true
		var che = {
			"pk": 999,
			"name": "MQTT-INTEGRATION-CHE",
			"equipment_type_code": "TT",
			"powered_on": true,
			"working": false,
			"is_available": true,
			"latitude": 17.9491,
			"longitude": -102.1504,
			"fuel_level": 75.0,
		}
		if not _client.publish_che_birth(che):
			_fail("DBIRTH was rejected")
			return
		if not _client.publish_che_data(che):
			_fail("DDATA was rejected")
			return
		che.powered_on = false
		che.is_available = false
		if not _client.publish_che_data(che):
			_fail("Powered-off DDATA was rejected")
			return
		if not _client.publish_che_death(che):
			_fail("DDEATH was rejected")
			return
	if _lifecycle_published and not _disconnect_requested and connected_elapsed >= 0.75:
		_disconnect_requested = true
		_client.disconnect_from_broker()
	if _disconnect_requested and connected_elapsed >= 2.0:
		var missing = EXPECTED_TOPICS.filter(func(topic): return not _received_topics.has(topic))
		if not missing.is_empty():
			_fail("Broker did not deliver topics: %s" % ", ".join(missing))
			return
		if not _saw_powered_off_data:
			_fail("Broker did not deliver a DDATA observation with PoweredOn=false")
			return
		print("Local MQTT lifecycle received successfully")
		quit(1 if _failed else 0)


func _on_observer_connected():
	_observer.subscribe("spBv1.0/MINI-TERMINAL/#", 0)


func _on_observer_subscribed(_packet_id: int):
	_client.connect_to_broker(_host, _port)


func _on_observer_message(topic: String, payload):
	_received_topics.append(topic)
	if "/DDATA/" in topic:
		var document = SparkPlugManager.decode_payload(payload)
		var metrics = SparkPlugManager.metrics_by_name(document)
		if metrics.get("State/PoweredOn") == false:
			_saw_powered_off_data = true
	print("Broker delivered: ", topic)


func _on_broker_checked(ok: bool, message: String):
	print(message)
	if ok:
		_connected_at = _elapsed
	elif not _disconnect_requested:
		_fail(message)


func _on_message_rejected(message_type: String, device_id: String, reason: String):
	_fail("%s for %s rejected: %s" % [message_type, device_id, reason])


func _fail(message: String):
	if _failed:
		return
	_failed = true
	push_error(message)
	quit(1)
