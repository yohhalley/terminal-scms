extends RefCounted

## Seeded randomness for the headless simulator.
##
## Every draw re-seeds one RandomNumberGenerator from a hash of the run seed and
## the draw's own key (CHE key, leg/command key, wait index). A value therefore
## does not depend on how many other draws happened before it, on frame timing
## or on wall-clock time. The same seed and keys always give the same values.

const DEFAULT_SEED := 1

var run_seed: int = DEFAULT_SEED
var run_id := ""
var _rng := RandomNumberGenerator.new()


func _init(seed_value: int = DEFAULT_SEED, run_identity: String = ""):
	run_seed = seed_value
	run_id = run_identity


## Parses a seed from text (env/bootstrap). Non-integers fall back to `fallback`.
static func parse_seed(text: String, fallback: int = DEFAULT_SEED) -> int:
	var trimmed = text.strip_edges()
	if trimmed.is_empty() or not trimmed.is_valid_int():
		return fallback
	return int(trimmed)


## 32-bit FNV-1a over UTF-8 bytes. Implemented here (not `hash()`) so values stay
## stable across Godot versions and platforms.
static func fnv1a32(text: String) -> int:
	var value := 0x811c9dc5
	for byte in text.to_utf8_buffer():
		value = ((value ^ byte) * 0x01000193) & 0xffffffff
	return value


func _key(parts: Array) -> String:
	var text = str(run_seed)
	for part in parts:
		text += "|" + str(part)
	return text


func derive_seed(parts: Array) -> int:
	var key = _key(parts)
	return (fnv1a32("hi|" + key) << 32) | fnv1a32("lo|" + key)


## Whole seconds, uniform over [min_s, max_s] inclusive.
func draw_wait_s(che_key: String, command_key: String, wait_index: int, min_s: int, max_s: int) -> int:
	var low = mini(min_s, max_s)
	var high = maxi(min_s, max_s)
	_rng.seed = derive_seed(["wait", che_key, command_key, wait_index])
	return _rng.randi_range(low, high)


## UUID-shaped (version 8, RFC 4122 variant) id for a command result. It includes
## the run id so reruns of the same session/command never reuse a result id.
func result_id(session_id: String, command_id: String, event: String, event_seq: int) -> String:
	var key = _key(["result", run_id, session_id, command_id, event, event_seq])
	var a = fnv1a32("0|" + key)
	var b = fnv1a32("1|" + key)
	var c = fnv1a32("2|" + key)
	var d = fnv1a32("3|" + key)
	return "%08x-%04x-8%03x-%04x-%04x%08x" % [
		a, (b >> 16) & 0xffff, b & 0xfff, ((c >> 16) & 0x3fff) | 0x8000, c & 0xffff, d
	]
