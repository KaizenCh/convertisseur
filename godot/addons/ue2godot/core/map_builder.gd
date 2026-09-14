# addons/ue2godot/core/map_builder.gd
class_name MapBuilder
extends RefCounted

static func build(cfg: Dictionary) -> Dictionary:
	var manifest_path: String = cfg.get("manifest_path", "res://ue2godot_in/level_manifest.json")
	var asset_map_path: String = cfg.get("asset_map_path", "res://ue2godot_in/ue5_godot_asset_map.json")
	var decal_map_path: String = cfg.get("decal_map_path", "res://ue2godot_in/ue5_godot_decal_map.json")
	var output_scene_path: String = cfg.get("output_scene", "res://Maps/Map_REBUILT.tscn")

	var report := {
		"status": "OK",
		"built_nodes": 0,
		"errors": [],
		"warnings": [],
		"stats": {}
	}

	if not FileAccess.file_exists(manifest_path):
		report["status"] = "FAILED"
		(report["errors"] as Array).append("Manifest not found: " + manifest_path)
		return report

	var manifest_file := FileAccess.open(manifest_path, FileAccess.READ)
	var manifest_data: Dictionary = JSON.parse_string(manifest_file.get_as_text())

	var asset_map_data: Dictionary = {}
	if FileAccess.file_exists(asset_map_path):
		var asset_file := FileAccess.open(asset_map_path, FileAccess.READ)
		asset_map_data = JSON.parse_string(asset_file.get_as_text())

	var decal_map_data: Dictionary = {}
	if FileAccess.file_exists(decal_map_path):
		var decal_file := FileAccess.open(decal_map_path, FileAccess.READ)
		decal_map_data = JSON.parse_string(decal_file.get_as_text())

	var root_node := Node3D.new()
	root_node.name = "Map_REBUILT"

	var stats := {}
	var placements: Array = manifest_data.get("geometry", {}).get("placements", [])

	for p in placements:
		GeometryBuilder.build_geometry_placement(p, asset_map_data, root_node, stats)

	DecalBuilder.build_decals(decal_map_data, manifest_data, root_node, stats)
	VFXBuilder.build_vfx_markers(decal_map_data, manifest_data, root_node, stats)

	var packed_scene := PackedScene.new()
	var pack_result := packed_scene.pack(root_node)

	if pack_result == OK:
		var save_result := ResourceSaver.save(packed_scene, output_scene_path)
		if save_result == OK:
			report["status"] = "OK"
			report["output_scene"] = output_scene_path
		else:
			report["status"] = "FAILED"
			(report["errors"] as Array).append("Failed to save scene: " + str(save_result))
	else:
		report["status"] = "FAILED"
		(report["errors"] as Array).append("Failed to pack scene: " + str(pack_result))

	report["stats"] = stats
	root_node.free()
	return report
