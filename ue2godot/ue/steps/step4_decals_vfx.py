# -*- coding: utf-8 -*-
"""Step 4: Decals, VFX markers, and Audio markers Exporter."""

import os
import json
import time
from typing import Dict, Any
from ue2godot.core.config import ResolvedConfig
from ue2godot.core.report import StepReport


def run(cfg: ResolvedConfig) -> StepReport:
    started_at = time.strftime("%Y-%m-%d %H:%M:%S")
    start_time = time.time()

    enabled = cfg.get("decals.enabled", True)
    export_root = cfg.get("paths.ue_export_root", "C:/Export")
    manifest_path = os.path.join(export_root, "level_manifest_v10.json")
    decal_map_path = os.path.join(export_root, "GodotAssets", "ue5_godot_decal_map.json")
    godot_asset_root = cfg.get("paths.godot_asset_root", "res://UEAssets")

    errors = []
    warnings = []
    counters = {"decals": 0, "vfx": 0, "audio": 0}

    if not enabled:
        return StepReport(
            step="step4_decals_vfx", run_id=cfg.run_id, config_hash=cfg.config_hash,
            status="OK", started_at=started_at, duration_s=time.time() - start_time,
            warnings=["Decals/VFX export disabled in config."], counters=counters
        )

    if not os.path.isfile(manifest_path):
        return StepReport(
            step="step4_decals_vfx", run_id=cfg.run_id, config_hash=cfg.config_hash,
            status="FAILED", started_at=started_at, duration_s=time.time() - start_time,
            errors=[f"Manifest file not found: {manifest_path}"], counters=counters
        )

    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest_data = json.load(f)

    effects = manifest_data.get("effects", {})
    decals = effects.get("decals", [])
    niagara = effects.get("niagara", [])
    audio_list = manifest_data.get("world_features", {}).get("audio", [])

    counters["decals"] = len(decals)
    counters["vfx"] = len(niagara)
    counters["audio"] = len(audio_list)

    # Process decal materials mapping
    decal_materials: Dict[str, Dict[str, Any]] = {}
    for d in decals:
        mat_path = d.get("material_path") or d.get("material", "")
        if mat_path and mat_path not in decal_materials:
            mat_name = mat_path.rsplit(".", 1)[-1] if "." in mat_path else mat_path.rsplit("/", 1)[-1]
            png_name = f"{mat_name}.png"
            godot_tex_path = f"{godot_asset_root}/Decals/{png_name}"
            decal_materials[mat_path] = {
                "ue_material_path": mat_path,
                "name": mat_name,
                "godot_path": godot_tex_path,
                "tint": cfg.get("decals.tint_fallback", [1.0, 1.0, 1.0])
            }

    # Niagara grouping
    vfx_systems: Dict[str, int] = {}
    for n in niagara:
        sys_name = n.get("system_name") or n.get("name", "UnknownSystem")
        vfx_systems[sys_name] = vfx_systems.get(sys_name, 0) + 1

    audio_policy = cfg.get("audio.policy", "markers")
    audio_markers = []
    if audio_policy in ("markers", "export_with_fallback"):
        for a in audio_list:
            audio_markers.append({
                "component_path": a.get("component_path"),
                "sound_path": a.get("sound_path"),
                "transform": a.get("transform")
            })

    decal_map_payload = {
        "exporter_version": "1.0",
        "manifest_version": manifest_data.get("manifest_version", "10.0"),
        "godot_decal_root": f"{godot_asset_root}/Decals",
        "_resolved": {"run_id": cfg.run_id, "config_hash": cfg.config_hash},
        "decal_materials": decal_materials,
        "decal_placement_total": len(decals),
        "vfx": {
            "converted": False,
            "reason": "Niagara non convertible, placement seul",
            "systems": vfx_systems,
            "placement_total": len(niagara)
        },
        "audio": {
            "policy": audio_policy,
            "markers": audio_markers,
            "placement_total": len(audio_list)
        }
    }

    os.makedirs(os.path.dirname(decal_map_path), exist_ok=True)
    with open(decal_map_path, "w", encoding="utf-8") as f:
        json.dump(decal_map_payload, f, indent=2, ensure_ascii=False)

    outputs = [{"path": decal_map_path, "bytes": os.path.getsize(decal_map_path)}]

    return StepReport(
        step="step4_decals_vfx", run_id=cfg.run_id, config_hash=cfg.config_hash,
        status="OK", started_at=started_at, duration_s=time.time() - start_time,
        outputs=outputs, counters=counters, errors=errors, warnings=warnings
    )
