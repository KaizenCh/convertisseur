# addons/ue2godot/entry_headless.gd
extends SceneTree

func _init() -> void:
	var args := OS.get_cmdline_user_args()
	var cfg := {
		"manifest_path": "res://level_manifest_v10.json",
		"asset_map_path": "res://ue5_godot_asset_map.json",
		"decal_map_path": "res://ue5_godot_decal_map.json",
		"output_scene": "res://Map--_REBUILT.tscn",
		"report_path": ""
	}

	var i := 0
	while i < args.size():
		var arg := args[i] as String
		if arg == "--manifest" and i + 1 < args.size():
			cfg["manifest_path"] = args[i + 1]
			i += 1
		elif arg == "--asset-map" and i + 1 < args.size():
			cfg["asset_map_path"] = args[i + 1]
			i += 1
		elif arg == "--decal-map" and i + 1 < args.size():
			cfg["decal_map_path"] = args[i + 1]
			i += 1
		elif arg == "--output" and i + 1 < args.size():
			cfg["output_scene"] = args[i + 1]
			i += 1
		elif arg == "--report" and i + 1 < args.size():
			cfg["report_path"] = args[i + 1]
			i += 1
		i += 1

	print("[ue2godot Headless] Starting build...")
	var report := MapBuilder.build(cfg)
	print("[ue2godot Headless] Result: ", report.get("status"))

	var report_path: String = cfg.get("report_path", "")
	if report_path != "":
		var f := FileAccess.open(report_path, FileAccess.WRITE)
		if f != null:
			f.store_string(JSON.stringify(report, "\t"))
			f.close()

	if report.get("status") == "OK":
		quit(0)
	else:
		quit(1)
