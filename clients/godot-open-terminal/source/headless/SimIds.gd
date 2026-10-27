extends RefCounted

## Single place that maps CHE records to Sparkplug device ids and to the stable
## keys used for seeded draws.
##
## Agreed contract: the device id is the MiniTOS Che.name (e.g. QC01), the
## default (SIMULATOR_DEVICE_ID_MODE=name). OES binds whatever id DBIRTH
## registers. `use_che_name = false` (SIMULATOR_DEVICE_ID_MODE=legacy) selects
## the old `CHE_{MiniTOS id}` form. An explicit `device_id` always wins.

const LEGACY_DEVICE_PREFIX := "CHE_"

static var use_che_name := true


static func device_id(che: Dictionary) -> String:
	var explicit_id = str(che.get("device_id", "")).strip_edges()
	if not explicit_id.is_empty():
		return explicit_id
	if use_che_name:
		var name_id = sanitize(str(che.get("name", "")))
		if not name_id.is_empty():
			return name_id
	return LEGACY_DEVICE_PREFIX + str(che.get("pk", che.get("id", che.get("name", "UNKNOWN"))))


static func device_id_for(che_id: int, ches: Dictionary) -> String:
	if ches.has(che_id) and ches[che_id] is Dictionary:
		return device_id(ches[che_id])
	return device_id({"pk": che_id})


## Sparkplug ids must not contain '/', '+' or '#'. Q11 forbids silent
## normalisation: when the name has to change, a warning names both forms (once
## per name) so the MiniTOS Che.name can be fixed. The replacement stays so the
## topic is still valid.
static func sanitize(raw: String) -> String:
	var clean = raw.strip_edges().replace("/", "_").replace("+", "_").replace("#", "_")
	if clean != raw:
		_warn_once(raw, "Device name '%s' is not a valid Sparkplug id; publishing as '%s' (fix Che.name, contract Q11)" % [raw, clean])
	return clean


## Warnings raised so far (also pushed to the Godot log). Tests read and clear it.
static var warnings: Array = []
static var _warned := {}


static func _warn_once(key: String, message: String):
	if _warned.has(key):
		return
	_warned[key] = true
	warnings.append(message)
	push_warning(message)


## Key used to derive per-CHE random draws. It follows the device id so that a
## switch to fixed equipment codes also makes the draws independent of DB ids.
static func seed_key(che: Dictionary) -> String:
	return device_id(che)
