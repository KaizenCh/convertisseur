# addons/ue2godot/core/decal_builder.gd
class_name DecalBuilder
extends RefCounted

static func build_decals(
	decal_map: Dictionary,
	manifest: Dictionary,
	root_node: Node3D,
	stats: Dictionary
) -> void:
	var decals_list: Array = manifest.get("effects", {}).get("decals", [])
	if decals_list.size() == 0:
		return

	var decal_materials: Dictionary = decal_map.get("decal_materials", {})

	for placement in decals_list:
		var mat_path: String = placement.get("material_path", "")
		var tf_data: Dictionary = placement.get("transform", {})
		var tf := TransformConverter.transform_from_v10(tf_data)

		var decal_node := Decal.new()
		decal_node.transform = tf.orthonormalized()

		var size_data: Array = placement.get("size", [256.0, 256.0, 256.0])
		if size_data.size() >= 3:
			var w: float = float(size_data[1]) * TransformConverter.UE_CM_TO_GODOT_M
			var h: float = float(size_data[2]) * TransformConverter.UE_CM_TO_GODOT_M
			var th: float = float(size_data[0]) * TransformConverter.UE_CM_TO_GODOT_M
			decal_node.size = Vector3(w, th, h)

		if decal_materials.has(mat_path):
			var mat_info: Dictionary = decal_materials[mat_path]
			var tex_path: String = mat_info.get("godot_path", "")
			if tex_path != "":
				var tex = load(tex_path)
				if tex != null and tex is Texture2D:
					decal_node.texture_albedo = tex

		root_node.add_child(decal_node)
		decal_node.owner = root_node
		stats["built_decals"] = stats.get("built_decals", 0) + 1
