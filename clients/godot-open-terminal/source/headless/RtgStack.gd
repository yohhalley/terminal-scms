extends RefCounted

## RTG stack state machine (TT -> RTG -> dummy yard slot), pure and serialisable.
##
## Mirror of QcDischarge for the receiving end of the import flow (TCL-014b).
## The state lives in the command Dictionary so it survives the runtime-state
## JSON round trip. The caller passes the sim time and the id of a TT that is
## ready to give this container right now (0 = none); the simulator owns that
## gate (same HandoffId, TT waiting at the block lane, counterparts match).
##
## Contract rev 9.1 §3.4 canonical RTG states:
## idle -> gantry_to_bay -> [waiting_for_tt] -> picking_from_tt -> holding
##      -> depositing_to_stack -> idle
## - On command receipt: acknowledged + started (gantry_to_bay). No gantry
##   travel is modelled, so gantry_to_bay takes zero sim time.
## - The pick from the TT happens at the first step the TT is ready. If the TT
##   is not ready yet, the RTG enters waiting_for_tt (waiting_for_handoff).
## - Pick = handoff_completed (receiver), then the seeded hold (whole seconds,
##   Sim/Timing/RtgHold*) before the container is put down in the commanded
##   slot (or the dummy slot when OES sent none): container_dropped
##   (depositing_to_stack, slot echoed) + completed (idle).
## - D-013 analogue: after `tt_no_show_timeout_ms` of sim time in
##   waiting_for_tt the RTG publishes handoff_timed_out once and the HandoffId
##   is no longer valid here (a late TT gets no transfer). OES cancels.

const IDLE := "idle"
const GANTRY_TO_BAY := "gantry_to_bay"
## Canonical since contract rev 9.1 (Tech Lead decision (a), mirrors the QC).
const WAITING_FOR_TT := "waiting_for_tt"
const PICKING_FROM_TT := "picking_from_tt"
const HOLDING := "holding"
const DEPOSITING_TO_STACK := "depositing_to_stack"
const DEFAULT_TT_NO_SHOW_TIMEOUT_MS := 300000


static func is_finished(command: Dictionary) -> bool:
	return bool(command.get("rtg_finished", false))


## Accepts the command. `hold_wait_ms` is the seeded stack hold used after the
## pick; `slot` is the yard slot the container ends in ({"position_id", "position_code"}).
static func start(
	command: Dictionary,
	hold_wait_ms: int,
	slot: Dictionary,
	tt_no_show_timeout_ms: int = DEFAULT_TT_NO_SHOW_TIMEOUT_MS
) -> Array:
	command["work_state"] = GANTRY_TO_BAY
	command["rtg_finished"] = false
	command["hold_wait_ms"] = hold_wait_ms
	command["tt_no_show_timeout_ms"] = tt_no_show_timeout_ms
	command["waiting_since_ms"] = 0
	command["wait_until_ms"] = 0
	command["handoff_timed_out"] = false
	command["counterpart_che_id_actual"] = 0
	command["load_container_visit_id"] = 0
	command["slot_position_id"] = int(slot.get("position_id", 0))
	command["slot_position_code"] = str(slot.get("position_code", ""))
	var events: Array = [_event("acknowledged"), _event("started")]
	for item in events:
		item["work_state"] = GANTRY_TO_BAY
	return events


## Advances the machine to `now_ms`. `ready_counterpart_id` is the CHE id of a
## TT that is ready to give this container now, or 0.
static func step(command: Dictionary, now_ms: int, ready_counterpart_id: int) -> Array:
	var events: Array = []
	for _guard in range(4):
		var state = str(command.get("work_state", IDLE))
		if state == GANTRY_TO_BAY:
			if ready_counterpart_id > 0:
				events.append_array(_pick(command, now_ms, ready_counterpart_id))
			else:
				command["work_state"] = WAITING_FOR_TT
				command["waiting_since_ms"] = now_ms
				events.append(_event("waiting_for_handoff"))
		elif state == WAITING_FOR_TT and bool(command.get("handoff_timed_out", false)):
			break
		elif state == WAITING_FOR_TT and ready_counterpart_id > 0:
			events.append_array(_pick(command, now_ms, ready_counterpart_id))
		elif state == WAITING_FOR_TT and _no_show_due(command, now_ms):
			command["handoff_timed_out"] = true
			events.append(_event("handoff_timed_out"))
			break
		elif state == HOLDING and now_ms >= int(command.get("wait_until_ms", 0)):
			events.append_array(_drop(command))
		else:
			break
	return events


## True when the last step() picked the container from the TT (the simulator
## then completes the TT side of the hand-off in the same fixed step).
static func picked_in(events: Array) -> bool:
	for item in events:
		if str(item.get("event", "")) == "handoff_completed":
			return true
	return false


## OES cancel: stop wherever the machine is, clear any container and return to
## idle. Returns the result events to publish.
static func cancel(command: Dictionary) -> Array:
	command["work_state"] = IDLE
	command["wait_until_ms"] = 0
	command["load_container_visit_id"] = 0
	var item = _event("cancelled")
	item["work_state"] = IDLE
	return [item]


static func _no_show_due(command: Dictionary, now_ms: int) -> bool:
	var timeout_ms = int(command.get("tt_no_show_timeout_ms", DEFAULT_TT_NO_SHOW_TIMEOUT_MS))
	return timeout_ms > 0 and now_ms - int(command.get("waiting_since_ms", now_ms)) >= timeout_ms


static func _pick(command: Dictionary, now_ms: int, counterpart_id: int) -> Array:
	var position_id = int(command.get("transfer_position_id", 0))
	command["counterpart_che_id_actual"] = counterpart_id
	command["load_container_visit_id"] = int(command.get("container_visit_id", 0))
	command["waiting_since_ms"] = 0
	var handoff = _event("handoff_completed", position_id, counterpart_id)
	handoff["handoff_role"] = "receiver"
	handoff["work_state"] = PICKING_FROM_TT
	command["work_state"] = HOLDING
	command["wait_until_ms"] = now_ms + int(command.get("hold_wait_ms", 0))
	return [handoff]


static func _drop(command: Dictionary) -> Array:
	var counterpart_id = int(command.get("counterpart_che_id_actual", 0))
	var position_id = int(command.get("slot_position_id", 0))
	command["work_state"] = IDLE
	command["rtg_finished"] = true
	command["wait_until_ms"] = 0
	command["load_container_visit_id"] = 0
	var dropped = _event("container_dropped", position_id, counterpart_id)
	dropped["work_state"] = DEPOSITING_TO_STACK
	var done = _event("completed", position_id, counterpart_id)
	done["work_state"] = IDLE
	var code = str(command.get("slot_position_code", ""))
	if not code.is_empty():
		dropped["position_code"] = code
		done["position_code"] = code
	return [dropped, done]


static func _event(name: String, position_id: int = 0, counterpart_id: int = 0) -> Dictionary:
	return {"event": name, "position_id": position_id, "counterpart_che_id": counterpart_id}
