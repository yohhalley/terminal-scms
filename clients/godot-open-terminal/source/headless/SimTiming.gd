extends RefCounted

## TCL-034: simulation timing profiles and the time-out margin rule.
##
## Precedence for every timing value: explicit env key > profile > built-in
## default. A profile only sets the crane hold ranges; the no-show and stall
## time-outs have one default for both profiles (TCL-011 hand-off design §6.1).
##
## Default profile: `realistic` (D-039: the testing server is the demo). CI,
## the unit tests and the broker job set SIMULATOR_TIMING_PROFILE=fast explicitly.
##
## PENDING E3 / Q22 (profile values are assumptions, not product decisions):
## - `fast`: QC and RTG hold 2..5 s per step. Team assumption; D-037 caps a
##   step at <= 5 s. CI uses these values.
## - `realistic`: QC hold 90..120 s, RTG hold 60..120 s (D-039). Read as the
##   HOLD time only (the move/drive is simulated separately); whether the
##   ranges cover only the hold or hold + move is open (E3 / Q22).

const PROFILE_FAST := "fast"
const PROFILE_REALISTIC := "realistic"
const DEFAULT_PROFILE := PROFILE_REALISTIC
const PROFILES := {
	PROFILE_FAST: {"qc_hold": [2, 5], "rtg_hold": [2, 5]},
	PROFILE_REALISTIC: {"qc_hold": [90, 120], "rtg_hold": [60, 120]},
}
## D-037: a fast-profile step never takes longer than this.
const FAST_STEP_CAP_S := 5

const DEFAULT_TT_NO_SHOW_TIMEOUT_S := 300
const DEFAULT_QC_NO_SHOW_TIMEOUT_S := 300
const DEFAULT_RTG_NO_SHOW_TIMEOUT_S := 300
const DEFAULT_LEG_STALL_TIMEOUT_S := 600

## Longest normal TT drive used for the W4 margin: the demo layout's longest
## QC lane -> block lane road (TCL-011 §8(g): "TT drive 25-50 s").
## PENDING E22 (whether the RTG wait clock includes the TT drive); assumption.
const DEMO_MAX_TT_DRIVE_M := 200.0
## TCL-011 §8(g): every time-out >= 2 x the worst normal wait it can cut short.
const MARGIN_FACTOR := 2


static func is_profile(name: String) -> bool:
	return PROFILES.has(name)


static func profile_names() -> Array:
	return PROFILES.keys()


static func hold_range(profile: String, che_kind: String) -> Array:
	var values: Dictionary = PROFILES.get(profile, PROFILES[DEFAULT_PROFILE])
	return (values[che_kind + "_hold"] as Array).duplicate()


## Worst normal waits (whole seconds) per wait of TCL-011 §4 / §8(g):
## W1 QC waits for TT ~ one TT cycle, bounded by the RTG hold (H2 frees the TT);
## W2 TT waits for QC = the rest of a QC hold; W3 TT waits for RTG = one RTG
## cycle; W4 RTG waits for TT = the TT drive (E22: whether the RTG clock
## includes the drive is open; counting it is the safe side).
static func worst_normal_waits_s(qc_hold: Array, rtg_hold: Array, speed_mps: float) -> Dictionary:
	var drive_s = int(ceil(DEMO_MAX_TT_DRIVE_M / max(speed_mps, 0.1)))
	return {
		"W1": int(rtg_hold[1]),
		"W2": int(qc_hold[1]),
		"W3": int(rtg_hold[1]),
		"W4": drive_s,
	}


## Margin violations as text ([] = ok). `timeouts` holds tt / qc / rtg no-show
## and leg_stall seconds. TCL-011 rev 2 (B1, N2): the stall clock only runs in
## motion/work states and is paused in every wait, so it must clear the longest
## single motion/work phase (a crane hold or the TT drive), not a no-show wait.
## Longest single motion/work phase without a progress result (whole seconds).
static func longest_motion_or_work_s(qc_hold: Array, rtg_hold: Array, speed_mps: float) -> int:
	var drive_s = int(ceil(DEMO_MAX_TT_DRIVE_M / max(speed_mps, 0.1)))
	return maxi(maxi(int(qc_hold[1]), int(rtg_hold[1])), drive_s)


static func margin_violations(qc_hold: Array, rtg_hold: Array, speed_mps: float, timeouts: Dictionary) -> Array:
	var waits = worst_normal_waits_s(qc_hold, rtg_hold, speed_mps)
	var checks = [
		["TtNoShowTimeoutS", int(timeouts.tt), "W1", waits.W1],
		["QcNoShowTimeoutS", int(timeouts.qc), "W2", waits.W2],
		["RtgNoShowTimeoutS", int(timeouts.rtg), "W3", waits.W3],
		["TtNoShowTimeoutS", int(timeouts.tt), "W4", waits.W4],
	]
	checks.append([
		"LegStallTimeoutS", int(timeouts.leg_stall), "longest motion/work phase",
		longest_motion_or_work_s(qc_hold, rtg_hold, speed_mps),
	])
	var out: Array = []
	for check in checks:
		if int(check[1]) < MARGIN_FACTOR * int(check[3]):
			out.append("%s=%s s < %s x %s (%s s)" % [check[0], check[1], MARGIN_FACTOR, check[2], check[3]])
	return out
