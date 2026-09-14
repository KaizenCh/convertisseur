# -*- coding: utf-8 -*-
"""Step 3: Landscape Exporter (raycast + GLB writer + patch manifest/asset_map)."""

import os
import json
import time
from typing import Dict, Any
from ue2godot.core.config import ResolvedConfig
from ue2godot.core.report import StepReport
from ue2godot.ue.raycast import raycast_grid_heights
from ue2godot.core.glb import build_grid_mesh, write_glb

try:
    import unreal
except ImportError:
    unreal = None


LANDSCAPE_UE_PATH = "/AutoTerrain/Landscape.BakedLandscape"
LANDSCAPE_GLB_NAME = "BakedLandscape.glb"


def run(cfg: ResolvedConfig) -> StepReport:
    started_at = time.strftime("%Y-%m-%d %H:%M:%S")
    start_time = time.time()

    enabled = cfg.get("landscape.enabled", True)
    export_root = cfg.get("paths.ue_export_root", "C:/Export")
    manifest_path = os.path.join(export_root, "level_manifest_v10.json")
    asset_map_path = os.path.join(export_root, "GodotAssets", "ue5_godot_asset_map.json")
    mesh_output_dir = os.path.join(export_root, "GodotAssets", "Meshes")

    godot_asset_root = cfg.get("paths.godot_asset_root", "res://UEAssets")
    godot_mesh_root = f"{godot_asset_root}/Meshes"

    errors = []
    warnings = []
    counters = {"vertices": 0, "triangles": 0}

    if not enabled:
        return StepReport(
            step="step3_landscape", run_id=cfg.run_id, config_hash=cfg.config_hash,
            status="OK", started_at=started_at, duration_s=time.time() - start_time,
            warnings=["Landscape export disabled in config."], counters=counters
        )

    if unreal is None:
        return StepReport(
            step="step3_landscape", run_id=cfg.run_id, config_hash=cfg.config_hash,
            status="FAILED", started_at=started_at, duration_s=time.time() - start_time,
            errors=["Unreal Engine Python API is unavailable."], counters=counters
        )

    # Discover landscape actors
    actor_subsystem = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    actors = actor_subsystem.get_all_level_actors()
    landscapes = [a for a in actors if isinstance(a, (unreal.Landscape, unreal.LandscapeProxy))]

    if not landscapes:
        return StepReport(
            step="step3_landscape", run_id=cfg.run_id, config_hash=cfg.config_hash,
            status="OK", started_at=started_at, duration_s=time.time() - start_time,
            warnings=["No Landscape actor found in level."], counters=counters
        )

    # Auto-configure landscape material & layers from dedicated folder
    from ue2godot.ue.landscape_material_config import auto_configure_landscape_material
    mat_folder = cfg.get("landscape.material_folder", "/Game/LandscapeMaterials")
    mat_assigned = False
    for l in landscapes:
        mat_res = auto_configure_landscape_material(l, mat_folder)
        if mat_res.get("assigned_material"):
            mat_assigned = True
            if mat_res.get("auto_configured"):
                warnings.append(f"Landscape material auto-assigned: {mat_res['assigned_material']}")

    if not mat_assigned and cfg.get("landscape.require_assigned_material", True):
        warnings.append("Landscape has no custom material assigned (default checker material).")

    # Measure bounds
    min_x, max_x = float("inf"), float("-inf")
    min_y, max_y = float("inf"), float("-inf")
    min_z, max_z = float("inf"), float("-inf")

    for l in landscapes:
        try:
            origin, extent = l.get_actor_bounds(False)
            min_x = min(min_x, origin.x - extent.x)
            max_x = max(max_x, origin.x + extent.x)
            min_y = min(min_y, origin.y - extent.y)
            max_y = max(max_y, origin.y + extent.y)
            min_z = min(min_z, origin.z - extent.z)
            max_z = max(max_z, origin.z + extent.z)
        except Exception:
            pass

    if min_x == float("inf"):
        return StepReport(
            step="step3_landscape", run_id=cfg.run_id, config_hash=cfg.config_hash,
            status="FAILED", started_at=started_at, duration_s=time.time() - start_time,
            errors=["Could not determine landscape bounds."], counters=counters
        )

    resolution = cfg.get("landscape.grid_resolution", 256)
    world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()

    grid_heights = raycast_grid_heights(world, (min_x, max_x, min_y, max_y, min_z, max_z), resolution)

    # Build 2D grid representation
    side = resolution + 1
    grid_2d = []
    for iy in range(side):
        row = []
        for ix in range(side):
            row.append(grid_heights[ix * side + iy])
        grid_2d.append(row)

    positions, normals, uvs, indices = build_grid_mesh(grid_2d, (min_x, min_y, max_x, max_y))
    counters["vertices"] = len(positions) // 3
    counters["triangles"] = len(indices) // 3

    os.makedirs(mesh_output_dir, exist_ok=True)
    glb_disk_path = os.path.join(mesh_output_dir, LANDSCAPE_GLB_NAME).replace("\\", "/")
    godot_path = f"{godot_mesh_root}/{LANDSCAPE_GLB_NAME}"

    write_glb(glb_disk_path, positions, normals, uvs, indices)

    # Patch asset map
    if os.path.isfile(asset_map_path):
        with open(asset_map_path, "r", encoding="utf-8") as f:
            asset_map = json.load(f)
    else:
        asset_map = {"assets": {}}

    asset_map.setdefault("assets", {})[LANDSCAPE_UE_PATH] = {
        "ue_path": LANDSCAPE_UE_PATH, "ue_name": "BakedLandscape",
        "godot_path": godot_path, "disk_path": glb_disk_path,
        "format": "glb", "status": "EXPORTED"
    }

    with open(asset_map_path, "w", encoding="utf-8") as f:
        json.dump(asset_map, f, indent=2, ensure_ascii=False)

    # Patch manifest
    if os.path.isfile(manifest_path):
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)

        geometry = manifest.setdefault("geometry", {})
        placements = geometry.setdefault("placements", [])
        unique_meshes = geometry.setdefault("unique_meshes", {})

        unique_meshes[LANDSCAPE_UE_PATH] = {
            "path": LANDSCAPE_UE_PATH, "name": "BakedLandscape", "class": "StaticMesh"
        }

        placements.append({
            "kind": "static_mesh", "placement_id": "MESH_BAKED_LANDSCAPE",
            "actor": {"path": LANDSCAPE_UE_PATH, "name": "BakedLandscape", "class": "BakedLandscape"},
            "component": {"path": f"{LANDSCAPE_UE_PATH}.Mesh", "name": "BakedLandscapeMesh", "class": "StaticMeshComponent"},
            "mesh": {"path": LANDSCAPE_UE_PATH, "name": "BakedLandscape"},
            "source_transform": {"location": [0,0,0], "rotation": {"pitch":0,"yaw":0,"roll":0}, "scale": [1,1,1]},
            "final_world_transform": {"location": [0,0,0], "rotation": {"pitch":0,"yaw":0,"roll":0}, "scale": [1,1,1]},
            "reconstruction_transform": {"location": [0,0,0], "rotation": {"pitch":0,"yaw":0,"roll":0}, "scale": [1,1,1]}
        })

        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2, ensure_ascii=False)

    outputs = [{"path": glb_disk_path, "bytes": os.path.getsize(glb_disk_path)}]

    return StepReport(
        step="step3_landscape", run_id=cfg.run_id, config_hash=cfg.config_hash,
        status="OK", started_at=started_at, duration_s=time.time() - start_time,
        outputs=outputs, counters=counters, errors=errors, warnings=warnings
    )
