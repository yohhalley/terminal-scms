extends Node

const ContainerScript = preload("res://source/match/units/ContainerUnit.gd")

# Define physical dimensions for a container.
# Sightly smaller than a pile to fit inside the outline.
const CONTAINER_WIDTH = 0.25
const CONTAINER_LENGTH = 1.15
const CONTAINER_HEIGHT = 0.3

# This function will be called from Match.gd
func build_container_inventory(container_visits: Array, parent_node: Node):
	if not container_visits is Array:
		printerr("Container visit data must be an array.")
		return
	
	print("ContainerVisitManager: Building container inventory...")
	
	var container_material = StandardMaterial3D.new()
	container_material.albedo_color = Color.ORANGE_RED

	for visit_data in container_visits:
		var logical_position = visit_data.get("position")
		var tier = visit_data.get("tier", 1) # Default to tier 1 if not specified
		
		if not logical_position:
			printerr("Container visit is missing 'position' data.")
			continue
			
			
		# Get the ground-level world coordinates from the YardManager's map.
		var base_position = YardManager.get_pile_world_position(logical_position)
		
		if base_position == Vector3.ZERO:
			printerr("Could not find world position for pile: ", logical_position)
			continue

		var container_instance = Node3D.new()
		container_instance.set_script(ContainerScript)
		container_instance.name = visit_data.get("container", "UnknownContainer")
		container_instance.container_id = container_instance.name
		container_instance.visit_id = visit_data.get("id", "")
		container_instance.logical_position = logical_position
		
		# Create the visual 3D box for the container
		var mesh_instance = MeshInstance3D.new()
		var box_mesh = BoxMesh.new()
		# Flip the dimensions so the longer side (length) is on the X-axis.
		box_mesh.size = Vector3(CONTAINER_LENGTH, CONTAINER_HEIGHT, CONTAINER_WIDTH)
		mesh_instance.mesh = box_mesh
		mesh_instance.material_override = container_material
		container_instance.add_child(mesh_instance)
		
		# Stack the container based on its tier level.
		# The +0.5 is because a BoxMesh's origin is in its center.
		var vertical_offset = (tier - 1) * CONTAINER_HEIGHT + (CONTAINER_HEIGHT * 0.5)
		container_instance.global_position = base_position + Vector3(0, vertical_offset, 0)
		
		parent_node.add_child(container_instance)
		print("  - Created container '", container_instance.name, "' at logical position ", logical_position, ", Tier ", tier) 
