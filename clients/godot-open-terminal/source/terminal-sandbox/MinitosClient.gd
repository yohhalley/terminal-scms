extends Node

signal server_checked(ok, message)
signal authenticated(ok, message)
signal locations_loaded(ok, locations, message)
signal areas_loaded(ok, areas, message)
signal terminal_layout_loaded(ok, location_data, areas, roads, ches, message)
signal object_saved(ok, object_data, message)

var server_url := ""
var username := ""
var password := ""

var _pending_request := ""
var _pending_context := {}

@onready var _http := HTTPRequest.new()


func _ready():
	add_child(_http)
	_http.request_completed.connect(_on_request_completed)


func configure(p_server_url: String, p_username := "", p_password := ""):
	server_url = _normalize_server_url(p_server_url)
	username = p_username
	password = p_password


func check_server(p_server_url: String):
	configure(p_server_url, username, password)
	_pending_request = "check_server"
	_pending_context = {}
	_send_json_request(_locations_url(), HTTPClient.METHOD_GET, false)


func authenticate(p_username: String, p_password: String):
	username = p_username
	password = p_password
	_pending_request = "authenticate"
	_pending_context = {}
	_send_json_request(_locations_url(), HTTPClient.METHOD_GET, true)


func load_locations():
	_pending_request = "load_locations"
	_pending_context = {}
	_send_json_request(_locations_url(), HTTPClient.METHOD_GET, true)


func load_areas(location_id: String):
	_pending_request = "load_areas"
	_pending_context = {}
	_send_json_request("%s/locations/%s/layout-assets/" % [server_url, location_id], HTTPClient.METHOD_GET, true)


func refresh_terminal(location_id: String):
	_pending_request = "refresh_location"
	_pending_context = {"location_id": location_id}
	_send_json_request("%s/locations/%s/" % [server_url, location_id], HTTPClient.METHOD_GET, true)


func update_che_snapshot(location_id: String, che: Dictionary):
	var request = HTTPRequest.new()
	add_child(request)
	request.request_completed.connect(func(_result, _response_code, _headers, _body): request.queue_free())
	var headers = ["Accept: application/json", "Content-Type: application/json", _auth_header()]
	var che_id = str(che.get("pk", che.get("id", "")))
	var payload = {
		"powered_on": bool(che.get("powered_on", false)),
		"working": bool(che.get("working", false)),
		"is_available": bool(che.get("is_available", false)),
		"is_dispatched": bool(che.get("is_dispatched", false)),
		"is_in_maintenance": bool(che.get("is_in_maintenance", false)),
		"latitude": che.get("latitude"),
		"longitude": che.get("longitude"),
		"rotation": float(che.get("rotation", 0.0)),
		"fuel_level": float(che.get("fuel_level", 100.0)),
	}
	if che.has("current_asset"):
		payload["current_asset"] = che.current_asset
	request.request(
		"%s/locations/%s/ches/%s/" % [server_url, location_id, che_id],
		headers,
		HTTPClient.METHOD_PATCH,
		JSON.stringify(payload)
	)


func update_container_visit_position(location_id: String, visit_id: String, latitude: float, longitude: float):
	_pending_request = "update_container_visit_position"
	_pending_context = {"visit_id": visit_id}
	_send_json_request(
		"%s/locations/%s/container_visits/%s/" % [server_url, location_id, visit_id],
		HTTPClient.METHOD_PATCH,
		true,
		{"latitude": latitude, "longitude": longitude}
	)


func create_design_object(location_id: String, object_data: Dictionary):
	_pending_request = "create_design_object"
	_pending_context = {"object_data": object_data}

	var object_type = object_data.get("type", "yard_block")
	if object_data.has("coordinates"):
		var coordinates: Array = object_data.get("coordinates", [])
		if coordinates.size() >= 3 and coordinates[0] != coordinates[-1]:
			coordinates.append(coordinates[0])
		var area_payload = {
			"code": object_data.get("code", _make_code(object_type)),
			"name": object_data.get("name", "Area"),
			"asset_kind": str(object_type).replace(" ", "_"),
			"geometry": {"type": "Polygon", "coordinates": [coordinates]},
			"is_visible": bool(object_data.get("is_visible", true))
		}
		if object_type == "yard_block":
			area_payload["storage_grid_settings"] = _storage_grid_payload(object_data)
		_send_json_request("%s/locations/%s/layout-assets/" % [server_url, location_id], HTTPClient.METHOD_POST, true, area_payload)
		return

	if object_type == "yard_block":
		var latitude = float(object_data.get("latitude", 0.0))
		var longitude = float(object_data.get("longitude", 0.0))
		var payload = {
			"code": object_data.get("code", _make_code(object_type)),
			"name": object_data.get("name", "Block"),
			"asset_kind": "yard_block",
			"geometry": {"type": "Point", "coordinates": [longitude, latitude]},
			"storage_grid_settings": _storage_grid_payload(object_data)
		}
		_send_json_request("%s/locations/%s/layout-assets/" % [server_url, location_id], HTTPClient.METHOD_POST, true, payload)
		return

	var pow_payload = {
		"name": object_data.get("name", "Point of Work"),
		"pow_type": "terminal area",
		"location": int(location_id),
		"code": object_data.get("code", _make_code(object_type)),
		"latitude": object_data.get("latitude"),
		"longitude": object_data.get("longitude"),
		"max_throughput": int(object_data.get("max_throughput", 30)),
		"is_active": true
	}
	_send_json_request("%s/points-of-work/" % server_url, HTTPClient.METHOD_POST, true, pow_payload)


func _send_json_request(url: String, method: int, include_auth: bool, payload: Variant = null):
	var headers = ["Accept: application/json", "Content-Type: application/json"]
	if include_auth:
		headers.append(_auth_header())

	var body = ""
	if payload != null:
		body = JSON.stringify(payload)

	var error = _http.request(url, headers, method, body)
	if error != OK:
		_emit_pending_failure("Could not start HTTP request: %s" % error)


func _on_request_completed(result, response_code, headers, body):
	var body_text = body.get_string_from_utf8()
	var reachable = result == HTTPRequest.RESULT_SUCCESS and response_code > 0

	if _pending_request == "check_server":
		if reachable:
			var message = "Server reachable"
			if response_code in [401, 403]:
				message = "Server reachable, authentication required"
			server_checked.emit(true, message)
		else:
			server_checked.emit(false, "Server did not respond")
		return

	if not reachable:
		_emit_pending_failure("No HTTP response from server")
		return

	if response_code < 200 or response_code >= 300:
		_emit_pending_failure("HTTP %s: %s" % [response_code, body_text.left(180)])
		return

	var parsed = _parse_json(body_text)
	match _pending_request:
		"authenticate":
			authenticated.emit(true, "Credentials accepted")
		"load_locations":
			locations_loaded.emit(true, _as_list(parsed), "Locations loaded")
		"load_areas":
			areas_loaded.emit(true, _layout_assets_as_areas(_as_list(parsed)), "Layout assets loaded")
		"refresh_location":
			_pending_context["location"] = parsed
			_pending_request = "refresh_assets"
			_send_json_request("%s/locations/%s/layout-assets/" % [server_url, _pending_context["location_id"]], HTTPClient.METHOD_GET, true)
		"refresh_assets":
			_pending_context["areas"] = _layout_assets_as_areas(_as_list(parsed))
			_pending_request = "refresh_axes"
			_send_json_request("%s/locations/%s/linear-axes/" % [server_url, _pending_context["location_id"]], HTTPClient.METHOD_GET, true)
		"refresh_axes":
			_pending_context["roads"] = _linear_axes_as_roads(_as_list(parsed))
			_pending_request = "refresh_ches"
			_send_json_request("%s/locations/%s/ches/" % [server_url, _pending_context["location_id"]], HTTPClient.METHOD_GET, true)
		"refresh_ches":
			terminal_layout_loaded.emit(
				true,
				_pending_context.get("location", {}),
				_pending_context.get("areas", []),
				_pending_context.get("roads", []),
				_as_list(parsed),
				"Terminal layout refreshed"
			)
		"create_design_object":
			object_saved.emit(true, parsed, "Object saved to Minitos")
		"update_container_visit_position":
			object_saved.emit(true, parsed, "Position updated")


func _emit_pending_failure(message: String):
	match _pending_request:
		"check_server":
			server_checked.emit(false, message)
		"authenticate":
			authenticated.emit(false, message)
		"load_locations":
			locations_loaded.emit(false, [], message)
		"load_areas":
			areas_loaded.emit(false, [], message)
		"refresh_location", "refresh_assets", "refresh_axes", "refresh_ches":
			terminal_layout_loaded.emit(false, {}, [], [], [], message)
		"create_design_object", "update_container_visit_position":
			object_saved.emit(false, _pending_context.get("object_data", {}), message)


func _parse_json(text: String):
	var json = JSON.new()
	if json.parse(text) != OK:
		return {}
	var data = json.get_data()
	if data is Dictionary and data.has("results"):
		return data["results"]
	return data


func _as_list(data) -> Array:
	if data is Array:
		return data
	if data is Dictionary:
		return [data]
	return []


func _layout_assets_as_areas(assets: Array) -> Array:
	var areas: Array = []
	for asset in assets:
		if not asset is Dictionary:
			continue
		var geometry = asset.get("geometry", {})
		if not geometry is Dictionary or geometry.get("type", "") != "Polygon":
			continue
		var rings = geometry.get("coordinates", [])
		if not rings is Array or rings.is_empty():
			continue
		var area_type = str(asset.get("asset_kind", "other")).replace("_", " ")
		if area_type == "terminal":
			area_type = "terminal area"
		var area = {
			"id": asset.get("id"),
			"name": asset.get("name", asset.get("code", "Area")),
			"area_type": area_type,
			"coordinates": rings[0],
			"is_visible": asset.get("is_visible", true),
			"heading_deg": asset.get("heading_deg", 0.0),
			"metadata": asset.get("metadata", {})
		}
		if asset.get("storage_grid") is Dictionary:
			area["block_config"] = asset["storage_grid"]
		areas.append(area)
	return areas


func _linear_axes_as_roads(axes: Array) -> Array:
	var roads: Array = []
	for axis in axes:
		if not axis is Dictionary:
			continue
		if axis.get("axis_kind", "") not in ["road_centerline", "rail_track", "transfer_lane"]:
			continue
		roads.append({
			"id": axis.get("id"),
			"name": axis.get("name", axis.get("code", "Axis")),
			"points": axis.get("points", []),
			"lane_count": axis.get("lane_count", 1),
			"capacity": axis.get("capacity", 1),
			"direction": axis.get("direction", "both"),
			"axis_kind": axis.get("axis_kind", "road_centerline"),
			"is_enabled": axis.get("is_enabled", true)
		})
	return roads


func _storage_grid_payload(object_data: Dictionary) -> Dictionary:
	return {
		"name": object_data.get("name", "Block"),
		"grid_kind": "automated",
		"max_bays": int(object_data.get("max_bays", 6)),
		"max_rows": int(object_data.get("max_rows", 4)),
		"max_tier": int(object_data.get("max_tier", 5)),
		"latitude": object_data.get("latitude"),
		"longitude": object_data.get("longitude"),
		"heading_deg": float(object_data.get("heading_deg", 0.0)),
		"is_heapzone": false
	}


func _locations_url() -> String:
	return "%s/locations/" % server_url


func _auth_header() -> String:
	var credentials = "%s:%s" % [username, password]
	return "Authorization: Basic " + Marshalls.utf8_to_base64(credentials)


func _normalize_server_url(value: String) -> String:
	var normalized = value.strip_edges()
	if not normalized.begins_with("http://") and not normalized.begins_with("https://"):
		normalized = "http://" + normalized
	normalized = normalized.trim_suffix("/")
	if not normalized.ends_with("/minitos_core"):
		normalized += "/minitos_core"
	return normalized


func _make_code(object_type: String) -> String:
	return "%s-%s" % [object_type.left(4).to_upper(), int(Time.get_unix_time_from_system())]
