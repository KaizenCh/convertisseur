# -*- coding: utf-8 -*-
"""Step 1: Manifest Exporter."""

import os
import json
import time
from typing import Dict, Any
from ue2godot.core.config import ResolvedConfig
from ue2godot.core.report import StepReport
from ue2godot.ue.session import discover_level_actors
from ue2godot.ue.classify import classify_actor, classify_component
from ue2godot.ue.transforms import actor_transform, component_transform_diagnostic
from ue2godot.core.ids import object_path, object_name, class_name, stable_id

try:
    import unreal
except ImportError:
    unreal = None


def run(cfg: ResolvedConfig) -> StepReport:
    started_at = time.strftime("%Y-%m-%d %H:%M:%S")
    start_time = time.time()

    export_root = cfg.get("paths.ue_export_root", "C:/Export")
    manifest_path = os.path.join(export_root, "level_manifest_v10.json")
    os.makedirs(export_root, exist_ok=True)

    errors = []
    warnings = []
    counters = {
        "actors": 0, "placements": 0, "unique_meshes": 0,
        "unique_materials": 0, "level_instances": 0, "audio_components": 0
    }

    if unreal is None:
        return StepReport(
            step="step1_manifest", run_id=cfg.run_id, config_hash=cfg.config_hash,
            status="FAILED", started_at=started_at, duration_s=time.time() - start_time,
            errors=["Unreal Engine Python API is unavailable."], counters=counters
        )

    actors = discover_level_actors()
    counters["actors"] = len(actors)

    placements = []
    unique_meshes: Dict[str, Dict[str, Any]] = {}
    unique_materials: Dict[str, Dict[str, Any]] = {}
    audio_features = []

    actor_rules = cfg.get("classification.actor_rules", [])
    comp_rules = cfg.get("classification.component_rules", [])

    for actor in actors:
        a_path = object_path(actor)
        a_name = object_name(actor)
        a_class = class_name(actor)
        a_cat = classify_actor(actor, actor_rules)
        a_tf = actor_transform(actor)

        # Inspect components
        try:
            comps = actor.get_components_by_class(unreal.ActorComponent)
        except Exception:
            comps = []

        for comp in comps:
            c_kind = classify_component(comp, comp_rules)
            c_tf, err = component_transform_diagnostic(comp, actor)

            if c_kind == "static_mesh":
                mesh = None
                try:
                    mesh = comp.get_editor_property("static_mesh")
                except Exception:
                    pass
                if mesh is not None:
                    m_path = object_path(mesh)
                    m_name = object_name(mesh)
                    if m_path not in unique_meshes:
                        unique_meshes[m_path] = {
                            "path": m_path, "name": m_name, "class": class_name(mesh)
                        }

                    pid = stable_id("PLACEMENT", f"{a_path}_{comp.get_name()}")
                    placements.append({
                        "kind": "static_mesh",
                        "placement_id": pid,
                        "actor": {"path": a_path, "name": a_name, "class": a_class},
                        "component": {"path": object_path(comp), "name": comp.get_name(), "class": class_name(comp)},
                        "mesh": {"path": m_path, "name": m_name},
                        "source_transform": c_tf,
                        "final_world_transform": c_tf,
                        "reconstruction_transform": c_tf,
                        "transform_space": "composed_world"
                    })

            elif c_kind == "audio":
                sound = None
                try:
                    sound = comp.get_editor_property("sound")
                except Exception:
                    pass
                sound_path = object_path(sound) if sound else ""
                audio_features.append({
                    "component_path": object_path(comp),
                    "sound_path": sound_path,
                    "transform": c_tf
                })

    counters["placements"] = len(placements)
    counters["unique_meshes"] = len(unique_meshes)
    counters["audio_components"] = len(audio_features)

    manifest_data = {
        "manifest_version": "10.0",
        "pipeline": {"resolved_config": cfg.to_dict()},
        "_resolved": {"run_id": cfg.run_id, "config_hash": cfg.config_hash},
        "geometry": {
            "unique_meshes": unique_meshes,
            "placements": placements
        },
        "world_features": {
            "audio": audio_features
        },
        "reconstruction": {
            "ready_for_godot_geometry": True,
            "ready_for_godot_fx": True
        }
    }

    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest_data, f, indent=2, ensure_ascii=False)

    outputs = [{"path": manifest_path, "bytes": os.path.getsize(manifest_path)}]

    return StepReport(
        step="step1_manifest",
        run_id=cfg.run_id,
        config_hash=cfg.config_hash,
        status="OK",
        started_at=started_at,
        duration_s=time.time() - start_time,
        outputs=outputs,
        counters=counters,
        errors=errors,
        warnings=warnings
    )
