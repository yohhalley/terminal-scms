extends RefCounted


static func project(points: Array, point: Vector3) -> Dictionary:
	var best := {"distance": INF, "chainage": 0.0, "point": point, "heading": Vector3.FORWARD}
	var total := 0.0
	for index in range(points.size() - 1):
		var start: Vector3 = points[index]
		var segment: Vector3 = points[index + 1] - start
		segment.y = 0.0
		var length := segment.length()
		if length <= 0.000001:
			continue
		var offset := (point - start) * Vector3(1, 0, 1)
		var ratio := clampf(offset.dot(segment) / segment.length_squared(), 0.0, 1.0)
		var closest := start + segment * ratio
		closest.y = point.y
		var distance := closest.distance_to(point)
		if distance < float(best.distance):
			best = {"distance": distance, "chainage": total + length * ratio, "point": closest, "heading": segment / length}
		total += length
	best["length"] = total
	return best


static func rail_at(rails: Array, target: Vector3, units: float) -> Dictionary:
	var best: Dictionary = {}
	var distance := INF
	for rail in rails:
		var projection := project(rail.points, target)
		var polygon: PackedVector2Array = rail.get("polygon", PackedVector2Array())
		var inside := polygon.size() >= 3 and Geometry2D.is_point_in_polygon(Vector2(target.x, target.z), polygon)
		if (inside or float(projection.distance) <= float(rail.get("entry_tolerance_m", 0.5)) * units) and float(projection.distance) < distance:
			best = rail
			distance = float(projection.distance)
	return best


static func along(rail: Dictionary, from: Vector3, to: Vector3) -> Array:
	var first := project(rail.points, from)
	var last := project(rail.points, to)
	var low: float = min(first.chainage, last.chainage)
	var high: float = max(first.chainage, last.chainage)
	var positions: Array = []
	var chainage := 0.0
	for index in range(rail.points.size() - 1):
		chainage += (rail.points[index + 1] - rail.points[index]).length()
		if chainage > low + 0.00001 and chainage < high - 0.00001:
			positions.append(rail.points[index + 1])
	var reverse := float(last.chainage) < float(first.chainage)
	if reverse:
		positions.reverse()
	positions.append(last.point)
	var previous: Vector3 = first.point
	var steps: Array = []
	for position in positions:
		var heading: Vector3 = (position - previous) * Vector3(1, 0, 1)
		if reverse:
			heading = -heading
		if heading.length_squared() <= 0.000001:
			heading = last.heading
		steps.append({"position": position, "heading": heading.normalized(), "axis_id": rail.axis_id})
		previous = position
	return steps


static func plan(rails: Array, mode: String, active_axis_id: int, from: Vector3, target: Vector3, units: float) -> Dictionary:
	var active: Dictionary = {}
	for rail in rails:
		if int(rail.axis_id) == active_axis_id:
			active = rail
			break
	if mode == "fixed" and active.is_empty():
		return {"accepted": false}
	var destination := rail_at(rails, target, units)
	if not active.is_empty() and (mode == "fixed" or not bool(active.get("can_exit", false)) or endpoints(active).is_empty()):
		destination = active
	if not active.is_empty() and not destination.is_empty() and active.axis_id == destination.axis_id:
		var projected := project(active.points, target)
		return {"accepted": true, "target": projected.point, "steps": along(active, from, projected.point)}
	if not destination.is_empty() and (endpoints(destination).is_empty() or not bool(destination.get("can_exit", false))):
		return {"accepted": false}
	var steps: Array = []
	var entry := target
	var exit := from
	var best := INF
	var exits: Array = [from] if active.is_empty() else endpoints(active)
	var entries: Array = [target] if destination.is_empty() else endpoints(destination)
	for exit_point in exits:
		for entry_point in entries:
			var cost: float = exit_point.distance_to(entry_point)
			if not active.is_empty():
				cost += absf(float(project(active.points, from).chainage) - float(project(active.points, exit_point).chainage))
			if not destination.is_empty():
				cost += absf(float(project(destination.points, target).chainage) - float(project(destination.points, entry_point).chainage))
			if cost < best:
				best = cost
				exit = exit_point
				entry = entry_point
	if not active.is_empty():
		steps.append_array(along(active, from, exit))
	steps.append({"position": entry, "axis_id": 0})
	var final_target := target
	if not destination.is_empty():
		final_target = project(destination.points, target).point
		steps.append_array(along(destination, entry, final_target))
	return {"accepted": true, "target": final_target, "steps": steps}


static func endpoints(rail: Dictionary) -> Array:
	var points: Array = []
	if bool(rail.get("entry_at_start", true)):
		points.append(rail.points[0])
	if bool(rail.get("entry_at_end", true)):
		points.append(rail.points[-1])
	return points
