# ======================================================================
# AJOUT AU CONSTRUCTEUR — DÉCALS ET VFX
# ======================================================================
#
# À coller dans ton ue5_godot_map_constructor.gd existant.
# Rien à supprimer : ton correctif de quaternion et ta conversion d'axes
# restent intacts, ce bloc réutilise _transform_from_v10() telle quelle.
#
# ----------------------------------------------------------------------
# ÉTAPE 1 — en haut du fichier, à côté des autres const
# ----------------------------------------------------------------------

const DECAL_MAP_PATH := "res://ue5_godot_decal_map.json"
const BUILD_DECALS := true
const BUILD_VFX_MARKERS := true

# Unreal décrit un décal par une BOÎTE de projection dont l'échelle vaut
# [épaisseur, largeur, hauteur] et dont la taille de base est 256 unités.
# Godot utilise size = (largeur, hauteur, profondeur) en mètres, dans un
# ordre différent : d'où l'échange d'axes plus bas.
const UE_DECAL_BASE_SIZE := 256.0

# ----------------------------------------------------------------------
# ÉTAPE 2 — ajouter ces compteurs dans le dictionnaire `stats`
# ----------------------------------------------------------------------
#
#     "decals_built": 0,
#     "decals_missing_texture": 0,
#     "decals_transform_failed": 0,
#     "vfx_markers_built": 0,
#
# ----------------------------------------------------------------------
# ÉTAPE 3 — dans _run(), juste avant `_build_li_metadata_nodes(li_root)`
# ----------------------------------------------------------------------
#
#     if BUILD_DECALS:
#         _build_decals(root)
#     if BUILD_VFX_MARKERS:
#         _build_vfx_markers(root)
#
# ----------------------------------------------------------------------
# ÉTAPE 4 — dans _print_report(), ajouter
# ----------------------------------------------------------------------
#
#     print("Decals built                :", stats["decals_built"])
#     print("Decals without texture      :", stats["decals_missing_texture"])
#     print("Decal transform failures    :", stats["decals_transform_failed"])
#     print("VFX markers built           :", stats["vfx_markers_built"])
#
# ----------------------------------------------------------------------
# ÉTAPE 5 — coller ces fonctions à la fin du fichier
# ----------------------------------------------------------------------


func _build_decals(root: Node3D) -> void:
	var entries: Array = manifest.get("effects", {}).get("decals", [])

	if entries.is_empty():
		print("No decals in the manifest.")
		return

	if not FileAccess.file_exists(DECAL_MAP_PATH):
		push_warning("Decal map missing (%s) - decals skipped. Run the decal exporter and copy the file." % DECAL_MAP_PATH)
		return

	var parsed = JSON.parse_string(FileAccess.get_file_as_string(DECAL_MAP_PATH))
	if typeof(parsed) != TYPE_DICTIONARY:
		push_warning("Decal map is not a JSON object - decals skipped.")
		return

	var decal_map: Dictionary = parsed
	var materials: Dictionary = decal_map.get("decal_materials", {})

	if materials.is_empty():
		push_warning("Decal map has no materials - decals skipped.")
		return

	# One texture per material, loaded once and shared by every decal that
	# uses it, instead of 781 separate loads of the same file.
	var textures := {}
	var normals := {}

	for ue_material_path in materials.keys():
		var info: Dictionary = materials[ue_material_path]

		var albedo_path := str(info.get("godot_path", ""))
		if not albedo_path.is_empty() and ResourceLoader.exists(albedo_path):
			textures[ue_material_path] = load(albedo_path)
		else:
			push_warning("Decal texture missing: %s" % albedo_path)

		var normal_path := str(info.get("normal_godot_path", ""))
		if not normal_path.is_empty() and ResourceLoader.exists(normal_path):
			normals[ue_material_path] = load(normal_path)

		# The exporter flags a mask with no variation, because that decal
		# renders as a solid rectangle rather than a stain.
		if info.has("warning"):
			push_warning("Decal '%s': %s" % [str(info.get("name", "?")), str(info.get("warning", ""))])

	var decal_root := Node3D.new()
	decal_root.name = "Decals"
	root.add_child(decal_root)
	decal_root.owner = root

	for i in range(entries.size()):
		var entry: Dictionary = entries[i]

		var material_info: Dictionary = entry.get("material", {})
		var ue_material_path := str(material_info.get("path", ""))

		if not textures.has(ue_material_path):
			stats["decals_missing_texture"] += 1
			continue

		var transform_data = entry.get("reconstruction_transform", null)
		if typeof(transform_data) != TYPE_DICTIONARY:
			transform_data = entry.get("final_world_transform", null)
		if typeof(transform_data) != TYPE_DICTIONARY:
			transform_data = entry.get("transform", null)
		if typeof(transform_data) != TYPE_DICTIONARY:
			stats["decals_transform_failed"] += 1
			continue

		# Reuses the script's own conversion, so decals follow the exact
		# same axis convention as every mesh - including the quaternion fix.
		var transform = _transform_from_v10(transform_data)
		if transform == null:
			stats["decals_transform_failed"] += 1
			continue

		var decal := Decal.new()
		decal.name = "Decal_%04d_%s" % [i, _safe_node_name(str(material_info.get("name", "decal")))]
		decal.texture_albedo = textures[ue_material_path]

		if normals.has(ue_material_path):
			decal.texture_normal = normals[ue_material_path]

		# Unreal's decal box: scale is [thickness, width, height] on a
		# 256-unit cube, and the box projects along its own X axis.
		# Godot's Decal projects along -Y with size = (width, height, depth).
		# _transform_from_v10 already rotated the node, so only the size
		# components have to be reordered here.
		var scale_array = transform_data.get("scale", [1.0, 1.0, 1.0])
		var thickness := float(scale_array[0]) * UE_DECAL_BASE_SIZE * UE_CM_TO_GODOT_M
		var width := float(scale_array[1]) * UE_DECAL_BASE_SIZE * UE_CM_TO_GODOT_M
		var height := float(scale_array[2]) * UE_DECAL_BASE_SIZE * UE_CM_TO_GODOT_M

		decal.size = Vector3(width, thickness, height)

		# The node must not inherit the scale twice: it is already baked
		# into decal.size above.
		decal.transform = Transform3D(
			transform.basis.orthonormalized(),
			transform.origin
		)

		decal_root.add_child(decal)
		decal.owner = root

		if KEEP_PLACEMENT_METADATA:
			decal.set_meta("ue_actor_path", str((entry.get("actor", {}) as Dictionary).get("path", "")))
			decal.set_meta("ue_component_path", str((entry.get("component", {}) as Dictionary).get("path", "")))
			decal.set_meta("ue_material_path", ue_material_path)
			decal.set_meta("ue_decal_scale", scale_array)

		stats["decals_built"] += 1

	print("Decals built                :", stats["decals_built"], "/", entries.size())


func _build_vfx_markers(root: Node3D) -> void:
	var entries: Array = manifest.get("effects", {}).get("niagara", [])

	if entries.is_empty():
		return

	# Niagara systems are NOT converted: their behaviour lives in
	# engine-specific modules with no Godot equivalent. What is preserved
	# is exactly where each one was, so the effects can be rebuilt in
	# Godot without having to re-place 422 of them by hand.
	var vfx_root := Node3D.new()
	vfx_root.name = "VFX_MARKERS_NOT_CONVERTED"
	root.add_child(vfx_root)
	vfx_root.owner = root

	# Group by system so the 341 candle flames can be selected in one go.
	var groups := {}

	for i in range(entries.size()):
		var entry: Dictionary = entries[i]
		var system: Dictionary = entry.get("system", {})
		var system_name := str(system.get("name", "unknown"))

		var transform_data = entry.get("reconstruction_transform", null)
		if typeof(transform_data) != TYPE_DICTIONARY:
			transform_data = entry.get("final_world_transform", null)
		if typeof(transform_data) != TYPE_DICTIONARY:
			transform_data = entry.get("transform", null)
		if typeof(transform_data) != TYPE_DICTIONARY:
			continue

		var transform = _transform_from_v10(transform_data)
		if transform == null:
			continue

		if not groups.has(system_name):
			var group := Node3D.new()
			group.name = _safe_node_name(system_name)
			vfx_root.add_child(group)
			group.owner = root
			groups[system_name] = group

		var marker := Marker3D.new()
		marker.name = "%s_%04d" % [_safe_node_name(system_name), i]
		marker.transform = transform

		(groups[system_name] as Node3D).add_child(marker)
		marker.owner = root

		marker.set_meta("ue_niagara_system", str(system.get("path", "")))
		marker.set_meta("ue_niagara_system_name", system_name)
		marker.set_meta("ue_actor_path", str((entry.get("actor", {}) as Dictionary).get("path", "")))
		marker.set_meta("ue_not_converted", true)

		stats["vfx_markers_built"] += 1

	print("VFX markers built           :", stats["vfx_markers_built"], "(placement only - not converted)")
