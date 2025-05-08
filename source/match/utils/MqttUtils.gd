extends Control

var receivedmessagecount = 0

func connect_to_broker(brokerurl= "localhost", 
					protocol = "tcp://", 
					lastwilltopic = "terminal_simulator/connection_status", 
					lastwillmessage = false, 
					lastwillretain = false, 
					brokeruser = null, 
					brokerpswd = null):
	randomize()
	$MQTT.client_id = "s%d" % randi()
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
	var retval = $MQTT.connect_to_broker("%s%s:%s" % [protocol, brokerurl, "1883"])
	if not retval:
		print("mqtt connection error.")

	else:
		#$VBox/HBoxBrokerControl/status.text = "disconnecting..."
		$MQTT.disconnect_from_server()


func publish(publishtopic, publishmessage, publishretain):
	var qos = 0
	$MQTT.publish(publishtopic, 
				publishmessage, 
				publishretain, 
				qos)
				
func _on_mqtt_received_message(topic, message):
	print("[b]%s[/b] %s\n" % [topic, message])

func subscribe(topic):
	var qos = 0
	$MQTT.subscribe(topic, qos)
	

func unsubscribe(topic):
	$MQTT.unsubscribe(topic)
