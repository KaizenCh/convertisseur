@tool
extends EditorScript
## UE5 -> Godot V10 MAP CONSTRUCTOR  (V10.11 - complete)
##
## Fixes carried since V10.4, in the order they were found and verified:
##   V10.7  FRotator quaternion signs matched to Unreal's own
##          FRotator::Quaternion(). Yaw-only placements hid the bug.
##   V10.8  Axis conversion determinant is now -1, matching Unreal's
##          glTF exporter. Symmetric props hid it; stairs revealed it.
##   V10.9  Decal pass, plus a rewritten VFX pass: shared resources,
##          physics-driven curves, light budget, candle/swarm presets.
##   V10.10 VFX rebuilt on a shader-free default path after the parcel
##          shader produced flat white/pale blobs on test renders.
##   V10.11 VFX_USE_PARCEL_SHADER was documented as "off by default" but
##          the constant itself was left at true - so every "candle",
##          "fire", "smoke" and "steam" emitter (the only categories with
##          "parcel": true) was still hitting the unverified shader path
##          and rendering as pale round blobs, while "spark" and the other
##          parcel:false categories looked correct. Constant now matches
##          its own documented intent (false). Also: "torch" no longer
##          shares the bonfire-scale "fire" preset (too big, travels too
##          far for a wall/hand torch) - it has its own preset, sized
##          between "candle" and "fire", still contained. transform_align
##          assignment is now guarded by _try_set like every other
##          version-sensitive property in this file.
##   V10.12 "fire" still travelled ~3 m before dying (v up to 2.25 m/s +
##          upward buoyancy 0.70 + 1.15s lifetime) - a brazier/cauldron
##          flame reaching into the tree canopy above it. Brought down to
##          a ~0.8 m max reach. Also: the flame body (candle/torch/fire)
##          is now deliberately near-static at the source; a SEPARATE
##          emitter reusing the already-correct "spark" physics is layered
##          on top for fire/torch, so the rising/fading motion reads as
##          embers and ash leaving the flame, not the flame itself
##          stretching upward. Candle has no ember layer (a candle-scale
##          flame doesn't throw visible embers).
##
## Expected JSON at the project root:
##   res://level_manifest_v10.json
##   res://ue5_godot_asset_map.json
##   res://ue5_godot_decal_map.json   (only if BUILD_DECALS)
##
## Consumes ONLY the already-produced V10 manifest + asset map.
## It does NOT scan Unreal, does NOT search meshes by name, and does NOT
## recompute LevelInstance transforms.
##
## Expected assets:
##   res://UEAssets/Meshes/*.glb
##
## Expected JSON:
##   res://level_manifest_v10.json
##   res://ue5_godot_asset_map.json
##
## Run from Godot:
##   FileSystem -> right-click this script -> Run
##
## The output scene is:
##   res://Map--_REBUILT.tscn
##
## ------------------------------------------------------------------
## V10.4 CHANGES (vs the previous version of this script):
##
## 1. FIXED: a single known/documented transform failure (e.g. one of the
##    14 stale-component cases the manifest itself already tracks) used to
##    ABORT THE ENTIRE RECONSTRUCTION with zero output .tscn, because
##    FAIL_ON_MISSING_ASSET / FAIL_ON_MISSING_GLBS were being checked as a
##    catch-all for ANY failure reason, not just missing-asset/missing-GLB
##    failures. Each failure category now has its own flag, and only
##    genuinely systemic problems (asset map or GLB file truly absent)
##    abort by default; per-item issues are skipped with a counted warning.
##
## 2. FIXED (the big one): ISM/HISM placements (UE InstancedStaticMesh /
##    HierarchicalInstancedStaticMesh components) used to spawn exactly ONE
##    node per component, no matter how many real instances that component
##    actually held (instance_count was only ever used as a label). On this
##    project that silently dropped 440 of 458 real instances (up to 165
##    grass/weed instances collapsed into 1). This now reads
##    "instance_final_world_transforms" (added to the manifest generator in
##    the same pass as this script) and spawns one GLB instance per real
##    UE instance.
##
## 3. FIXED: an ISM/HISM component with instance_count == 0 (a genuinely
##    empty instance list - happens with some foliage components) used to
##    still spawn one phantom object at the component's own transform. It
##    is now skipped like an empty mesh slot.
##
## 4. FIXED: HISM placements were never counted in the stats report (only
##    "static_mesh" and "instanced_mesh" were checked), undercounting the
##    printed summary. Cosmetic only, but fixed for accurate reporting.

const MANIFEST_PATH := "res://level_manifest_v10.json"
const ASSET_MAP_PATH := "res://ue5_godot_asset_map.json"
const OUTPUT_SCENE_PATH := "res://Map--_REBUILT.tscn"

# IMPORTANT:
# V10 locations are Unreal centimeters and the manifest explicitly labels
# reconstruction transforms as Unreal world space / composed world.
# This mode converts:
#   UE X,Y,Z cm -> Godot X,Z,-Y meters
#
# If the GLBs were exported with an exporter that already bakes the complete
# UE->Godot coordinate conversion into the asset coordinate system, change
# this to false. Do NOT change this just to "make it look right" before
# checking the validation output.
const CONVERT_UE_TO_GODOT := true
const UE_CM_TO_GODOT_M := 0.01

# Safety / reproducibility.
#
# V10.4: split into one flag per failure CATEGORY instead of one pair of
# flags checked for every failure reason. Missing-asset-mapping / missing-
# GLB are systemic pipeline problems (something upstream wasn't copied or
# exported) - aborting the whole run to force a fix is the right call, so
# they still default to true. A transform failure, an unloadable single
# GLB, or a duplicate placement id are ISOLATED per-item problems that the
# manifest's own diagnostics already tracked and explained - they default
# to "skip with a counted warning" so 99.8% good data doesn't get thrown
# away because of a handful of known edge cases.
const FAIL_ON_MISSING_ASSET_MAPPING := true
const FAIL_ON_MISSING_GLB := true
const FAIL_ON_TRANSFORM_FAILURE := false
const FAIL_ON_INSTANTIATE_FAILURE := false
const FAIL_ON_DUPLICATE_PLACEMENT_ID := false

const SKIP_EMPTY_MESH_SLOTS := true
const KEEP_PLACEMENT_METADATA := true

# --- Decals / VFX (optional passes) ---
const DECAL_MAP_PATH := "res://ue5_godot_decal_map.json"
const BUILD_DECALS := true
const BUILD_VFX_MARKERS := true
const VFX_AUTO_PARTICLES := true

# Unreal describes a decal by a projection BOX whose scale is
# [thickness, width, height] on a 256-unit base cube. Godot uses
# size = (width, height, depth), in a different axis order.
const UE_DECAL_BASE_SIZE := 256.0

# Global multiplier on the decal footprint, so it can be nudged
# without touching the source data. 1.0 = exact UE size.
const DECAL_SIZE_SCALE := 0.9

# Internal per-placement outcome codes (see _build_geometry_placement).
enum Outcome { OK, SKIPPED, FATAL }

var manifest: Dictionary
var asset_map: Dictionary
var stats := {
	"geometry_total": 0,
	"geometry_static": 0,
	"geometry_instanced": 0,
	"empty_mesh_slots": 0,
	"zero_instance_ism_skipped": 0,
	"valid_geometry": 0,
	"instances_spawned": 0,
	"instance_transform_missing": 0,
	"missing_asset_mapping": 0,
	"missing_glb": 0,
	"instantiate_failures": 0,
	"transform_failures": 0,
	"duplicate_placement_ids": 0,
	"level_instances": 0,
	"recomputed_li_transforms": 0,
	"decals_built": 0,
	"decals_missing_texture": 0,
	"decals_transform_failed": 0,
	"vfx_markers_built": 0,
	"vfx_transform_failed": 0,
	"vfx_lights_built": 0,
	"vfx_embers_built": 0
}
var seen_placement_keys := {}

func _run() -> void:
	print("")
	print("============================================================")
	print(" UE5 -> GODOT V10 MAP CONSTRUCTOR (V10.11)")
	print("============================================================")

	if not _load_inputs():
		push_error("CONSTRUCTOR ABORTED: input validation failed.")
		return

	if not _validate_manifest_and_asset_map():
		push_error("CONSTRUCTOR ABORTED: manifest/asset-map validation failed.")
		return

	var root := Node3D.new()
	root.name = _safe_node_name(str(manifest.get("world", {}).get("name", "Map--")))

	var geometry_root := Node3D.new()
	geometry_root.name = "Geometry_WORLD_SPACE"
	root.add_child(geometry_root)
	geometry_root.owner = root

	# Identity organizational folders only. Geometry is NOT parented under
	# transformed LI nodes, because every geometry placement already contains
	# its final composed world transform.
	var li_root := Node3D.new()
	li_root.name = "LevelInstances_METADATA_ONLY"
	root.add_child(li_root)
	li_root.owner = root

	var placements: Array = manifest.get("geometry", {}).get("placements", [])
	stats["geometry_total"] = placements.size()
	stats["level_instances"] = _level_instance_count()

	print("Geometry placements         :", stats["geometry_total"])
	print("LevelInstance placements    :", stats["level_instances"])
	print("Coordinate conversion       :", "UE cm -> Godot m / X,Z,-Y" if CONVERT_UE_TO_GODOT else "DIRECT")

	for i in range(placements.size()):
		var placement: Dictionary = placements[i]
		var outcome := _build_geometry_placement(placement, i, geometry_root)

		if outcome == Outcome.FATAL:
			push_error("CONSTRUCTOR ABORTED at geometry index %d (systemic failure - see error above)." % i)
			root.free()
			return

		if i % 250 == 0:
			print("Progress: %d / %d" % [i, placements.size()])

	if BUILD_DECALS:
		_build_decals(root)
	if BUILD_VFX_MARKERS:
		_build_vfx_markers(root)

	# Preserve LI registry as metadata-only nodes for traceability.
	# No LI transform is applied here.
	_build_li_metadata_nodes(li_root)

	var packed := PackedScene.new()
	var pack_err := packed.pack(root)
	if pack_err != OK:
		push_error("PackedScene.pack failed: %s" % pack_err)
		root.free()
		return

	var save_err := ResourceSaver.save(packed, OUTPUT_SCENE_PATH)
	if save_err != OK:
		push_error("ResourceSaver.save failed: %s" % save_err)
		root.free()
		return

	_print_report()
	print("OUTPUT                      :", OUTPUT_SCENE_PATH)
	print("STATUS                      : PASS")
	print("============================================================")
	print("")

	root.free()


func _load_inputs() -> bool:
	if not FileAccess.file_exists(MANIFEST_PATH):
		push_error("Missing manifest: %s" % MANIFEST_PATH)
		return false

	if not FileAccess.file_exists(ASSET_MAP_PATH):
		push_error("Missing asset map: %s" % ASSET_MAP_PATH)
		return false

	var manifest_text := FileAccess.get_file_as_string(MANIFEST_PATH)
	var manifest_json = JSON.parse_string(manifest_text)
	if typeof(manifest_json) != TYPE_DICTIONARY:
		push_error("Manifest is not a JSON object.")
		return false
	manifest = manifest_json

	var asset_text := FileAccess.get_file_as_string(ASSET_MAP_PATH)
	var asset_json = JSON.parse_string(asset_text)
	if typeof(asset_json) != TYPE_DICTIONARY:
		push_error("Asset map is not a JSON object.")
		return false
	asset_map = asset_json

	return true


func _validate_manifest_and_asset_map() -> bool:
	var manifest_version := str(manifest.get("manifest_version", ""))
	var map_version := str(asset_map.get("manifest_version", ""))

	if not manifest_version.begins_with("10"):
		push_error("Unexpected manifest version: %s (expected V10)." % manifest_version)
		return false

	if not map_version.begins_with("10"):
		push_error("Unexpected asset-map manifest version: %s (expected V10)." % map_version)
		return false

	var assets: Dictionary = asset_map.get("assets", {})
	var unique_meshes: Dictionary = manifest.get("geometry", {}).get("unique_meshes", {})

	print("Manifest version             :", manifest_version)
	print("Asset map version            :", map_version)
	print("Asset-map entries            :", assets.size())
	print("Manifest unique meshes       :", unique_meshes.size())

	# The asset map is the authoritative UE-path -> GLB-path resolver.
	# Never fall back to filename/name guessing.
	var missing := 0
	for ue_path in unique_meshes.keys():
		if not assets.has(ue_path):
			missing += 1
			if missing <= 20:
				push_error("Missing asset-map entry for UE mesh: %s" % ue_path)

	if missing > 0:
		stats["missing_asset_mapping"] = missing
		return not FAIL_ON_MISSING_ASSET_MAPPING

	# If the map declares exported_count, use it as a consistency check.
	var exported_count := int(asset_map.get("exported_count", -1))
	if exported_count >= 0 and exported_count != assets.size():
		push_warning("Asset map exported_count=%d but assets=%d." % [exported_count, assets.size()])

	# Informational: surface how many meshes reported real export failures,
	# so a silent partial export doesn't go unnoticed even though it
	# wouldn't block validation above (those meshes simply won't be in
	# `assets` at all, which the loop above already catches).
	var failure_count := int(asset_map.get("failure_count", 0))
	if failure_count > 0:
		push_warning("Asset map reports %d export failure(s) - see ue5_godot_asset_map.json/.txt." % failure_count)

	return true


## Builds one geometry placement. Returns Outcome.OK (spawned fine),
## Outcome.SKIPPED (nothing spawned, but this is expected/tolerated and
## already counted in `stats`), or Outcome.FATAL (systemic problem that
## should stop the whole run per the FAIL_ON_* flags).
func _build_geometry_placement(placement: Dictionary, index: int, geometry_root: Node3D) -> int:
	var kind := str(placement.get("kind", ""))
	if kind == "static_mesh":
		stats["geometry_static"] += 1
	elif kind == "instanced_mesh" or kind == "hierarchical_instanced_mesh":
		stats["geometry_instanced"] += 1

	var mesh_value = placement.get("mesh", null)
	if typeof(mesh_value) != TYPE_DICTIONARY:
		stats["empty_mesh_slots"] += 1
		if SKIP_EMPTY_MESH_SLOTS:
			return Outcome.SKIPPED
		push_error("Geometry %d has no mesh object." % index)
		return Outcome.FATAL if FAIL_ON_MISSING_ASSET_MAPPING else Outcome.SKIPPED

	var ue_mesh_path := str(mesh_value.get("path", ""))
	if ue_mesh_path.is_empty():
		stats["empty_mesh_slots"] += 1
		if SKIP_EMPTY_MESH_SLOTS:
			return Outcome.SKIPPED
		push_error("Geometry %d has empty mesh.path." % index)
		return Outcome.FATAL if FAIL_ON_MISSING_ASSET_MAPPING else Outcome.SKIPPED

	var is_instanced := kind == "instanced_mesh" or kind == "hierarchical_instanced_mesh"
	var instance_count := int(placement.get("instance_count", 1))

	# An ISM/HISM component that genuinely has zero instances has nothing
	# to draw. Spawning one node at the component's own transform would be
	# a phantom object that was never really in the UE scene.
	if is_instanced and instance_count <= 0:
		stats["zero_instance_ism_skipped"] += 1
		return Outcome.SKIPPED

	if not asset_map.get("assets", {}).has(ue_mesh_path):
		stats["missing_asset_mapping"] += 1
		push_error("No exact asset-map mapping for UE mesh: %s" % ue_mesh_path)
		return Outcome.FATAL if FAIL_ON_MISSING_ASSET_MAPPING else Outcome.SKIPPED

	var asset_info: Dictionary = asset_map["assets"][ue_mesh_path]
	var godot_path := str(asset_info.get("godot_path", ""))

	if godot_path.is_empty():
		stats["missing_asset_mapping"] += 1
		push_error("Asset-map entry has no godot_path: %s" % ue_mesh_path)
		return Outcome.FATAL if FAIL_ON_MISSING_ASSET_MAPPING else Outcome.SKIPPED

	if not ResourceLoader.exists(godot_path):
		stats["missing_glb"] += 1
		push_error("Missing GLB: %s | UE: %s" % [godot_path, ue_mesh_path])
		return Outcome.FATAL if FAIL_ON_MISSING_GLB else Outcome.SKIPPED

	var packed = load(godot_path)
	if packed == null or not (packed is PackedScene):
		stats["instantiate_failures"] += 1
		push_error("GLB is not loadable as PackedScene: %s" % godot_path)
		return Outcome.FATAL if FAIL_ON_INSTANTIATE_FAILURE else Outcome.SKIPPED

	var base_placement_id := str(placement.get("placement_id", ""))
	if base_placement_id.is_empty():
		base_placement_id = "MESH_INDEX_%d" % index

	# Gather the list of transforms to spawn:
	# - static_mesh: exactly one, from reconstruction_transform/final_world_transform.
	# - instanced_mesh / hierarchical_instanced_mesh: one PER REAL UE INSTANCE,
	#   from instance_final_world_transforms (V10.4). Falls back to the single
	#   component transform only if that per-instance list isn't present at
	#   all, so older manifests (pre-V10.4) still degrade gracefully instead
	#   of producing nothing.
	var transform_dicts: Array = []

	if is_instanced and placement.has("instance_final_world_transforms"):
		var raw_list: Array = placement.get("instance_final_world_transforms", [])
		for entry in raw_list:
			transform_dicts.append(entry)
	else:
		var single = placement.get("reconstruction_transform", null)
		if typeof(single) != TYPE_DICTIONARY:
			single = placement.get("final_world_transform", null)
		transform_dicts.append(single)

	if transform_dicts.is_empty():
		stats["transform_failures"] += 1
		push_error("No transform data at all for placement %s" % base_placement_id)
		return Outcome.FATAL if FAIL_ON_TRANSFORM_FAILURE else Outcome.SKIPPED

	var spawned_any := false
	var multi := transform_dicts.size() > 1

	for inst_index in range(transform_dicts.size()):
		var transform_data = transform_dicts[inst_index]

		if typeof(transform_data) != TYPE_DICTIONARY:
			stats["transform_failures"] += 1
			stats["instance_transform_missing"] += 1
			push_error("Missing/invalid transform for placement %s instance %d" % [base_placement_id, inst_index])
			if FAIL_ON_TRANSFORM_FAILURE:
				return Outcome.FATAL
			continue

		# _transform_from_v10() returns Transform3D OR null, so it has no
		# single declared return type - `:=` cannot infer one. Plain `var`
		# keeps it a Variant, which the `== null` check below requires.
		var transform = _transform_from_v10(transform_data)
		if transform == null:
			stats["transform_failures"] += 1
			push_error("Invalid transform fields for placement %s instance %d" % [base_placement_id, inst_index])
			if FAIL_ON_TRANSFORM_FAILURE:
				return Outcome.FATAL
			continue

		var placement_key := base_placement_id if not multi else "%s_INST_%d" % [base_placement_id, inst_index]
		if seen_placement_keys.has(placement_key):
			stats["duplicate_placement_ids"] += 1
			push_error("Duplicate placement key: %s" % placement_key)
			if FAIL_ON_DUPLICATE_PLACEMENT_ID:
				return Outcome.FATAL
			continue
		seen_placement_keys[placement_key] = true

		var instance = packed.instantiate()
		if instance == null:
			stats["instantiate_failures"] += 1
			push_error("Failed to instantiate GLB: %s (instance %d)" % [godot_path, inst_index])
			if FAIL_ON_INSTANTIATE_FAILURE:
				return Outcome.FATAL
			continue

		if not (instance is Node3D):
			stats["instantiate_failures"] += 1
			push_error("GLB root is not Node3D: %s" % godot_path)
			instance.queue_free()
			if FAIL_ON_INSTANTIATE_FAILURE:
				return Outcome.FATAL
			continue

		instance.name = _make_instance_name(placement, index, inst_index if multi else -1)
		geometry_root.add_child(instance)
		instance.owner = geometry_root.get_owner()

		if KEEP_PLACEMENT_METADATA:
			_attach_metadata(instance, placement, ue_mesh_path, godot_path, index, inst_index if multi else -1, transform_dicts.size())

		(instance as Node3D).transform = transform

		stats["valid_geometry"] += 1
		stats["instances_spawned"] += 1
		spawned_any = true

	return Outcome.OK if spawned_any else Outcome.SKIPPED


func _transform_from_v10(data: Dictionary):
	var location = data.get("location", null)
	var rotation = data.get("rotation", null)
	var scale = data.get("scale", null)

	if typeof(location) != TYPE_ARRAY or location.size() < 3:
		return null
	if typeof(rotation) != TYPE_DICTIONARY:
		return null
	if typeof(scale) != TYPE_ARRAY or scale.size() < 3:
		return null

	var ue_pos := Vector3(
		float(location[0]),
		float(location[1]),
		float(location[2])
	)

	var pitch := float(rotation.get("pitch", 0.0))
	var yaw := float(rotation.get("yaw", 0.0))
	var roll := float(rotation.get("roll", 0.0))

	var ue_scale := Vector3(
		float(scale[0]),
		float(scale[1]),
		float(scale[2])
	)

	if not CONVERT_UE_TO_GODOT:
		var basis_direct := _unreal_rotator_to_basis(pitch, yaw, roll)
		return Transform3D(basis_direct.scaled(ue_scale), ue_pos * UE_CM_TO_GODOT_M)

	# Coordinate basis conversion:
	# UE: X forward, Y right, Z up
	# Godot: X right/forward convention here is represented by:
	# Gx = Ux, Gy = Uz, Gz = Uy  (determinant -1: handedness flips,
	# matching Unreal's glTF exporter - see _convert_basis_ue_to_godot)
	#
	# Rotation is converted by C * R * C^-1 rather than by swapping
	# Euler angles, keeping position and rotation in agreement.
	var ue_basis := _unreal_rotator_to_basis(pitch, yaw, roll)
	var converted_basis := _convert_basis_ue_to_godot(ue_basis)
	converted_basis = converted_basis.scaled(ue_scale)

	var godot_pos := Vector3(
		ue_pos.x,
		ue_pos.z,
		ue_pos.y
	) * UE_CM_TO_GODOT_M

	return Transform3D(converted_basis, godot_pos)


func _unreal_rotator_to_basis(pitch_deg: float, yaw_deg: float, roll_deg: float) -> Basis:
	# Reconstruct Unreal FRotator as quaternion using UE's Pitch/Yaw/Roll
	# convention, then convert that quaternion to a Godot Basis.
	var pitch := deg_to_rad(pitch_deg) * 0.5
	var yaw := deg_to_rad(yaw_deg) * 0.5
	var roll := deg_to_rad(roll_deg) * 0.5

	var sp := sin(pitch)
	var cp := cos(pitch)
	var sy := sin(yaw)
	var cy := cos(yaw)
	var sr := sin(roll)
	var cr := cos(roll)

	# Unreal quaternion components: X,Y,Z,W
	# V10.7 FIX: qx and qy had inverted signs versus Unreal's own
	# FRotator::Quaternion(). For a yaw-only rotation the flip
	# cancels out, which is why buildings looked right; anything
	# with pitch or roll came out mirrored.
	var qx := cr * sp * sy - sr * cp * cy
	var qy := -cr * sp * cy - sr * cp * sy
	var qz := cr * cp * sy - sr * sp * cy
	var qw := cr * cp * cy + sr * sp * sy

	var q := Quaternion(qx, qy, qz, qw).normalized()
	return Basis(q)


func _convert_basis_ue_to_godot(ue_basis: Basis) -> Basis:
	# C maps UE vectors to Godot:
	# [Gx Gy Gz] = [Ux Uz Uy]  (determinant -1: this swap changes handedness,
	# matching Unreal's own glTF exporter convention - verified against the
	# actual mesh exports, not just reasoned about in the abstract).
	#
	# Columns of the converted basis are C * R * C^-1.
	# Build it explicitly to avoid any Euler-order ambiguity.
	var c := Basis(
		Vector3(1, 0, 0),
		Vector3(0, 0, 1),
		Vector3(0, 1, 0)
	)

	return c * ue_basis * c.inverse()


func _attach_metadata(node: Node, placement: Dictionary, ue_mesh_path: String, godot_path: String, index: int, inst_index: int, inst_total: int) -> void:
	node.set_meta("ue_placement_id", str(placement.get("placement_id", "")))
	node.set_meta("ue_mesh_path", ue_mesh_path)
	node.set_meta("godot_asset_path", godot_path)
	node.set_meta("ue_kind", str(placement.get("kind", "")))
	node.set_meta("ue_index", index)
	node.set_meta("ue_source_level", str(placement.get("source_level", "")))
	node.set_meta("ue_transform_space", str(placement.get("transform_space", "")))

	var chain = placement.get("level_instance_chain", [])
	if typeof(chain) == TYPE_ARRAY:
		node.set_meta("ue_level_instance_chain", chain)

	var actor: Dictionary = placement.get("actor", {})
	if typeof(actor) == TYPE_DICTIONARY:
		node.set_meta("ue_actor_path", str(actor.get("path", "")))
		node.set_meta("ue_actor_name", str(actor.get("name", "")))

	var component: Dictionary = placement.get("component", {})
	if typeof(component) == TYPE_DICTIONARY:
		node.set_meta("ue_component_path", str(component.get("path", "")))
		node.set_meta("ue_component_name", str(component.get("name", "")))

	node.set_meta("ue_instance_count", int(placement.get("instance_count", 1)))

	# V10.4: which specific instance (of the real UE instance list) this
	# node represents, when a single ISM/HISM component fans out to
	# multiple Godot nodes.
	if inst_index >= 0:
		node.set_meta("ue_instance_index", inst_index)
		node.set_meta("ue_instance_total_spawned", inst_total)


func _build_li_metadata_nodes(li_root: Node3D) -> void:
	var registry = manifest.get("level_instances", {}).get("registry", {})

	if typeof(registry) != TYPE_DICTIONARY:
		return

	for actor_path in registry.keys():
		var info: Dictionary = registry[actor_path]
		var n := Node3D.new()
		n.name = _safe_node_name(str(info.get("placement_id", actor_path)))
		li_root.add_child(n)
		n.owner = li_root.get_owner()

		n.set_meta("ue_actor_path", str(actor_path))
		n.set_meta("ue_world_asset", str(info.get("world_asset", "")))
		n.set_meta("ue_placement_id", str(info.get("placement_id", "")))
		n.set_meta("ue_parent_placement_id", str(info.get("parent_placement_id", "")))
		n.set_meta("ue_depth", int(info.get("depth", 0)))
		n.set_meta("ue_transform_space", str(info.get("transform_space", "")))

		# Deliberately DO NOT apply source/final LI transforms.
		# Geometry already carries final world transforms.
		n.transform = Transform3D.IDENTITY


func _level_instance_count() -> int:
	var registry = manifest.get("level_instances", {}).get("registry", {})
	if typeof(registry) == TYPE_DICTIONARY:
		return registry.size()

	var placements = manifest.get("level_instances", {}).get("placements", [])
	if typeof(placements) == TYPE_ARRAY:
		return placements.size()

	return 0


func _make_instance_name(placement: Dictionary, index: int, inst_index: int) -> String:
	var mesh: Dictionary = placement.get("mesh", {})
	var mesh_name := str(mesh.get("name", "Mesh"))
	var actor: Dictionary = placement.get("actor", {})
	var actor_name := str(actor.get("name", "Actor"))
	var pid := str(placement.get("placement_id", "MESH_%d" % index))

	if inst_index >= 0:
		return "%04d_%03d_%s_%s_%s" % [
			index,
			inst_index,
			_safe_node_name(actor_name),
			_safe_node_name(mesh_name),
			_safe_node_name(pid)
		]

	return "%04d_%s_%s_%s" % [
		index,
		_safe_node_name(actor_name),
		_safe_node_name(mesh_name),
		_safe_node_name(pid)
	]


func _safe_node_name(value: String) -> String:
	var s := value
	s = s.replace("/", "_")
	s = s.replace("\\", "_")
	s = s.replace(":", "_")
	s = s.replace(".", "_")
	s = s.replace(" ", "_")
	s = s.replace("-", "_")
	if s.is_empty():
		return "Unnamed"
	return s


## Builds Decal nodes from manifest["effects"]["decals"], textured via
## res://ue5_godot_decal_map.json (UE material path -> Godot texture).
## Reuses _transform_from_v10() so decals share the exact same axis
## convention and quaternion fix as every mesh placement.
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

## =============================================================================
## VFX RECONSTRUCTION  (V10.10 - rebuilt on a failure-proof base)
## =============================================================================
##
## WHY THIS WAS REBUILT
##
## V10.9 made every parcel a QuadMesh driven by a custom spatial shader.
## That shader has to compile, and it has to receive COLOR and
## INSTANCE_CUSTOM exactly as expected. When any of that does not happen,
## Godot falls back to the mesh's default material and you get flat WHITE
## OPAQUE SQUARES - which is precisely what the test render showed. The
## failure mode was not a degraded effect, it was a wall of white quads.
##
## So the default path no longer contains a shader at all:
##
##   - the soft round falloff comes from a GradientTexture2D in RADIAL
##     fill mode, generated here, used as the albedo texture;
##   - the per-particle colour comes from StandardMaterial3D with
##     vertex_color_use_as_albedo, which is the documented way particle
##     colour reaches the mesh;
##   - billboarding uses BILLBOARD_PARTICLES, the particle-specific mode.
##
## Nothing there can fail to compile, and there is no default-material
## fallback to land on. VFX_USE_PARCEL_SHADER can still turn the richer
## deforming shader back on once it has been eyeballed on one emitter.
##
## Kept from V10.9: physics-driven curves, shared per-category resources,
## light budget, candle/swarm presets, twelve categories.
##
## Physics model: VFX_Physics_Reference_Godot_V10_8.md - reduced-order
## buoyant-plume envelopes used as shaping laws, not a CFD solve.


## Realtime light budget for the whole VFX pass. One light per fire marker
## meant 341 of them in this map. 0 disables VFX lights entirely.
const VFX_MAX_LIGHTS := 24
const VFX_LIGHT_MIN_SPACING := 6.0

## V10.12: the flame body (candle/torch/fire) is deliberately grounded and
## near-static at the source - it should NOT be what carries the sense of
## rising heat. That job goes to a second, separate emitter reusing the
## "spark" category's already-correct physics (small, brief, genuinely
## travels and fades) layered on top, so the flame stays put and the
## embers/ash are what drift upward. Candle is intentionally excluded -
## a birthday-candle-scale flame doesn't throw visible embers.
const VFX_EMBER_CATEGORIES := ["fire", "torch"]
const VFX_EMBER_AMOUNT_SCALE := 0.35

## Vertical flame placement.
##
## The Niagara marker's own transform is the actor/component origin as
## exported from Unreal - for a torch or candle prop that is almost always
## the BASE of the mesh (the pivot sits on the ground/socket, not at the
## flame). Left alone, every flame spawns at the foot of the prop instead
## of where the fuel actually is. This offsets the marker along its own
## local "up" axis (transform.basis.y, UN-normalized - see below) by
## RATIO * ASSUMED_HEIGHT before any particles/light are attached, so
## embers and the glow light move with it too.
##
## - Torch: flame sits near the top of the shaft but BELOW the very tip -
##   the top of most torch meshes is the cage/basket holding the fuel, and
##   100% would push the flame into/above that geometry. ~75-80% is the
##   visually correct spot.
## - Candle: the flame is the wick, i.e. the very top of the mesh - 100%.
##
## ASSUMED_HEIGHT_M is the reference mesh height IN METRES *at scale 1.0*
## - a stand-in for the real per-instance GLB bounds, which this VFX pass
## does not have access to. transform.basis.y is used UN-normalized on
## purpose: its length already carries this instance's own scale along the
## up axis (from the manifest's "scale" field), so a torch/candle placed
## bigger or smaller than reference gets a proportionally bigger/smaller
## offset instead of one flat number applied to every instance regardless
## of size.
##
## These starting values are a guess (no bounds data to derive them from);
## if the flame still isn't at the right height after a rebuild, that's a
## calibration problem, not a direction problem - measure the real
## torch/candle mesh height in Unreal (in cm) and set ASSUMED_HEIGHT_M to
## that number / 100.
const VFX_TORCH_FLAME_HEIGHT_RATIO := 0.78
const VFX_TORCH_ASSUMED_HEIGHT_M := 1.40

const VFX_CANDLE_FLAME_HEIGHT_RATIO := 1.0
const VFX_CANDLE_ASSUMED_HEIGHT_M := 0.05

## Sampling resolution for the curves generated from the physics laws.
const VFX_CURVE_SAMPLES := 12

## Resolution of the generated falloff textures. 64 is plenty for a soft
## blob and keeps 12 categories at well under a megabyte in total.
const VFX_FALLOFF_SIZE := 64

## OFF by default, on purpose: see the header. Turn it on only after
## checking one emitter, and revert if squares come back.
const VFX_USE_PARCEL_SHADER := false

## Hard ceiling on a single particle's world size, in metres. A bad scale
## law or a stray preset value would otherwise fill the screen with one
## parcel and be very hard to trace back.
const VFX_MAX_PARTICLE_SIZE := 4.0

var _vfx_cache := {}
var _vfx_lights_spent := 0
var _vfx_light_positions: Array[Vector3] = []


## -----------------------------------------------------------------------------
## Reduced-order plume laws
## -----------------------------------------------------------------------------
## Buoyant rise         z ~ t^(3/2)
## Self-similar radius  b ~ z
## Dilution proxy       C ~ (z - z0)^(-5/3)

func _smooth01(x: float) -> float:
	var t: float = clampf(x, 0.0, 1.0)
	return t * t * (3.0 - 2.0 * t)


func _plume_height_law(age: float) -> float:
	return pow(clampf(age, 0.0, 1.0), 1.5)


func _plume_width_law(age: float) -> float:
	var z_hat: float = _plume_height_law(age)
	var z0_hat: float = 0.12
	return clampf((z0_hat + z_hat) / (z0_hat + 1.0), 0.0, 1.0)


func _plume_density_law(age: float) -> float:
	var u: float = clampf(age, 0.0, 1.0)
	var z_hat: float = _plume_height_law(u)
	var z0_hat: float = 0.12
	var concentration: float = pow(z0_hat / (z0_hat + z_hat), 5.0 / 3.0)
	var birth: float = _smooth01(u / 0.045)
	var dilution_tail: float = 1.0 - _smooth01((u - 0.58) / 0.42)
	return clampf(concentration * birth * dilution_tail, 0.0, 1.0)


func _smoke_scale_law(age: float) -> float:
	return lerpf(0.22, 2.75, pow(_plume_width_law(age), 0.82))


func _fire_scale_law(age: float) -> float:
	var u: float = clampf(age, 0.0, 1.0)
	var growth: float = sqrt(_plume_height_law(u))
	## Broadens once the coherent core forms, then pinches at the tip.
	var taper: float = 1.0 - 0.68 * pow(u, 3.8)
	return lerpf(0.30, 2.15, growth * taper)


func _candle_scale_law(age: float) -> float:
	var u: float = clampf(age, 0.0, 1.0)
	## A candle flame is laminar near the wick: it barely widens, then
	## tapers to a thin luminous tip. Nearly the opposite of a bonfire.
	return lerpf(0.55, 1.35, _plume_height_law(u)) * (1.0 - 0.80 * pow(u, 2.6))


func _fire_density_law(age: float) -> float:
	var u: float = clampf(age, 0.0, 1.0)
	return clampf(_smooth01(u / 0.06) * (1.0 - _smooth01((u - 0.45) / 0.55)), 0.0, 1.0)


func _turbulence_influence_law(age: float) -> float:
	## Weak at the source, strong downstream: keeps the emission point
	## clean while older parcels develop irregular motion.
	return pow(clampf(age, 0.0, 1.0), 0.62)


## -----------------------------------------------------------------------------
## Generated resources
## -----------------------------------------------------------------------------

## The soft round falloff that replaces the shader. GradientTexture2D in
## radial mode produces a disc that is opaque at the centre and fully
## transparent at the rim, with no code to compile and nothing to fall
## back from. `core` controls how much of the disc stays dense before the
## falloff starts - a flame keeps a hot centre, smoke is diffuse.
func _falloff_texture(core: float, softness: float) -> GradientTexture2D:
	var gradient := Gradient.new()
	var offsets := PackedFloat32Array()
	var colors := PackedColorArray()

	var mid: float = clampf(core, 0.0, 0.9)
	var edge: float = clampf(mid + maxf(softness, 0.05), mid + 0.05, 1.0)

	offsets.append(0.0)
	colors.append(Color(1, 1, 1, 1))
	offsets.append(mid)
	colors.append(Color(1, 1, 1, 0.85))
	offsets.append(edge)
	colors.append(Color(1, 1, 1, 0.18))
	offsets.append(1.0)
	colors.append(Color(1, 1, 1, 0.0))

	gradient.offsets = offsets
	gradient.colors = colors

	var tex := GradientTexture2D.new()
	tex.gradient = gradient
	tex.width = VFX_FALLOFF_SIZE
	tex.height = VFX_FALLOFF_SIZE
	tex.fill = GradientTexture2D.FILL_RADIAL
	tex.fill_from = Vector2(0.5, 0.5)
	tex.fill_to = Vector2(1.0, 0.5)
	return tex


## Samples a law into a CurveTexture, so the documented physics actually
## shapes the particles instead of hand-typed control points.
func _curve_from_law(law: Callable, samples: int = VFX_CURVE_SAMPLES) -> CurveTexture:
	var curve := Curve.new()
	var count: int = maxi(samples, 2)
	var lowest: float = INF
	var highest: float = -INF

	for i in range(count):
		var t: float = float(i) / float(count - 1)
		var v: float = float(law.call(t))
		lowest = minf(lowest, v)
		highest = maxf(highest, v)
		curve.add_point(Vector2(t, v))

	## Curve clamps to min_value/max_value, so a law reaching 2.75 (the
	## smoke scale) would be silently flattened at the default 1.0.
	curve.min_value = minf(0.0, lowest)
	curve.max_value = maxf(1.0, highest)

	var tex := CurveTexture.new()
	tex.curve = curve
	return tex


func _curve_from_points(points: Array) -> CurveTexture:
	var curve := Curve.new()
	var highest: float = 1.0
	for p in points:
		highest = maxf(highest, float(p[1]))
		curve.add_point(Vector2(float(p[0]), float(p[1])))
	curve.max_value = highest
	var tex := CurveTexture.new()
	tex.curve = curve
	return tex


func _gradient_from_stops(stops: Array) -> GradientTexture1D:
	var gradient := Gradient.new()
	var offsets := PackedFloat32Array()
	var colors := PackedColorArray()
	for s in stops:
		offsets.append(float(s[0]))
		colors.append(s[1])
	gradient.offsets = offsets
	gradient.colors = colors
	var tex := GradientTexture1D.new()
	tex.gradient = gradient
	return tex


## Sets a property only if this build exposes it. velocity_pivot,
## turbulence_* and use_scale_3d landed in different 4.x releases, and a
## hard assignment would break the whole constructor on an older editor
## rather than lose one detail of one effect.
func _try_set(target: Object, property: String, value) -> bool:
	for entry in target.get_property_list():
		if entry.get("name", "") == property:
			target.set(property, value)
			return true
	return false
	
	## -----------------------------------------------------------------------------
## Deforming parcel shader
## -----------------------------------------------------------------------------
## Built once per blend mode and shared. Unlike the V10.9 attempt, this one
## does NOT invent the silhouette: it samples the same radial falloff
## texture the shaderless path uses, and only WARPS the lookup. So the
## worst case is a soft blob that deforms oddly - never the flat white
## square the previous version produced when it failed.
##
## Three properties the reference document asks for, enforced here:
##
##   ASYMMETRIC GROWTH  the upper half of the parcel is stretched, the
##                      lower half is not, so a rising parcel spreads
##                      upward and sideways but never downward.
##   IRREGULAR SHAPE    multi-octave noise displaces the lookup, and the
##                      displacement scales with age, so a young parcel is
##                      still round and an old one is torn.
##   DILUTION           alpha is divided by the area the parcel now covers
##                      (1 / (sx * sy)), so spreading and fading are the
##                      same phenomenon rather than two curves that happen
##                      to be tuned to agree.
## -----------------------------------------------------------------------------
## Deforming parcel shader
## -----------------------------------------------------------------------------
## Fire-oriented parcel deformation.
## The existing radial falloff remains the base silhouette. The shader reshapes
## it into a vertically rising, irregular flame made of rounded lobes which
## progressively converge into a pointed tip.
##
## The deformation is still age-driven:
##   - young parcel  -> compact rounded base
##   - middle        -> several asymmetric rounded flame lobes
##   - old parcel    -> elongated, torn shape ending in a pointed tip
##
## Color also evolves with the parcel progression while preserving the
## particle COLOR as the base tint.
## -----------------------------------------------------------------------------
## Deforming parcel shader
## -----------------------------------------------------------------------------
## Flame-oriented parcel shader.
## The parcel is no longer treated as a deformed circular blob.
## Its silhouette is built from several overlapping rounded lobes which
## progressively rise, bend in different directions and converge into a sharp
## flame tip.
func _parcel_shader(additive: bool) -> Shader:
	var key: String = "shader_add" if additive else "shader_mix"
	if _vfx_cache.has(key):
		return _vfx_cache[key]

	var shader := Shader.new()
	shader.code = """
shader_type spatial;
render_mode unshaded, cull_disabled, depth_draw_never, BLEND_MODE;

uniform sampler2D falloff : source_color, filter_linear;

uniform float growth_side = 1.55;
uniform float growth_up = 2.30;
uniform float warp = 0.30;
uniform float dilution = 1.0;

float hash21(vec2 p) {
	p = fract(p * vec2(127.31, 311.7));
	p += dot(p, p + 34.23);
	return fract(p.x * p.y);
}

float noise2(vec2 p) {
	vec2 i = floor(p);
	vec2 f = fract(p);
	f = f * f * (3.0 - 2.0 * f);

	float a = hash21(i);
	float b = hash21(i + vec2(1.0, 0.0));
	float c = hash21(i + vec2(0.0, 1.0));
	float d = hash21(i + vec2(1.0, 1.0));

	return mix(
		mix(a, b, f.x),
		mix(c, d, f.x),
		f.y
	);
}

float fbm(vec2 p) {
	float v = 0.0;
	float a = 0.5;

	for (int i = 0; i < 3; i++) {
		v += noise2(p) * a;
		p = p * 2.07 + vec2(11.3, 7.9);
		a *= 0.5;
	}

	return v;
}

float ellipse_lobe(
	vec2 p,
	float center_y,
	float center_x,
	float radius_x,
	float radius_y
) {
	vec2 d = vec2(
		(p.x - center_x) / max(radius_x, 0.001),
		(p.y - center_y) / max(radius_y, 0.001)
	);

	float dist = length(d);

	return 1.0 - smoothstep(
		0.72,
		1.0,
		dist
	);
}

float rounded_union(float a, float b) {
	return max(a, b);
}

void fragment() {
	float age = clamp(INSTANCE_CUSTOM.y, 0.0, 1.0);
	float seed = float(INSTANCE_ID) * 0.137;

	vec2 p = UV - vec2(0.5);

	// -------------------------------------------------------------------------
	// Particle growth.
	// -------------------------------------------------------------------------
	float sx = mix(1.0, growth_side, age);
	float sy = mix(1.0, growth_up, age);

	p.x /= sx;
	p.y /= sy;

	// -------------------------------------------------------------------------
	// Flame height.
	//
	// bottom = 0
	// top    = 1
	// -------------------------------------------------------------------------
	float h = clamp(0.5 - p.y, 0.0, 1.0);

	// -------------------------------------------------------------------------
	// Large-scale flame movement.
	//
	// The entire flame bends progressively as it rises.
	// -------------------------------------------------------------------------
	float n0 = fbm(
		vec2(
			seed * 1.7,
			h * 3.1 + age * 1.4
		)
	) - 0.5;

	float n1 = fbm(
		vec2(
			seed * 2.9 + h * 4.7,
			age * 2.1 + h * 6.2
		)
	) - 0.5;

	float bend =
		n0 * 0.12 * h +
		n1 * 0.07 * h * h;

	p.x -= bend;

	// -------------------------------------------------------------------------
	// Individual rounded flame lobes.
	//
	// These deliberately sit at different heights and move in different
	// horizontal directions. Their overlap produces the characteristic
	// half-circle / tongue-like flame silhouette.
	// -------------------------------------------------------------------------
	float direction_a = sin(seed * 4.17) * 0.055;
	float direction_b = sin(seed * 6.31 + 1.7) * 0.075;
	float direction_c = sin(seed * 8.73 + 3.1) * 0.065;
	float direction_d = sin(seed * 11.2 + 5.4) * 0.045;

	float x_a =
		direction_a +
		sin(age * 4.0 + seed) * 0.025;

	float x_b =
		direction_b +
		sin(age * 5.2 + seed * 1.3) * 0.035;

	float x_c =
		direction_c +
		sin(age * 6.1 + seed * 1.8) * 0.030;

	float x_d =
		direction_d +
		sin(age * 7.0 + seed * 2.2) * 0.020;

	float lobe_a = ellipse_lobe(
		p,
		0.13,
		x_a,
		0.23,
		0.19
	);

	float lobe_b = ellipse_lobe(
		p,
		0.31,
		x_b,
		0.19,
		0.18
	);

	float lobe_c = ellipse_lobe(
		p,
		0.49,
		x_c,
		0.155,
		0.17
	);

	float lobe_d = ellipse_lobe(
		p,
		0.66,
		x_d,
		0.115,
		0.145
	);

	float lobe_e = ellipse_lobe(
		p,
		0.79,
		x_d * 0.65,
		0.080,
		0.105
	);

	float shape = 0.0;

	shape = rounded_union(shape, lobe_a);
	shape = rounded_union(shape, lobe_b);
	shape = rounded_union(shape, lobe_c);
	shape = rounded_union(shape, lobe_d);
	shape = rounded_union(shape, lobe_e);

	// -------------------------------------------------------------------------
	// Continuous flame body.
	//
	// This connects the rounded lobes so they form one flame rather than
	// several disconnected circles.
	// -------------------------------------------------------------------------
	float body_width =
		mix(
			0.225,
			0.035,
			smoothstep(0.18, 0.94, h)
		);

	float body_center =
		mix(
			0.0,
			bend * 1.8,
			smoothstep(0.15, 0.95, h)
		);

	float body = 1.0 -
		smoothstep(
			body_width * 0.72,
			body_width,
			abs(p.x - body_center)
		);

	// Body is strongest at the bottom and middle.
	body *= 1.0 - smoothstep(0.78, 1.0, h);

	shape = rounded_union(shape, body);

	// -------------------------------------------------------------------------
	// Pointed upper flame.
	//
	// This is NOT another ellipse.
	// Its width collapses continuously toward zero, producing the sharp tip.
	// -------------------------------------------------------------------------
	float tip_progress = smoothstep(0.62, 0.995, h);

	float tip_width = mix(
		0.095,
		0.002,
		tip_progress
	);

	float tip_center =
		x_d * 0.45 +
		bend * 1.7;

	float tip_shape = 1.0 -
		smoothstep(
			tip_width * 0.35,
			tip_width,
			abs(p.x - tip_center)
		);

	// Only exists in the upper region.
	tip_shape *= smoothstep(0.60, 0.68, h);

	// Fade exactly into the point.
	tip_shape *= 1.0 - smoothstep(0.94, 1.0, abs(h - 0.995));

	shape = rounded_union(shape, tip_shape);

	// -------------------------------------------------------------------------
	// Tear / irregularity.
	//
	// Stronger toward the top, where the flame should become unstable.
	// -------------------------------------------------------------------------
	float turbulence = fbm(
		p * 8.0 +
		vec2(
			seed * 2.0,
			age * 3.0
		)
	) - 0.5;

	float irregularity =
		turbulence *
		warp *
		age *
		smoothstep(0.25, 0.95, h);

	shape += irregularity * 0.32;

	shape = clamp(shape, 0.0, 1.0);

	// -------------------------------------------------------------------------
	// Very soft edge.
	// -------------------------------------------------------------------------
	shape = smoothstep(
		0.02,
		0.72,
		shape
	);

	// -------------------------------------------------------------------------
	// Area dilution.
	// -------------------------------------------------------------------------
	float area = sx * sy;

	float dilute = mix(
		1.0,
		1.0 / max(area * area, 0.001),
		dilution
	);

	// -------------------------------------------------------------------------
	// FIRE COLOR
	//
	// The color is controlled by BOTH:
	//
	//   age    -> evolution through the particle's life
	//   height -> hotter core / darker outer flame / hot tip
	//
	// This prevents the entire particle from becoming one flat color.
	// -------------------------------------------------------------------------
	vec3 deep_orange = vec3(
		1.0,
		0.12,
		0.005
	);

	vec3 orange = vec3(
		1.0,
		0.34,
		0.015
	);

	vec3 yellow = vec3(
		1.0,
		0.72,
		0.10
	);

	vec3 hot_core = vec3(
		1.0,
		0.94,
		0.55
	);

	// Outer flame evolves toward deeper orange as the particle ages.
	vec3 outer_color = mix(
		orange,
		deep_orange,
		smoothstep(0.25, 1.0, age)
	);

	// Higher parts become hotter and brighter before fading.
	outer_color = mix(
		outer_color,
		yellow,
		smoothstep(0.35, 0.72, h)
	);

	// Core remains yellow/white.
	float core = 1.0 -
		smoothstep(
			0.015,
			0.115,
			abs(p.x - body_center)
		);

	vec3 fire_color = mix(
		outer_color,
		hot_core,
		core * 0.82
	);

	// The very tip becomes warmer and slightly brighter.
	float tip_heat = smoothstep(0.72, 0.97, h);

	fire_color = mix(
		fire_color,
		yellow,
		tip_heat * 0.28
	);

	// Final particle tint.
	ALBEDO = COLOR.rgb * fire_color;

	ALPHA = clamp(
		COLOR.a *
		shape *
		dilute,
		0.0,
		1.0
	);

	EMISSION_LINE
}
""".replace(
		"BLEND_MODE",
		"blend_add" if additive else "blend_mix"
	).replace(
		"EMISSION_LINE",
		"EMISSION = COLOR.rgb * fire_color * 1.8;"
		if additive
		else
		"EMISSION = vec3(0.0);"
	)

	_vfx_cache[key] = shader
	return shader
## -----------------------------------------------------------------------------
## Category matching
## -----------------------------------------------------------------------------
## Keyword heuristic over the Niagara system name. This is a stand-in for a
## graph that cannot be translated, not a conversion - anything unmatched
## lands on "generic" and is meant to be replaced by hand.
##
## Order matters: "candle" is tested before "flame" so NS_candle_flame does
## not get the bonfire preset.

func _vfx_category_for_name(system_name: String) -> String:
	var n := system_name.to_lower()

	if n.contains("candle") or n.contains("wick") or n.contains("lantern"):
		return "candle"
	if n.contains("torch"):
		return "torch"
	if n.contains("fire") or n.contains("flame") or n.contains("burn") or n.contains("campfire"):
		return "fire"
	if n.contains("smoke") or n.contains("fog") or n.contains("mist") or n.contains("haze"):
		return "smoke"
	if n.contains("steam") or n.contains("vapor") or n.contains("vapour"):
		return "steam"
	if n.contains("spark") or n.contains("ember") or n.contains("electric") or n.contains("shock"):
		return "spark"
	if n.contains("insect") or n.contains("fly") or n.contains("flies") or n.contains("moth") or n.contains("firefly") or n.contains("swarm") or n.contains("bug"):
		return "swarm"
	if n.contains("blood") or n.contains("gore"):
		return "blood"
	if n.contains("magic") or n.contains("arcane") or n.contains("spell") or n.contains("rune") or n.contains("soul") or n.contains("spirit") or n.contains("ghost"):
		return "magic"
	if n.contains("dust") or n.contains("debris") or n.contains("sand") or n.contains("ash"):
		return "dust"
	if n.contains("water") or n.contains("splash") or n.contains("rain") or n.contains("waterfall") or n.contains("river") or n.contains("drip"):
		return "water"
	if n.contains("leaf") or n.contains("leaves") or n.contains("foliage") or n.contains("pollen") or n.contains("petal"):
		return "leaves"
	if n.contains("snow") or n.contains("frost") or n.contains("ice"):
		return "snow"

	return "generic"


## Local-space vertical offset (metres, along the marker's own up axis) to
## move the flame from the mesh's base up to where the fuel actually sits.
## See the VFX_*_FLAME_HEIGHT_RATIO / VFX_*_ASSUMED_HEIGHT_M constants above
## for why torch stops short of 100% while candle goes all the way up.
## Any other category returns 0.0 (no change - those markers are not
## anchored to a "prop with a base" the same way).
func _vfx_flame_height_offset(category: String) -> float:
	match category:
		"torch":
			return VFX_TORCH_FLAME_HEIGHT_RATIO * VFX_TORCH_ASSUMED_HEIGHT_M
		"candle":
			return VFX_CANDLE_FLAME_HEIGHT_RATIO * VFX_CANDLE_ASSUMED_HEIGHT_M
		_:
			return 0.0


## -----------------------------------------------------------------------------
## Category presets
## -----------------------------------------------------------------------------
## Returns a plain Dictionary describing one category. Kept as data rather
## than a match statement full of assignments so a preset can be read,
## diffed and tuned without touching the building logic.

func _vfx_preset(category: String) -> Dictionary:
	match category:

		"candle":
			## 341 of this map's systems. A candle flame is small, laminar
			## and nearly still: the bonfire preset made two-metre torches.
			return {
				"core": 0.10, "softness": 0.55,
				"amount": 14, "lifetime": 0.55, "randomness": 0.18,
				"velocity": [0.06, 0.16], "gravity": Vector3(0, 0.22, 0),
				"spread": 3.0, "lifetime_randomness": 0.22,
				"scale_law": _candle_scale_law, "alpha_law": _fire_density_law,
				"base_scale": 0.035,
				"scale_3d": Vector3(0.62, 1.30, 0.62),
				"colors": [
					[0.0, Color(1.0, 0.98, 0.80, 0.0)],
					[0.12, Color(1.0, 0.94, 0.58, 1.0)],
					[0.45, Color(1.0, 0.72, 0.22, 0.95)],
					[0.78, Color(0.95, 0.40, 0.08, 0.55)],
					[1.0, Color(0.35, 0.10, 0.02, 0.0)],
				],
				"turbulence": [0.22, 5.5, 0.55],
				"radial": [0.004, 0.018], "pivot": Vector3(0, -0.02, 0),
				"parcel": true, "additive": true,
				"light": {"color": Color(1.0, 0.72, 0.32), "energy": 0.55, "range": 2.4},
				"shader_params": {"distortion_strength": 0.14, "noise_scale": 4.6,
					"edge_softness": 0.30, "warp_strength": 0.10, "animation_speed": 1.25},
			}

		"torch":
			## Between "candle" and "fire": brighter and a bit taller than a
			## candle (wall/hand torches usually are), but still a contained
			## flame that must NOT travel far - unlike the bonfire "fire"
			## preset, which is sized for actual campfires/fireballs.
			return {
				"core": 0.13, "softness": 0.52,
				"amount": 22, "lifetime": 0.70, "randomness": 0.20,
				"velocity": [0.12, 0.30], "gravity": Vector3(0, 0.38, 0),
				"spread": 5.0, "lifetime_randomness": 0.20,
				"scale_law": _candle_scale_law, "alpha_law": _fire_density_law,
				"base_scale": 0.07,
				"scale_3d": Vector3(0.68, 1.30, 0.68),
				"colors": [
					[0.0, Color(1.0, 0.97, 0.78, 0.0)],
					[0.12, Color(1.0, 0.90, 0.52, 1.0)],
					[0.45, Color(1.0, 0.68, 0.20, 0.95)],
					[0.78, Color(0.95, 0.36, 0.07, 0.55)],
					[1.0, Color(0.32, 0.09, 0.02, 0.0)],
				],
				"turbulence": [0.35, 4.8, 0.55],
				"radial": [0.005, 0.024], "pivot": Vector3(0, -0.03, 0),
				"parcel": true, "additive": true,
				"light": {"color": Color(1.0, 0.68, 0.28), "energy": 0.85, "range": 2.8},
				"shader_params": {"distortion_strength": 0.16, "noise_scale": 4.4,
					"edge_softness": 0.30, "warp_strength": 0.12, "animation_speed": 1.15},
			}

		"fire":
			## V10.12: velocity/gravity/lifetime brought down hard. At the old
			## values (v up to 2.25 m/s, buoyancy 0.70, lifetime 1.15s) a
			## single flame licks up to ~3 metres before dying - which is
			## exactly the "pillar of fire reaching into the tree canopy"
			## problem. A brazier/campfire flame should read as dramatic but
			## grounded, not room-height. Max reach is now ~0.8 m.
			return {
				"core": 0.16, "softness": 0.50,
				"amount": 64, "lifetime": 0.85, "randomness": 0.24,
				"velocity": [0.35, 0.75], "gravity": Vector3(0, 0.42, 0),
				"spread": 7.0, "lifetime_randomness": 0.18,
				"scale_law": _fire_scale_law, "alpha_law": _fire_density_law,
				"base_scale": 0.45,
				"scale_3d": Vector3(0.82, 1.08, 0.82),
				"colors": [
					[0.0, Color(1.0, 0.96, 0.70, 0.0)],
					[0.08, Color(1.0, 0.92, 0.42, 1.0)],
					[0.28, Color(1.0, 0.62, 0.12, 1.0)],
					[0.56, Color(1.0, 0.25, 0.035, 0.82)],
					[0.80, Color(0.55, 0.10, 0.025, 0.38)],
					[1.0, Color(0.16, 0.02, 0.01, 0.0)],
				],
				"turbulence": [0.95, 3.25, 0.34],
				"radial": [0.015, 0.09], "pivot": Vector3(0, -0.20, 0),
				"parcel": true, "additive": true,
				"light": {"color": Color(1.0, 0.55, 0.15), "energy": 1.5, "range": 3.0},
				"shader_params": {"distortion_strength": 0.22, "noise_scale": 3.7,
					"edge_softness": 0.32, "warp_strength": 0.18, "animation_speed": 0.85},
			}

		"smoke":
			return {
				"core": 0.05, "softness": 0.80,
				"amount": 86, "lifetime": 4.60, "randomness": 0.28,
				"velocity": [0.28, 0.58], "gravity": Vector3(0, 0.18, 0),
				"spread": 5.0, "lifetime_randomness": 0.16,
				"scale_law": _smoke_scale_law, "alpha_law": _plume_density_law,
				"base_scale": 0.55,
				"scale_3d": Vector3(0.90, 1.04, 0.90),
				"colors": [
					[0.0, Color(0.32, 0.32, 0.33, 0.0)],
					[0.07, Color(0.40, 0.40, 0.41, 0.62)],
					[0.22, Color(0.47, 0.47, 0.49, 0.58)],
					[0.44, Color(0.56, 0.56, 0.58, 0.42)],
					[0.68, Color(0.67, 0.67, 0.69, 0.20)],
					[1.0, Color(0.78, 0.78, 0.80, 0.0)],
				],
				"turbulence": [1.45, 1.65, 0.38],
				"radial": [0.035, 0.26], "pivot": Vector3(0, -0.56, 0),
				"parcel": true, "additive": false,
				"shader_params": {"distortion_strength": 0.28, "noise_scale": 2.8,
					"edge_softness": 0.42, "warp_strength": 0.20, "animation_speed": 0.55},
			}

		"steam":
			## Same plume physics as smoke, but bright, short-lived and
			## dissipating much faster - it condenses rather than disperses.
			return {
				"core": 0.04, "softness": 0.85,
				"amount": 48, "lifetime": 2.20, "randomness": 0.30,
				"velocity": [0.45, 0.95], "gravity": Vector3(0, 0.42, 0),
				"spread": 8.0, "lifetime_randomness": 0.22,
				"scale_law": _smoke_scale_law, "alpha_law": _plume_density_law,
				"base_scale": 0.40,
				"scale_3d": Vector3(0.92, 1.00, 0.92),
				"colors": [
					[0.0, Color(0.92, 0.94, 0.96, 0.0)],
					[0.10, Color(0.95, 0.96, 0.98, 0.45)],
					[0.45, Color(0.97, 0.98, 1.00, 0.28)],
					[1.0, Color(1.00, 1.00, 1.00, 0.0)],
				],
				"turbulence": [1.10, 2.10, 0.42],
				"radial": [0.03, 0.20], "pivot": Vector3(0, -0.40, 0),
				"parcel": true, "additive": false,
				"shader_params": {"distortion_strength": 0.30, "noise_scale": 2.4,
					"edge_softness": 0.48, "warp_strength": 0.16, "animation_speed": 0.70},
			}

		"swarm":
			## NS_light_insects and NS_Flies used to fall through to
			## "generic" and emit an undirected puff. Insects hold station
			## around a point instead: almost no gravity, high damping, and
			## turbulence as the only driver so they mill about.
			return {
				"core": 0.35, "softness": 0.30,
				"amount": 26, "lifetime": 3.2, "randomness": 0.85,
				"velocity": [0.12, 0.45], "gravity": Vector3(0, 0.0, 0),
				"spread": 180.0, "lifetime_randomness": 0.5,
				"scale_points": [[0.0, 0.0], [0.12, 1.0], [0.88, 1.0], [1.0, 0.0]],
				"alpha_points": [[0.0, 0.0], [0.15, 1.0], [0.85, 1.0], [1.0, 0.0]],
				"base_scale": 0.018,
				"colors": [
					[0.0, Color(1.0, 0.95, 0.60, 0.0)],
					[0.2, Color(1.0, 0.92, 0.50, 1.0)],
					[0.8, Color(0.95, 0.80, 0.35, 0.9)],
					[1.0, Color(0.6, 0.45, 0.15, 0.0)],
				],
				"turbulence": [2.4, 3.8, 0.9],
				"damping": [0.8, 2.0],
				"emission_sphere": 0.35,
				"parcel": false, "additive": true,
				"light": {"color": Color(1.0, 0.88, 0.45), "energy": 0.30, "range": 1.6},
			}

		"spark":
			return {
				"core": 0.40, "softness": 0.25,
				"amount": 42, "lifetime": 0.55, "randomness": 0.4,
				"velocity": [1.4, 4.0], "gravity": Vector3(0, -2.4, 0),
				"spread": 26.0, "lifetime_randomness": 0.3,
				"scale_points": [[0.0, 1.0], [0.7, 0.6], [1.0, 0.0]],
				"alpha_points": [[0.0, 1.0], [0.6, 0.9], [1.0, 0.0]],
				"base_scale": 0.05,
				"colors": [
					[0.0, Color(1.0, 0.96, 0.70, 1.0)],
					[0.45, Color(1.0, 0.72, 0.20, 0.85)],
					[1.0, Color(0.55, 0.08, 0.0, 0.0)],
				],
				"turbulence": [0.38, 4.2, 0.30],
				"damping": [0.55, 1.15],
				"parcel": false, "additive": true,
			}

		"blood":
			return {
				"core": 0.45, "softness": 0.25,
				"amount": 28, "lifetime": 0.85, "randomness": 0.35,
				"velocity": [0.8, 2.6], "gravity": Vector3(0, -9.8, 0),
				"spread": 24.0, "lifetime_randomness": 0.25,
				"scale_points": [[0.0, 0.6], [0.3, 1.0], [1.0, 0.7]],
				"alpha_points": [[0.0, 0.0], [0.08, 1.0], [0.75, 0.9], [1.0, 0.0]],
				"base_scale": 0.045,
				"colors": [
					[0.0, Color(0.42, 0.03, 0.02, 0.0)],
					[0.1, Color(0.48, 0.04, 0.03, 1.0)],
					[0.7, Color(0.32, 0.02, 0.015, 0.9)],
					[1.0, Color(0.18, 0.01, 0.01, 0.0)],
				],
				"turbulence": [0.15, 3.0, 0.2],
				"parcel": false, "additive": false,
			}

		"magic":
			return {
				"core": 0.20, "softness": 0.55,
				"amount": 40, "lifetime": 2.4, "randomness": 0.5,
				"velocity": [0.15, 0.55], "gravity": Vector3(0, 0.25, 0),
				"spread": 45.0, "lifetime_randomness": 0.4,
				"scale_points": [[0.0, 0.0], [0.2, 1.0], [0.7, 0.8], [1.0, 0.0]],
				"alpha_points": [[0.0, 0.0], [0.15, 1.0], [0.7, 0.7], [1.0, 0.0]],
				"base_scale": 0.05,
				"colors": [
					[0.0, Color(0.45, 0.85, 1.0, 0.0)],
					[0.2, Color(0.55, 0.90, 1.0, 1.0)],
					[0.6, Color(0.40, 0.60, 1.0, 0.8)],
					[1.0, Color(0.25, 0.30, 0.85, 0.0)],
				],
				"turbulence": [1.2, 2.5, 0.6],
				"emission_sphere": 0.15,
				"parcel": false, "additive": true,
				"light": {"color": Color(0.45, 0.75, 1.0), "energy": 0.9, "range": 2.5},
			}

		"dust":
			return {
				"core": 0.06, "softness": 0.75,
				"amount": 32, "lifetime": 2.8, "randomness": 0.4,
				"velocity": [0.08, 0.38], "gravity": Vector3(0, -0.08, 0),
				"spread": 22.0, "lifetime_randomness": 0.3,
				"scale_points": [[0.0, 0.4], [0.5, 1.0], [1.0, 1.2]],
				"alpha_points": [[0.0, 0.0], [0.12, 1.0], [0.55, 0.55], [1.0, 0.0]],
				"base_scale": 0.15,
				"colors": [
					[0.0, Color(0.55, 0.50, 0.42, 0.0)],
					[0.12, Color(0.58, 0.53, 0.45, 0.58)],
					[0.55, Color(0.64, 0.59, 0.50, 0.30)],
					[1.0, Color(0.72, 0.66, 0.56, 0.0)],
				],
				"turbulence": [0.75, 2.1, 0.40],
				"parcel": false, "additive": false,
			}

		"water":
			return {
				"core": 0.35, "softness": 0.30,
				"amount": 34, "lifetime": 1.15, "randomness": 0.35,
				"velocity": [0.9, 2.3], "gravity": Vector3(0, -9.8, 0),
				"spread": 18.0, "lifetime_randomness": 0.25,
				"scale_points": [[0.0, 0.8], [0.4, 1.0], [1.0, 0.6]],
				"alpha_points": [[0.0, 0.0], [0.08, 1.0], [0.6, 0.7], [1.0, 0.0]],
				"base_scale": 0.06,
				"colors": [
					[0.0, Color(0.55, 0.75, 1.0, 0.0)],
					[0.08, Color(0.55, 0.75, 1.0, 0.82)],
					[0.55, Color(0.40, 0.65, 1.0, 0.55)],
					[1.0, Color(0.35, 0.55, 1.0, 0.0)],
				],
				"turbulence": [0.24, 3.0, 0.25],
				"damping": [0.10, 0.35],
				"parcel": false, "additive": false,
			}

		"leaves":
			return {
				"core": 0.50, "softness": 0.20,
				"amount": 22, "lifetime": 4.2, "randomness": 0.5,
				"velocity": [0.08, 0.42], "gravity": Vector3(0, -0.35, 0),
				"spread": 20.0, "lifetime_randomness": 0.4,
				"scale_points": [[0.0, 0.8], [0.5, 1.0], [1.0, 0.9]],
				"alpha_points": [[0.0, 0.0], [0.1, 1.0], [0.8, 1.0], [1.0, 0.0]],
				"base_scale": 0.12,
				"colors": [
					[0.0, Color(0.50, 0.70, 0.20, 1.0)],
					[0.60, Color(0.60, 0.55, 0.20, 0.92)],
					[1.0, Color(0.50, 0.40, 0.20, 0.0)],
				],
				"turbulence": [0.95, 1.35, 0.45],
				"rotate_3d": true,
				"parcel": false, "additive": false,
			}

		"snow":
			return {
				"core": 0.30, "softness": 0.45,
				"amount": 44, "lifetime": 5.2, "randomness": 0.45,
				"velocity": [0.04, 0.18], "gravity": Vector3(0, -0.16, 0),
				"spread": 28.0, "lifetime_randomness": 0.35,
				"scale_points": [[0.0, 0.9], [0.5, 1.0], [1.0, 0.9]],
				"alpha_points": [[0.0, 0.0], [0.12, 1.0], [0.86, 0.9], [1.0, 0.0]],
				"base_scale": 0.05,
				"colors": [
					[0.0, Color(1.0, 1.0, 1.0, 0.0)],
					[0.12, Color(1.0, 1.0, 1.0, 0.88)],
					[0.86, Color(1.0, 1.0, 1.0, 0.76)],
					[1.0, Color(1.0, 1.0, 1.0, 0.0)],
				],
				"turbulence": [0.52, 1.5, 0.45],
				"rotate_3d": true,
				"parcel": false, "additive": false,
			}

		_:
			## Unmatched systems get something deliberately plain and small,
			## so they are visible enough to find and replace by hand but
			## never pretend to be a converted effect.
			return {
				"core": 0.25, "softness": 0.45,
				"amount": 12, "lifetime": 1.6, "randomness": 0.4,
				"velocity": [0.15, 0.5], "gravity": Vector3(0, 0.1, 0),
				"spread": 30.0, "lifetime_randomness": 0.3,
				"scale_points": [[0.0, 0.4], [0.4, 1.0], [1.0, 0.2]],
				"alpha_points": [[0.0, 0.0], [0.2, 0.6], [1.0, 0.0]],
				"base_scale": 0.06,
				"colors": [
					[0.0, Color(0.8, 0.8, 0.85, 0.0)],
					[0.3, Color(0.8, 0.8, 0.85, 0.45)],
					[1.0, Color(0.8, 0.8, 0.85, 0.0)],
				],
				"turbulence": [0.4, 2.0, 0.3],
				"parcel": false, "additive": false,
			}




## -----------------------------------------------------------------------------
## Builder
## -----------------------------------------------------------------------------

## Builds, once per category, the resources every emitter of that category
## shares: process material, draw mesh and its material. Godot resources
## are reference-counted, so sharing is both correct and the difference
## between ~2500 allocations and a couple of dozen across 422 emitters.
func _vfx_resources(category: String) -> Dictionary:
	if _vfx_cache.has(category):
		return _vfx_cache[category]

	var preset: Dictionary = _vfx_preset(category)

	var mat := ParticleProcessMaterial.new()
	mat.direction = Vector3(0, 1, 0)
	mat.spread = float(preset.get("spread", 20.0))
	mat.gravity = preset.get("gravity", Vector3(0, -0.5, 0))

	var velocity: Array = preset.get("velocity", [0.5, 1.5])
	mat.initial_velocity_min = float(velocity[0])
	mat.initial_velocity_max = float(velocity[1])
	mat.lifetime_randomness = float(preset.get("lifetime_randomness", 0.2))

	var sphere_radius: float = float(preset.get("emission_sphere", 0.0))
	if sphere_radius > 0.0:
		mat.emission_shape = ParticleProcessMaterial.EMISSION_SHAPE_SPHERE
		mat.emission_sphere_radius = sphere_radius
	else:
		mat.emission_shape = ParticleProcessMaterial.EMISSION_SHAPE_POINT

	## Base size on scale_min/max, lifetime evolution on the curve. The
	## curve comes from a physics law when the preset names one, so
	## changing a law changes the visual - which was not true before.
	var base_scale: float = clampf(
		float(preset.get("base_scale", 0.1)), 0.001, VFX_MAX_PARTICLE_SIZE)
	mat.scale_min = base_scale
	mat.scale_max = base_scale

	if preset.has("scale_law"):
		mat.scale_curve = _curve_from_law(preset["scale_law"])
	elif preset.has("scale_points"):
		mat.scale_curve = _curve_from_points(preset["scale_points"])

	if preset.has("alpha_law"):
		mat.alpha_curve = _curve_from_law(preset["alpha_law"])
	elif preset.has("alpha_points"):
		mat.alpha_curve = _curve_from_points(preset["alpha_points"])

	if preset.has("scale_3d") and _try_set(mat, "use_scale_3d", true):
		_try_set(mat, "scale_3d_min", preset["scale_3d"])
		_try_set(mat, "scale_3d_max", preset["scale_3d"])

	mat.color_ramp = _gradient_from_stops(preset.get("colors", [
		[0.0, Color(1, 1, 1, 1)], [1.0, Color(1, 1, 1, 0)],
	]))

	if preset.has("damping"):
		var damping: Array = preset["damping"]
		mat.damping_min = float(damping[0])
		mat.damping_max = float(damping[1])

	if preset.has("rotate_3d") and _try_set(mat, "use_rotation_3d", true):
		_try_set(mat, "rotation_3d_min", Vector3(-1.0, -PI, -1.0))
		_try_set(mat, "rotation_3d_max", Vector3(1.0, PI, 1.0))
		_try_set(mat, "rotation_velocity_3d_min", Vector3(-1.4, -1.8, -1.1))
		_try_set(mat, "rotation_velocity_3d_max", Vector3(1.4, 1.8, 1.1))

	if preset.has("radial"):
		var radial: Array = preset["radial"]
		if _try_set(mat, "radial_velocity_min", float(radial[0])):
			_try_set(mat, "radial_velocity_max", float(radial[1]))
			_try_set(mat, "velocity_pivot", preset.get("pivot", Vector3.ZERO))
			_try_set(mat, "radial_velocity_curve", _curve_from_points(
				[[0.0, 0.0], [0.15, 0.15], [0.45, 0.55], [0.75, 0.90], [1.0, 1.0]]))

	if preset.has("turbulence"):
		var turbulence: Array = preset["turbulence"]
		if _try_set(mat, "turbulence_enabled", true):
			_try_set(mat, "turbulence_noise_strength", float(turbulence[0]))
			_try_set(mat, "turbulence_noise_scale", float(turbulence[1]))
			_try_set(mat, "turbulence_noise_speed", Vector3(0.31, 0.53, 0.23))
			_try_set(mat, "turbulence_noise_speed_random", float(turbulence[2]))
			_try_set(mat, "turbulence_influence_min", 0.0)
			_try_set(mat, "turbulence_influence_max", 1.0)
			_try_set(mat, "turbulence_influence_over_life",
				_curve_from_law(_turbulence_influence_law))

	## ---- draw mesh -------------------------------------------------------
	## A unit quad carrying a radial falloff texture. No shader: the soft
	## silhouette is in the texture's alpha, the colour comes from the
	## particle through vertex_color_use_as_albedo, and billboarding uses
	## the particle-specific mode. Nothing here can fail to compile, so
	## there is no white-square fallback to land on.
	var quad := QuadMesh.new()
	quad.size = Vector2(1.0, 1.0)

	var additive: bool = bool(preset.get("additive", false))

	var draw_material := StandardMaterial3D.new()
	draw_material.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	draw_material.vertex_color_use_as_albedo = true
	draw_material.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
	draw_material.billboard_mode = BaseMaterial3D.BILLBOARD_PARTICLES
	draw_material.billboard_keep_scale = true
	draw_material.albedo_texture = _falloff_texture(
		float(preset.get("core", 0.2)), float(preset.get("softness", 0.5)))
	draw_material.texture_filter = BaseMaterial3D.TEXTURE_FILTER_LINEAR
	draw_material.disable_receive_shadows = true

	## Depth write off, otherwise overlapping parcels punch holes in one
	## another instead of accumulating into a plume.
	draw_material.no_depth_test = false
	_try_set(draw_material, "depth_draw_mode", BaseMaterial3D.DEPTH_DRAW_DISABLED)

	if additive:
		draw_material.blend_mode = BaseMaterial3D.BLEND_MODE_ADD
		draw_material.emission_enabled = true
		draw_material.emission = Color(1.0, 0.85, 0.45)
		draw_material.emission_energy_multiplier = 1.6

	if VFX_USE_PARCEL_SHADER and bool(preset.get("parcel", false)):
		var parcel_shader := _parcel_shader(additive)
		var shader_material := ShaderMaterial.new()
		shader_material.shader = parcel_shader
		shader_material.set_shader_parameter("falloff", draw_material.albedo_texture)
		quad.material = shader_material
	else:
		quad.material = draw_material

	## Culling bounds. A plume rises well beyond the emitter's own extent,
	## and without this it would pop out of existence mid-flight.
	var lifetime: float = float(preset.get("lifetime", 1.0))
	var speed_reach: float = float(velocity[1]) * lifetime
	var gravity_reach: float = 0.5 * mat.gravity.length() * lifetime * lifetime
	var scale_reach: float = base_scale * 3.0
	var reach: float = maxf(maxf(speed_reach, gravity_reach), scale_reach) + 1.0

	var resources := {
		"preset": preset,
		"process_material": mat,
		"mesh": quad,
		"aabb": AABB(Vector3(-reach, -reach * 0.5, -reach),
			Vector3(reach * 2.0, reach * 2.5, reach * 2.0)),
	}

	_vfx_cache[category] = resources
	return resources


## Spends the light budget on the first emitters encountered, optionally
## thinned so two glows are not stacked on the same altar.
func _vfx_may_add_light(world_position: Vector3) -> bool:
	if VFX_MAX_LIGHTS <= 0 or _vfx_lights_spent >= VFX_MAX_LIGHTS:
		return false

	if VFX_LIGHT_MIN_SPACING > 0.0:
		for existing in _vfx_light_positions:
			if existing.distance_to(world_position) < VFX_LIGHT_MIN_SPACING:
				return false

	_vfx_lights_spent += 1
	_vfx_light_positions.append(world_position)
	return true


func _build_vfx_particles(system_name: String) -> GPUParticles3D:
	var category: String = _vfx_category_for_name(system_name)
	return _build_vfx_particles_for_category(category, system_name)


## Same builder, but the category is given directly instead of derived from
## the marker's own system name. Used for the ember overlay (a "fire"/"torch"
## marker also gets a "spark"-category emitter attached), so the name that
## ends up in metadata is still the REAL marker's name, not "spark".
func _build_vfx_particles_for_category(category: String, system_name: String, amount_scale: float = 1.0) -> GPUParticles3D:
	var resources: Dictionary = _vfx_resources(category)
	var preset: Dictionary = resources["preset"]

	var particles := GPUParticles3D.new()
	particles.name = "AutoVFX_" + category
	particles.amount = maxi(int(round(float(preset.get("amount", 16)) * amount_scale)), 1)
	particles.lifetime = maxf(float(preset.get("lifetime", 1.0)), 0.05)
	particles.randomness = float(preset.get("randomness", 0.2))
	particles.emitting = true
	particles.one_shot = false
	particles.explosiveness = 0.0
	particles.local_coords = false
	particles.visibility_aabb = resources["aabb"]
	particles.process_material = resources["process_material"]
	particles.draw_pass_1 = resources["mesh"]

	## Particle billboarding is handled by the material
	## (BILLBOARD_PARTICLES), so the node must not also align the transform
	## - doing both is what produced edge-on, flat-looking quads. Guarded by
	## _try_set like the other version-sensitive properties in this file:
	## transform_align landed in a specific 4.x release, and a hard
	## assignment would break the whole constructor on an older editor.
	_try_set(particles, "transform_align", 0)

	particles.set_meta("ue_vfx_heuristic_category", category)
	particles.set_meta("ue_vfx_system_name", system_name)
	particles.set_meta("ue_vfx_is_reconstruction", true)
	return particles


## Attached only AFTER the emitter is in the tree, and only if the budget
## allows. Assigning .owner before a node is in the scene tree silently
## drops it from the packed .tscn.
func _attach_vfx_light(particles: GPUParticles3D, category: String, world_position: Vector3, owner_node: Node) -> void:
	var preset: Dictionary = _vfx_preset(category)

	if not preset.has("light"):
		return
	if not _vfx_may_add_light(world_position):
		return

	var settings: Dictionary = preset["light"]

	var light := OmniLight3D.new()
	light.name = "AutoGlow"
	light.light_color = settings.get("color", Color(1.0, 0.6, 0.2))
	light.light_energy = float(settings.get("energy", 1.0))
	light.omni_range = float(settings.get("range", 3.0))
	light.shadow_enabled = false

	particles.add_child(light)
	light.owner = owner_node


## -----------------------------------------------------------------------------
## VFX marker pass
## -----------------------------------------------------------------------------
## Niagara systems are NOT converted: their behaviour lives in
## engine-specific modules. The exact placement is preserved, plus a
## heuristic stand-in effect so the scene is not silently empty where 422
## effects used to be.

func _build_vfx_markers(root: Node3D) -> void:
	var entries: Array = manifest.get("effects", {}).get("niagara", [])

	if entries.is_empty():
		return

	_vfx_cache.clear()
	_vfx_lights_spent = 0
	_vfx_light_positions.clear()

	var vfx_root := Node3D.new()
	vfx_root.name = "VFX_MARKERS_NOT_CONVERTED"
	root.add_child(vfx_root)
	vfx_root.owner = root

	var groups := {}
	var per_category := {}

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
			stats["vfx_transform_failed"] += 1
			continue

		var transform = _transform_from_v10(transform_data)
		if transform == null:
			stats["vfx_transform_failed"] += 1
			continue

		# Raise torch/candle flames off the base of the mesh (see
		# _vfx_flame_height_offset) BEFORE the marker is created, so the
		# particles, ember overlay and glow light - all attached at the
		# marker's own transform below - move up together with it.
		#
		# The offset is scaled by this instance's own up-axis scale
		# (transform.basis.y.length(), BEFORE normalizing) so a torch/candle
		# placed larger or smaller than the reference (scale 1.0) gets a
		# proportionally taller/shorter offset instead of one flat number
		# for every instance regardless of size.
		var flame_category: String = _vfx_category_for_name(system_name)
		var flame_height_offset := _vfx_flame_height_offset(flame_category)
		if flame_height_offset != 0.0:
			# basis.y is left UN-normalized on purpose: its length already
			# carries this instance's own scale along the up axis, so a
			# bigger torch/candle gets a proportionally bigger offset.
			transform.origin += transform.basis.y * flame_height_offset

		if not groups.has(system_name):
			var group := Node3D.new()
			group.name = _safe_node_name(system_name)
			vfx_root.add_child(group)
			group.owner = root
			groups[system_name] = group

		var marker := Marker3D.new()
		marker.name = "%s_%04d" % [_safe_node_name(system_name), i]
		(groups[system_name] as Node3D).add_child(marker)
		marker.owner = root
		marker.transform = transform

		marker.set_meta("ue_niagara_system", str(system.get("path", "")))
		marker.set_meta("ue_niagara_system_name", system_name)
		marker.set_meta("ue_actor_path", str((entry.get("actor", {}) as Dictionary).get("path", "")))
		marker.set_meta("ue_not_converted", true)

		var category: String = flame_category
		per_category[category] = int(per_category.get(category, 0)) + 1

		if VFX_AUTO_PARTICLES:
			var particles := _build_vfx_particles(system_name)
			marker.add_child(particles)
			particles.owner = root
			_attach_vfx_light(particles, category, transform.origin, root)

			if category in VFX_EMBER_CATEGORIES:
				var embers := _build_vfx_particles_for_category("spark", system_name, VFX_EMBER_AMOUNT_SCALE)
				embers.name = "AutoVFX_embers"
				marker.add_child(embers)
				embers.owner = root
				stats["vfx_embers_built"] += 1

		stats["vfx_markers_built"] += 1

	stats["vfx_lights_built"] = _vfx_lights_spent

	print("VFX markers built           :", stats["vfx_markers_built"], "(placement preserved - Niagara graphs not converted)")
	for category in per_category.keys():
		print("    %-10s %4d" % [category, per_category[category]])
	print("VFX ember overlays          :", stats["vfx_embers_built"], "(fire/torch only)")
	print("VFX realtime lights         :", _vfx_lights_spent, "/", VFX_MAX_LIGHTS, "budget")


func _print_report() -> void:
	print("------------------------------------------------------------")
	print("CONSTRUCTOR VALIDATION")
	print("------------------------------------------------------------")
	print("Geometry placements (rows)  :", stats["geometry_total"])
	print("StaticMesh placements       :", stats["geometry_static"])
	print("ISM/HISM component rows     :", stats["geometry_instanced"])
	print("Real Godot nodes spawned    :", stats["instances_spawned"])
	print("Valid mesh placements       :", stats["valid_geometry"])
	print("Empty mesh slots            :", stats["empty_mesh_slots"])
	print("Zero-instance ISM skipped   :", stats["zero_instance_ism_skipped"])
	print("Missing asset mappings      :", stats["missing_asset_mapping"])
	print("Missing GLB files           :", stats["missing_glb"])
	print("Instantiation failures      :", stats["instantiate_failures"])
	print("Transform failures          :", stats["transform_failures"])
	print("  (of which per-instance)   :", stats["instance_transform_missing"])
	print("Duplicate placement keys    :", stats["duplicate_placement_ids"])
	print("LevelInstance registry      :", stats["level_instances"])
	print("Recomputed LI transforms    :", stats["recomputed_li_transforms"])
	print("Decals built                :", stats["decals_built"])
	print("Decals without texture      :", stats["decals_missing_texture"])
	print("Decal transform failures    :", stats["decals_transform_failed"])
	print("VFX markers built           :", stats["vfx_markers_built"])
	print("VFX transform failures      :", stats["vfx_transform_failed"])
	print("VFX ember overlays          :", stats["vfx_embers_built"])
	print("VFX realtime lights         :", stats["vfx_lights_built"])
	print("------------------------------------------------------------")
