class_name OESBootstrapClient
extends Node

signal bootstrap_loaded(ok: bool, document: Dictionary, message: String)

var _http: HTTPRequest


func _ready():
	_http = HTTPRequest.new()
	add_child(_http)
	_http.request_completed.connect(_on_request_completed)


func load_bootstrap(oes_url: String, session_id: String, token: String):
	var base_url = oes_url.strip_edges().trim_suffix("/")
	var url = "%s/api/simulation-sessions/%s/bootstrap/" % [base_url, session_id]
	var headers = [
		"Accept: application/json",
		"X-Simulation-Token: %s" % token,
	]
	var error = _http.request(url, headers, HTTPClient.METHOD_GET)
	if error != OK:
		bootstrap_loaded.emit(false, {}, "Could not start OES bootstrap request: %s" % error)


func _on_request_completed(result: int, response_code: int, _headers: PackedStringArray, body: PackedByteArray):
	if result != HTTPRequest.RESULT_SUCCESS:
		bootstrap_loaded.emit(false, {}, "OES bootstrap request failed: %s" % result)
		return
	var body_text = body.get_string_from_utf8()
	if response_code < 200 or response_code >= 300:
		bootstrap_loaded.emit(false, {}, "OES bootstrap HTTP %s: %s" % [response_code, body_text.left(180)])
		return
	var json = JSON.new()
	if json.parse(body_text) != OK or not json.get_data() is Dictionary:
		bootstrap_loaded.emit(false, {}, "OES bootstrap response is not a JSON object")
		return
	bootstrap_loaded.emit(true, json.get_data(), "OES simulation snapshot loaded")
