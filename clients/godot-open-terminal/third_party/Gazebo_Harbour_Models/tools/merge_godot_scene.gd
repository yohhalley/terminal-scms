extends SceneTree


func _initialize():
	var args = OS.get_cmdline_user_args()
	if args.size() != 2:
		printerr("Usage: merge_godot_scene.gd <source scene> <destination mesh>")
		quit(2)
		return
	var scene = load(args[0]) as PackedScene
	if scene == null:
		printerr("Could not load %s" % args[0])
		quit(1)
		return
	var instance = scene.instantiate() as Node3D
	var surfaces: Dictionary = {}
	_collect_surfaces(instance, Transform3D.IDENTITY, surfaces)
	var merged = ArrayMesh.new()
	for surface_tool in surfaces.values():
		surface_tool.commit(merged)
	var error = ResourceSaver.save(merged, args[1])
	print("Saved %s with %s material surfaces" % [args[1], merged.get_surface_count()])
	instance.free()
	quit(0 if error == OK else 1)


func _collect_surfaces(node: Node3D, relative_transform: Transform3D, surfaces: Dictionary):
	if node is MeshInstance3D and node.mesh != null:
		for surface_index in range(node.mesh.get_surface_count()):
			var material = node.mesh.surface_get_material(surface_index)
			var key = material.get_instance_id() if material != null else 0
			var surface_tool = surfaces.get(key) as SurfaceTool
			if surface_tool == null:
				surface_tool = SurfaceTool.new()
				surface_tool.begin(Mesh.PRIMITIVE_TRIANGLES)
				surface_tool.set_material(material)
				surfaces[key] = surface_tool
			surface_tool.append_from(node.mesh, surface_index, relative_transform)
	for child in node.get_children():
		if child is Node3D:
			_collect_surfaces(child, relative_transform * child.transform, surfaces)
