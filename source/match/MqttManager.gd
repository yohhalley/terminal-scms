extends Node

var SparkPlugBMQTTManager = preload("res://source/sparkplugB/SparkPlugBMQTTManager.cs")
var spbm = SparkPlugBMQTTManager.new()


func _ready():
	add_child(spbm)

func _connect_signals():

	# Connect MQTT signals for Che updates
	MatchSignals.che_mqtt_birth.connect(_on_che_birth)
	MatchSignals.che_mqtt_update.connect(_on_che_update)
	MatchSignals.che_mqtt_death.connect(_on_che_death)

	if spbm != null:
		if spbm.has_signal("BrokerConnected"):
			spbm.BrokerConnected.connect(_on_broker_connected)
			#print(spbm.SignalName.BrokerConnected)
		if spbm.has_signal("BrokerDisconnected"):
			spbm.BrokerDisconnected.connect(_on_broker_disconnected)
		if spbm.has_signal("BrokerConnectionFailed"):
			spbm.BrokerConnectionFailed.connect(_on_broker_connection_failed)
	
	print("Signal connection list:")
	print(spbm.get_signal_list())
	# print(spbm.get_signal_connection_list("BrokerConnected"))
	print("---")

func _connect_to_broker(broker_address, broker_port, group_id, node_id, username = "", password = ""):
	spbm.call("ConnectToBroker",
		broker_address,  # brokerAddress
		broker_port,     # brokerPort
		group_id,        # groupId
		node_id,         # nodeId
		username,       # username
		password        # password
	)

func _on_broker_connected():
	MatchSignals.broker_connected.emit();

func _on_broker_disconnected():
	MatchSignals.broker_disconnected.emit();

func _on_broker_connection_failed():
	MatchSignals.broker_connection_failed.emit();

func _on_che_birth(device_id):
	spbm.call("PublishDBIRTH", device_id);
	
func _on_che_update(device_id, payload):
	spbm.call("PublishDDATA", device_id, payload);
	
func _on_che_death(device_id):
	spbm.call("PublishDDEATH", device_id);

