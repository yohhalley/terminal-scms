extends Node

const BlockScript = preload("res://source/match/yard/Block.gd")
const PileScript = preload("res://source/match/yard/Pile.gd")
const ContainerUnit = preload("res://source/match/units/ContainerUnit.tscn")

const Unit = preload("res://source/match/units/Unit.gd")
const Structure = preload("res://source/match/units/Structure.gd")

# Define physical dimensions for a container.
const CONTAINER_WIDTH = 0.25
const CONTAINER_LENGTH = 1.15
const CONTAINER_HEIGHT = 0.3

# Define default physical dimensions for a container pile/slot.
# These can be adjusted to match the scale of your 3D models.
const DEFAULT_PILE_WIDTH = 0.4  # meters
const DEFAULT_PILE_LENGTH = 1.2 # meterse

var _pile_world_positions = {}

# This function will be called from your main scene script at startup.
func build_yard(yard_data: Array, parent_node: Node):
	if not yard_data is Array:
		printerr("Yard data must be an array of blocks.")
		return

	print("YardManager: Starting to build yard...")
	
	# Create reusable material for the pile outlines
	var pile_material = StandardMaterial3D.new()
	pile_material.albedo_color = Color.CYAN
	pile_material.shading_mode = StandardMaterial3D.SHADING_MODE_UNSHADED

	for block_data in yard_data:
		# --- Create the Block Node Programmatically ---
		var block_instance = Node3D.new()
		block_instance.set_script(BlockScript)
		block_instance.name = block_data.get("name", "Unnamed_Block")
		block_instance.block_name = block_instance.name
		
		# Position the block node according to its data. Note y -> z mapping.
		var block_pos_x = block_data.get("x_position", 0.0)
		var block_pos_z = block_data.get("y_position", 0.0) # JSON 'y' is our 'z'
		block_instance.global_position = Vector3(block_pos_x, 0.1, block_pos_z) # Slightly above ground

		parent_node.add_child(block_instance)

		# --- Create the Pile Nodes Programmatically ---
		var piles = block_data.get("piles", [])
		if not piles:
			print("YardManager: Block '", block_instance.name, "' has no piles.")
			continue
		
		print("YardManager: Found ", piles.size(), " piles in block '", block_instance.name, "'.")
			
		for pile_data in piles:
			var pile_instance = Area3D.new()
			pile_instance.set_script(PileScript)
			
			# Use '__str__' as the unique logical name for the pile
			pile_instance.logical_name = pile_data.get("__str__", "Unnamed_Pile")
			pile_instance.name = pile_instance.logical_name

			# Position the pile relative to the world, not the block, as per the new data.
			var pile_pos_x = pile_data.get("x_position", 0.0)
			var pile_pos_z = pile_data.get("y_position", 0.0) # JSON 'y' is our 'z'
			pile_instance.global_position = Vector3(pile_pos_x, 0.2, pile_pos_z) # Raise slightly to prevent Z-fighting
			
			_pile_world_positions[pile_instance.logical_name] = pile_instance.global_position
			
			# print("  - Creating pile '", pile_instance.name, "' at world position: ", pile_instance.global_position)
			
			# Define the size for the collision shape and visual outline.
			# The X-axis (width) is now the longer side to align with bay orientation.
			var pile_size = {"x": DEFAULT_PILE_LENGTH, "z": DEFAULT_PILE_WIDTH}
			
			# Create collision shape
			var collision_shape = CollisionShape3D.new()
			collision_shape.shape = BoxShape3D.new()
			collision_shape.shape.size = Vector3(pile_size["x"], 1, pile_size["z"])
			pile_instance.add_child(collision_shape)

			# Create visual outline
			pile_instance.add_child(_create_outline_mesh("PileOutline", pile_size, pile_material))
			
			parent_node.add_child(pile_instance) # Add pile directly to the yard container
			
			# Register the pile with the visibility manager for LOD handling.
			VisibilityManager.register_pile(pile_instance)

func build_container_inventory(container_visits: Array, parent_node: Node):
	if not container_visits is Array:
		printerr("Container visit data must be an array.")
		return
	
	print("YardManager: Building container inventory...")

	var human_player = get_parent()._get_human_player()

	for visit_data in container_visits:
		# 1. Instantiate the ContainerUnit scene
		var container_unit_instance = ContainerUnit.instantiate()
		# 2. Initialize with data
		container_unit_instance.initialize_from_data(visit_data)
		# Add container to human player's node
		human_player.add_child(container_unit_instance)
		# 3. Set name (optional, for clarity)
		container_unit_instance.name = visit_data.get("container_number_name", "UnknownContainer")
		# 4. Set position (if not handled in initialize_from_data)
		if "x_position" in visit_data and "y_position" in visit_data:
			container_unit_instance.global_position = Vector3(
				visit_data.x_position,
				0,  # or visit_data.get("z_position", 0) if you want to use z
				visit_data.y_position
			)
		# 5. Add to the parent node
		parent_node.add_child(container_unit_instance)
		print("  - Created container '", container_unit_instance.name, "' at position ", container_unit_instance.global_position)


func get_pile_world_position(logical_name: String) -> Vector3:
	return _pile_world_positions.get(logical_name, Vector3.ZERO)

# Helper function to create a 2D line mesh on the ground
func _create_outline_mesh(node_name: String, size: Dictionary, material: Material) -> MeshInstance3D:
	var mesh_instance = MeshInstance3D.new()
	mesh_instance.name = node_name
	
	var immediate_mesh = ImmediateMesh.new()
	var mesh_size_x = size.get("x", 1.0)
	var mesh_size_z = size.get("z", 1.0)

	# Define the 4 corners of the rectangle, centered around the node's origin
	var half_x = mesh_size_x / 2.0
	var half_z = mesh_size_z / 2.0
	var corner1 = Vector3(-half_x, 0, -half_z)
	var corner2 = Vector3(half_x, 0, -half_z)
	var corner3 = Vector3(half_x, 0, half_z)
	var corner4 = Vector3(-half_x, 0, half_z)

	# Begin drawing lines using a line strip
	immediate_mesh.surface_begin(Mesh.PRIMITIVE_LINE_STRIP)
	immediate_mesh.surface_add_vertex(corner1)
	immediate_mesh.surface_add_vertex(corner2)
	immediate_mesh.surface_add_vertex(corner3)
	immediate_mesh.surface_add_vertex(corner4)
	immediate_mesh.surface_add_vertex(corner1) # Add the first vertex again to close the loop
	immediate_mesh.surface_end()

	mesh_instance.mesh = immediate_mesh
	mesh_instance.material_override = material
	
	return mesh_instance

