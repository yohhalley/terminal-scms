extends RefCounted

## QC discharge state machine (vessel -> QC -> TT), pure and serialisable.
##
## The state lives in the command Dictionary so it survives the runtime-state
## JSON round trip. The handler never looks at MQTT, the wall clock or other
## CHE: the caller passes the sim time and the id of a counterpart TT that is
## ready for this handoff right now (0 = none). That gate is evaluated by the
## simulator today and can later be driven by an OES "counterpart ready" signal
## without changing this state machine.
##
## idle -> commanded -> picking -> holding -> [waiting_for_tt] -> done
## - picking: wait #1 (pick wait) from command receipt, then the container
##   appears on the QC (taken from the vessel).
## - holding: wait #2 (hold wait) starts as soon as the QC has the container and
##   runs in parallel with the TT arriving.
## - deposit on the TT happens at max(hold wait end, TT ready); if the hold wait
##   ends first, the QC enters waiting_for_tt until the TT is ready.

const IDLE := "idle"
const COMMANDED := "commanded"
const PICKING := "picking"
const HOLDING := "holding"
const WAITING_FOR_TT := "waiting_for_tt"
const DONE := "done"


static func is_finished(command: Dictionary) -> bool:
	return str(command.get("work_state", IDLE)) == DONE


## Accepts the command. Returns the result events to publish, in order.
static func start(command: Dictionary, now_ms: int, pick_wait_ms: int, hold_wait_ms: int) -> Array:
	command["work_state"] = COMMANDED
	command["pick_wait_ms"] = pick_wait_ms
	command["hold_wait_ms"] = hold_wait_ms
	command["load_container_visit_id"] = 0
	command["counterpart_che_id_actual"] = 0
	var events: Array = [_event("acknowledged"), _event("started")]
	for item in events:
		item["work_state"] = COMMANDED
	command["work_state"] = PICKING
	command["wait_until_ms"] = now_ms + pick_wait_ms
	return events


## Advances the machine to `now_ms`. `ready_counterpart_id` is the CHE id of a
## TT that is ready to receive this container now, or 0.
static func step(command: Dictionary, now_ms: int, ready_counterpart_id: int) -> Array:
	var events: Array = []
	for _guard in range(4):
		var state = str(command.get("work_state", IDLE))
		var wait_until = int(command.get("wait_until_ms", 0))
		if state == PICKING and now_ms >= wait_until:
			command["work_state"] = HOLDING
			command["load_container_visit_id"] = int(command.get("container_visit_id", 0))
			command["wait_until_ms"] = now_ms + int(command.get("hold_wait_ms", 0))
			events.append(_event("container_picked_up", int(command.get("source_position_id", 0))))
		elif state == HOLDING and now_ms >= wait_until:
			if ready_counterpart_id > 0:
				events.append_array(_deposit(command, ready_counterpart_id))
			else:
				command["work_state"] = WAITING_FOR_TT
				command["wait_until_ms"] = 0
				events.append(_event("waiting_for_handoff"))
		elif state == WAITING_FOR_TT and ready_counterpart_id > 0:
			events.append_array(_deposit(command, ready_counterpart_id))
		else:
			break
	return events


static func _deposit(command: Dictionary, counterpart_id: int) -> Array:
	var position_id = int(command.get("transfer_position_id", 0))
	command["work_state"] = DONE
	command["wait_until_ms"] = 0
	command["load_container_visit_id"] = 0
	command["counterpart_che_id_actual"] = counterpart_id
	return [
		_event("container_dropped", position_id, counterpart_id),
		_event("handoff_completed", position_id, counterpart_id),
		_event("completed", position_id, counterpart_id),
	]


static func _event(name: String, position_id: int = 0, counterpart_id: int = 0) -> Dictionary:
	return {"event": name, "position_id": position_id, "counterpart_che_id": counterpart_id}
