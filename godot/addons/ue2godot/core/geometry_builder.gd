# addons/ue2godot/core/geometry_builder.gd
class_name GeometryBuilder
extends RefCounted

static func build_geometry_placement(
	placement: Dictionary,
	asset_map: Dictionary,
	root_node: Node3D,
	stats: Dictionary
) -> void:
	var mesh_data: Dictionary = placement.get("mesh", {})
	var ue_path: String = mesh_data.get("path", "")
	if ue_path == "":
		stats["skipped_slot_empty"] = stats.get("skipped_slot_empty", 0) + 1
		return

	var assets: Dictionary = asset_map.get("assets", {})
	if not assets.has(ue_path):
		stats["missing_asset_mapping"] = stats.get("missing_asset_mapping", 0) + 1
		return

	var asset_info: Dictionary = assets[ue_path]
	var godot_path: String = asset_info.get("godot_path", "")

	var glb_resource = load(godot_path)
	if glb_resource == null:
		stats["missing_glb"] = stats.get("missing_glb", 0) + 1
		return

	var tf_data: Dictionary = placement.get("reconstruction_transform", placement.get("final_world_transform", {}))
	var tf := TransformConverter.transform_from_v10(tf_data)

	var instance = glb_resource.instantiate()
	if instance == null:
		stats["instantiate_failure"] = stats.get("instantiate_failure", 0) + 1
		return

	if instance is Node3D:
		(instance as Node3D).transform = tf

	var actor_info: Dictionary = placement.get("actor", {})
	var actor_name: String = actor_info.get("name", "Mesh")
	instance.name = actor_name

	root_node.add_child(instance)
	instance.owner = root_node
	stats["built_placements"] = stats.get("built_placements", 0) + 1
