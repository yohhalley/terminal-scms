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
## - `realistic`: QC and RTG cycle REALISTIC_*_CYCLE_S, 60 s each (D-054).
##   QC: no motion is simulated, so hold = 60 s.
##   RTG (D-058): the cycle is ~30 s positioning before the truck arrives
##   (REALISTIC_RTG_POSITIONING_S, simulated in gantry_to_bay, from the RTG
##   command) + the rest after the pick (hold = 60 - 30 = 30 s). The RTG
##   no-show clock (W4) starts only when positioning ends (D-052 ready to pick).
## - `fast`: QC and RTG hold 2..5 s per step, used directly (test profile,
##   D-037 / D-050 cap a step at <= 5 s), and no RTG positioning (0 s, so
##   fast runs are unchanged by D-058). CI uses these values.

## D-054: realistic cycle, inclusive whole seconds [min, max].
const REALISTIC_QC_CYCLE_S := [60, 60]
const REALISTIC_RTG_CYCLE_S := [60, 60]
## D-058: RTG positioning before the truck arrives (whole seconds, realistic).
const REALISTIC_RTG_POSITIONING_S := 30
## Crane motion the sim simulates inside one realistic cycle (whole seconds).
## QC: none. RTG: the D-058 positioning.
const QC_SIMULATED_MOTION_S := 0
const RTG_SIMULATED_MOTION_S := REALISTIC_RTG_POSITIONING_S

const PROFILE_FAST := "fast"
const PROFILE_REALISTIC := "realistic"
const DEFAULT_PROFILE := PROFILE_REALISTIC
## A profile gives either a `<kind>_hold` range (used as is) or a
## `<kind>_cycle` range (hold derived with hold_from_cycle).
const PROFILES := {
	PROFILE_FAST: {"qc_hold": [2, 5], "rtg_hold": [2, 5], "rtg_positioning_s": 0},
	PROFILE_REALISTIC: {
		"qc_cycle": REALISTIC_QC_CYCLE_S, "rtg_cycle": REALISTIC_RTG_CYCLE_S,
		"rtg_positioning_s": REALISTIC_RTG_POSITIONING_S,
	},
}
const SIMULATED_MOTION_S := {"qc": QC_SIMULATED_MOTION_S, "rtg": RTG_SIMULATED_MOTION_S}
## D-037: a fast-profile step never takes longer than this.
const FAST_STEP_CAP_S := 5

const DEFAULT_TT_NO_SHOW_TIMEOUT_S := 300
const DEFAULT_QC_NO_SHOW_TIMEOUT_S := 300
const DEFAULT_RTG_NO_SHOW_TIMEOUT_S := 300
const DEFAULT_LEG_STALL_TIMEOUT_S := 600

## Longest normal TT drive used for the W4 margin: the demo layout's longest
## QC lane -> block lane road (TCL-011 §8(g): "TT drive 25-50 s"). D-052: W4
## starts when the RTG is at the bay and ready to pick; the RTG can be ready
## before the loaded TT arrives (TCL-011 N4), so the drive stays the worst W4.
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


## D-058: whole seconds the RTG spends in gantry_to_bay (positioning) before
## it is ready to pick, in `profile`. fast: 0.
static func rtg_positioning_s(profile: String) -> int:
	var values: Dictionary = PROFILES.get(profile, PROFILES[DEFAULT_PROFILE])
	return int(values.get("rtg_positioning_s", 0))


## D-051: hold = max(0, cycle - simulated motion), applied to both ends.
static func hold_from_cycle(cycle_s: Array, motion_s: int) -> Array:
	return [maxi(0, int(cycle_s[0]) - motion_s), maxi(0, int(cycle_s[1]) - motion_s)]


## Worst normal waits (whole seconds) per wait of TCL-011 §4 / §8(g):
## W1 QC waits for TT ~ one TT cycle, bounded by one RTG cycle (H2 frees the
## TT); W2 TT waits for QC = the rest of a QC hold; W3 TT waits for RTG = one
## RTG cycle (D-058: positioning + hold); W4 RTG waits for TT = the TT drive
## (D-052: the clock starts at ready-to-pick, which can be before the TT arrives).
static func worst_normal_waits_s(qc_hold: Array, rtg_hold: Array, speed_mps: float, rtg_positioning_s := 0) -> Dictionary:
	var drive_s = int(ceil(DEMO_MAX_TT_DRIVE_M / max(speed_mps, 0.1)))
	var rtg_cycle_s = rtg_positioning_s + int(rtg_hold[1])
	return {
		"W1": rtg_cycle_s,
		"W2": int(qc_hold[1]),
		"W3": rtg_cycle_s,
		"W4": drive_s,
	}


## Longest single motion/work phase without a progress result (whole seconds):
## a crane hold, the RTG positioning (D-058) or the TT drive.
static func longest_motion_or_work_s(qc_hold: Array, rtg_hold: Array, speed_mps: float, rtg_positioning_s := 0) -> int:
	var drive_s = int(ceil(DEMO_MAX_TT_DRIVE_M / max(speed_mps, 0.1)))
	return maxi(maxi(maxi(int(qc_hold[1]), int(rtg_hold[1])), drive_s), rtg_positioning_s)


## Margin violations as text ([] = ok). `timeouts` holds tt / qc / rtg no-show
## and leg_stall seconds. TCL-011 rev 2 (B1, N2): the stall clock only runs in
## motion/work states and is paused in every wait, so it must clear the longest
## single motion/work phase (a crane hold or the TT drive), not a no-show wait.
## D-060: with the no-show switch off the stall clock also runs in hand-off
## waits (it is the backstop), so it must clear the worst normal wait too.
static func margin_violations(qc_hold: Array, rtg_hold: Array, speed_mps: float, timeouts: Dictionary, no_show_enabled := true, rtg_positioning_s := 0) -> Array:
	var waits = worst_normal_waits_s(qc_hold, rtg_hold, speed_mps, rtg_positioning_s)
	var checks = []
	if no_show_enabled:
		checks = [
			["TtNoShowTimeoutS", int(timeouts.tt), "W1", waits.W1],
			["QcNoShowTimeoutS", int(timeouts.qc), "W2", waits.W2],
			["RtgNoShowTimeoutS", int(timeouts.rtg), "W3", waits.W3],
			["TtNoShowTimeoutS", int(timeouts.tt), "W4", waits.W4],
		]
	var stall_phase_s = longest_motion_or_work_s(qc_hold, rtg_hold, speed_mps, rtg_positioning_s)
	var stall_what = "longest motion/work phase"
	if not no_show_enabled:
		for key in ["W1", "W2", "W3", "W4"]:
			if int(waits[key]) > stall_phase_s:
				stall_phase_s = int(waits[key])
				stall_what = "worst wait %s (D-060 backstop)" % key
	checks.append(["LegStallTimeoutS", int(timeouts.leg_stall), stall_what, stall_phase_s])
	var out: Array = []
	for check in checks:
		if int(check[1]) < MARGIN_FACTOR * int(check[3]):
			out.append("%s=%s s < %s x %s (%s s)" % [check[0], check[1], MARGIN_FACTOR, check[2], check[3]])
	return out
