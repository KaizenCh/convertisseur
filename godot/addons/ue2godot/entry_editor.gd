# addons/ue2godot/entry_editor.gd
@tool
extends EditorScript

func _run() -> void:
	print("[ue2godot EditorScript] Running MapBuilder...")
	var cfg := {
		"manifest_path": "res://level_manifest_v10.json",
		"asset_map_path": "res://ue5_godot_asset_map.json",
		"decal_map_path": "res://ue5_godot_decal_map.json",
		"output_scene": "res://Map--_REBUILT.tscn"
	}
	var report := MapBuilder.build(cfg)
	print("[ue2godot EditorScript] Result: ", report)
