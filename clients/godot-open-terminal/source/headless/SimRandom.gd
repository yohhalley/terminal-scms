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
func draw_wait_s(
	che_key: String, command_key: String, wait_index: int, min_s: int, max_s: int
) -> int:
	var low = mini(min_s, max_s)
	var high = maxi(min_s, max_s)
	_rng.seed = derive_seed(["wait", che_key, command_key, wait_index])
	return _rng.randi_range(low, high)


## Contract §5.2 (B5) namespace for result ids. Fixed value, derived once as
## uuid5(NAMESPACE_URL, "urn:scms:contract:tcl-002:result-id"); never change it,
## or replays of an existing session would get new ids.
const NS_TCL002 := "5db85d9e-b37c-5c1a-9501-e4cbb091c5b9"


## Result/Id = uuid5(NS_TCL002, "{Session/Id}:{Sim/EventSeq}") (contract §5.2).
## A replay of the same result keeps its EventSeq and so its id; a new run has a
## new Session/Id, so reruns never collide.
static func result_id(session_id: String, event_seq: int) -> String:
	return uuid5(NS_TCL002, "%s:%d" % [session_id, event_seq])


## RFC 4122 name-based UUID, version 5 (SHA-1).
static func uuid5(namespace_uuid: String, name: String) -> String:
	var data: PackedByteArray = namespace_uuid.replace("-", "").hex_decode()
	data.append_array(name.to_utf8_buffer())
	var context = HashingContext.new()
	context.start(HashingContext.HASH_SHA1)
	context.update(data)
	var digest: PackedByteArray = context.finish().slice(0, 16)
	digest[6] = (digest[6] & 0x0f) | 0x50
	digest[8] = (digest[8] & 0x3f) | 0x80
	var hex = digest.hex_encode()
	return "%s-%s-%s-%s-%s" % [hex.substr(0, 8), hex.substr(8, 4), hex.substr(12, 4), hex.substr(16, 4), hex.substr(20, 12)]
