extends RefCounted

## Single place that maps CHE records to Sparkplug device ids and to the stable
## keys used for seeded draws. The contract may move from `CHE_{MiniTOS id}` to
## fixed equipment codes (e.g. QC01/TT01); a CHE record that carries an explicit
## `device_id` already uses it, and everything else falls back to `CHE_{id}`.

const LEGACY_DEVICE_PREFIX := "CHE_"


static func device_id(che: Dictionary) -> String:
	var explicit_id = str(che.get("device_id", "")).strip_edges()
	if not explicit_id.is_empty():
		return explicit_id
	return LEGACY_DEVICE_PREFIX + str(che.get("pk", che.get("id", che.get("name", "UNKNOWN"))))


static func device_id_for(che_id: int, ches: Dictionary) -> String:
	if ches.has(che_id) and ches[che_id] is Dictionary:
		return device_id(ches[che_id])
	return device_id({"pk": che_id})


## Key used to derive per-CHE random draws. It follows the device id so that a
## switch to fixed equipment codes also makes the draws independent of DB ids.
static func seed_key(che: Dictionary) -> String:
	return device_id(che)
