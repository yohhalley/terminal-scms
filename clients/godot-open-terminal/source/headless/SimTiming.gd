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
## D-051 (closes E3/Q22): the realistic timings are the FULL crane cycle, and
## hold = max(0, cycle - simulated motion). Today the sim models no crane motion
## (QC: the box appears at command, D-012, and the deposit is instant; RTG:
## gantry_to_bay, pick and drop take 0 s), so hold = cycle. When hoist/trolley
## or gantry motion is modelled (D-039, post-MVP), raise *_SIMULATED_MOTION_S
## and the hold shrinks; the cycle stays the user-facing number.
## - `realistic`: QC and RTG cycle REALISTIC_*_CYCLE_S (60 s, Yael's ~30 s
##   dispatch -> twistlocks locked + ~30 s locked -> open). The exact range is
##   PENDING Yael (TCL-034e Y1); change only the two constants below. The
##   whole RTG cycle sits after the pick (TCL-034e default, Y2 pending).
## - `fast`: QC and RTG hold 2..5 s per step, used directly (test profile,
##   D-037 / D-050 cap a step at <= 5 s). CI uses these values.

## TCL-034e Y1 (pending Yael): realistic cycle, inclusive whole seconds [min, max].
const REALISTIC_QC_CYCLE_S := [60, 60]
const REALISTIC_RTG_CYCLE_S := [60, 60]
## Crane motion the sim simulates inside one cycle (whole seconds). 0 today.
const QC_SIMULATED_MOTION_S := 0
const RTG_SIMULATED_MOTION_S := 0

const PROFILE_FAST := "fast"
const PROFILE_REALISTIC := "realistic"
const DEFAULT_PROFILE := PROFILE_REALISTIC
## A profile gives either a `<kind>_hold` range (used as is) or a
## `<kind>_cycle` range (hold derived with hold_from_cycle).
const PROFILES := {
	PROFILE_FAST: {"qc_hold": [2, 5], "rtg_hold": [2, 5]},
	PROFILE_REALISTIC: {"qc_cycle": REALISTIC_QC_CYCLE_S, "rtg_cycle": REALISTIC_RTG_CYCLE_S},
}
const SIMULATED_MOTION_S := {"qc": QC_SIMULATED_MOTION_S, "rtg": RTG_SIMULATED_MOTION_S}
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


## Effective hold range [min, max] (whole seconds) of `che_kind` (qc | rtg)
## in `profile`: the profile's hold range, or its cycle range minus the
## simulated motion (D-051).
static func hold_range(profile: String, che_kind: String) -> Array:
	var values: Dictionary = PROFILES.get(profile, PROFILES[DEFAULT_PROFILE])
	if values.has(che_kind + "_hold"):
		return (values[che_kind + "_hold"] as Array).duplicate()
	return hold_from_cycle(values[che_kind + "_cycle"], int(SIMULATED_MOTION_S.get(che_kind, 0)))


## D-051: hold = max(0, cycle - simulated motion), applied to both ends.
static func hold_from_cycle(cycle_s: Array, motion_s: int) -> Array:
	return [maxi(0, int(cycle_s[0]) - motion_s), maxi(0, int(cycle_s[1]) - motion_s)]


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


## Longest single motion/work phase without a progress result (whole seconds).
static func longest_motion_or_work_s(qc_hold: Array, rtg_hold: Array, speed_mps: float) -> int:
	var drive_s = int(ceil(DEMO_MAX_TT_DRIVE_M / max(speed_mps, 0.1)))
	return maxi(maxi(int(qc_hold[1]), int(rtg_hold[1])), drive_s)


## Margin violations as text ([] = ok). `timeouts` holds tt / qc / rtg no-show
## and leg_stall seconds. TCL-011 rev 2 (B1, N2): the stall clock only runs in
## motion/work states and is paused in every wait, so it must clear the longest
## single motion/work phase (a crane hold or the TT drive), not a no-show wait.
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
