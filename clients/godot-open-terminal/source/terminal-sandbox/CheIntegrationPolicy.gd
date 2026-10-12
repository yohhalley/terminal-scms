class_name CheIntegrationPolicy
extends RefCounted

const WORKING_PUBLISH_INTERVAL_MS := 2000
const IDLE_PUBLISH_INTERVAL_MS := 20000


static func move_started_state() -> Dictionary:
	return {
		"working": true,
		"is_available": false,
		"is_dispatched": true,
	}


static func move_completed_state() -> Dictionary:
	return {
		"working": false,
		"is_dispatched": false,
	}


static func transition_events(previous_powered_on: bool, powered_on: bool) -> Array[String]:
	var events: Array[String] = []
	if not powered_on:
		if previous_powered_on:
			events.append("DDEATH")
		return events
	if not previous_powered_on:
		events.append("DBIRTH")
	events.append("DDATA")
	return events


static func should_publish_data(powered_on: bool, working: bool, last_publish_ms: int, now_ms: int) -> bool:
	if not powered_on:
		return false
	var interval_ms = WORKING_PUBLISH_INTERVAL_MS if working else IDLE_PUBLISH_INTERVAL_MS
	return now_ms - last_publish_ms >= interval_ms
