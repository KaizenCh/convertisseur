# addons/ue2godot/core/vfx_builder.gd
class_name VFXBuilder
extends RefCounted

static func build_vfx_markers(
	decal_map: Dictionary,
	manifest: Dictionary,
	root_node: Node3D,
	stats: Dictionary
) -> void:
	var niagara_list: Array = manifest.get("effects", {}).get("niagara", [])
	if niagara_list.size() == 0:
		return

	var vfx_root := Node3D.new()
	vfx_root.name = "VFX_MARKERS_NOT_CONVERTED"
	root_node.add_child(vfx_root)
	vfx_root.owner = root_node

	for item in niagara_list:
		var sys_name: String = item.get("system_name", item.get("name", "VFX"))
		var tf_data: Dictionary = item.get("transform", {})
		var tf := TransformConverter.transform_from_v10(tf_data)

		var marker := Marker3D.new()
		marker.name = sys_name
		marker.transform = tf

		vfx_root.add_child(marker)
		marker.owner = root_node
		stats["built_vfx_markers"] = stats.get("built_vfx_markers", 0) + 1
