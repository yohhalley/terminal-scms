extends RefCounted

## Fixed-step simulation clock.
##
## Simulation logic only ever advances in whole steps of `step_ms`. The time
## scale only changes how many steps run per real second, so the sequence of
## simulated states is the same at x1 and at x50. Neither frame delta nor the
## wall clock enter the simulated state.

const DEFAULT_STEP_MS := 100
## Upper bound of steps per real frame; any backlog beyond it is dropped so a
## stalled process cannot spiral (the sim then runs slower than the scale).
const MAX_STEPS_PER_FRAME := 1000

var step_ms: int = DEFAULT_STEP_MS
var time_scale := 1.0
var now_ms: int = 0
var _pending_ms := 0.0


func _init(fixed_step_ms: int = DEFAULT_STEP_MS, scale: float = 1.0):
	step_ms = maxi(fixed_step_ms, 1)
	set_time_scale(scale)


func set_time_scale(scale: float):
	time_scale = maxf(scale, 0.01)


func step_seconds() -> float:
	return step_ms / 1000.0


## Converts real elapsed seconds into a whole number of fixed steps to run.
## Does not advance `now_ms`; call `step()` once per returned step.
func steps_for_real_delta(real_delta_s: float) -> int:
	_pending_ms += maxf(real_delta_s, 0.0) * 1000.0 * time_scale
	var steps = int(floor(_pending_ms / step_ms))
	if steps > MAX_STEPS_PER_FRAME:
		_pending_ms = 0.0
		return MAX_STEPS_PER_FRAME
	_pending_ms -= steps * step_ms
	return steps


func step() -> int:
	now_ms += step_ms
	return now_ms


func restore(sim_time_ms: int):
	now_ms = maxi(sim_time_ms, 0)
	_pending_ms = 0.0
