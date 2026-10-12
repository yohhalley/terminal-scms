extends Control

var url = "http://localhost:8000/minitos_core"
var user = ""
var pwd = ""

func _ready():
	$HTTPRequest.request_completed.connect(_on_request_completed)

func RequestCheList(location_id):
	var http_request = HTTPRequest.new()
	add_child(http_request)
	http_request.request_completed.connect(_http_request_for_che_completed)
	
	# Construct Basic Auth header
	var credentials = "%s:%s" % [user, pwd]
	var auth = "Basic " + Marshalls.utf8_to_base64(credentials)

	var headers = [
		"Content-Type: application/json",
		"Authorization: " + auth
	]
	
	var request_url = url + "/locations/" + location_id + "/ches/"
	print("Sending HTTP request:", request_url)
	
	# Send GET request to the specified endpoint with headers
	var error = http_request.request(
		request_url,  # URL
		headers,                                # headers
		HTTPClient.METHOD_GET                   # method
	)

	if error != OK:
		push_error("An error occurred in the HTTP request")
		
func RequestBlockList(location_id):
	var http_request = HTTPRequest.new()
	add_child(http_request)
	http_request.request_completed.connect(_http_request_for_blocks_completed)
	
	# Construct Basic Auth header
	var credentials = "%s:%s" % [user, pwd]
	var auth = "Basic " + Marshalls.utf8_to_base64(credentials)

	var headers = [
		"Content-Type: application/json",
		"Authorization: " + auth
	]
	
	var request_url = url + "/locations/" + location_id + "/storage-grids/"
	print("Sending HTTP request:", request_url)
	
	# Send GET request to the specified endpoint with headers
	var error = http_request.request(
		request_url,  # URL
		headers,                                # headers
		HTTPClient.METHOD_GET                   # method
	)

	if error != OK:
		push_error("An error occurred in the HTTP request")
		
func RequestContainerVisitList(location_id):
	var http_request = HTTPRequest.new()
	add_child(http_request)
	http_request.request_completed.connect(_http_request_for_container_visits_completed)
	
	# Construct Basic Auth header
	var credentials = "%s:%s" % [user, pwd]
	var auth = "Basic " + Marshalls.utf8_to_base64(credentials)

	var headers = [
		"Content-Type: application/json",
		"Authorization: " + auth
	]
	
	var request_url = url + "/locations/" + location_id + "/container_visits/"
	print("Sending HTTP request:", request_url)
	
	# Send GET request to the specified endpoint with headers
	var error = http_request.request(
		request_url,  # URL
		headers,                                # headers
		HTTPClient.METHOD_GET                   # method
	)

	if error != OK:
		push_error("An error occurred in the HTTP request")

func _on_request_completed(result, response_code, headers, body):
	print("HTTP Request completed with response code:", response_code)
	if result != HTTPRequest.RESULT_SUCCESS:
		push_error("Something didn't work.")
	else:
		var text = body.get_string_from_utf8()
		#print("Response Body:", text)

func _http_request_for_che_completed(result, response_code, headers, body):
	print("HTTP Request completed with response code:", response_code)
	if result != HTTPRequest.RESULT_SUCCESS:
		push_error("Something didn't work.")
	else:
		var text = body.get_string_from_utf8()
		#print("Response Body:", text)
		MatchSignals.che_list_received.emit(text)
		

func _http_request_for_blocks_completed(result, response_code, headers, body):
	print("HTTP Request completed with response code:", response_code)
	if result != HTTPRequest.RESULT_SUCCESS:
		push_error("Something didn't work.")
	else:
		var text = body.get_string_from_utf8()
		#print("Response Body:", text)
		MatchSignals.block_list_received.emit(text)
		

func _http_request_for_container_visits_completed(result, response_code, headers, body):
	print("HTTP Request completed with response code:", response_code)
	if result != HTTPRequest.RESULT_SUCCESS:
		push_error("Something didn't work.")
	else:
		var text = body.get_string_from_utf8()
		#print("Response Body:", text)
		MatchSignals.container_visits_list_received.emit(text)
