# -*- coding: utf-8 -*-
"""
============================================================
UE5 -> GODOT CONVERSION MANIFEST - V10 (FRAMEWORK STEP 1)
============================================================
Reframed generalist step 1 using the complete V10-8 manifest scanner algorithm.
"""

import os
import json
import time
import hashlib
import traceback
from collections import Counter
from typing import Dict, Any

from ue2godot.core.config import ResolvedConfig
from ue2godot.core.report import StepReport
from ue2godot.core.safe import safe_call, safe_property, safe_int, safe_float, safe_bool
from ue2godot.core.ids import object_path, object_name, class_name, class_path, stable_id

try:
    import unreal
except ImportError:
    unreal = None


MAX_LEVEL_INSTANCE_DEPTH = 64


def vector_to_list(vector):
    if vector is None:
        return None
    try:
        return [float(vector.x), float(vector.y), float(vector.z)]
    except Exception:
        return None


def color_to_dict(color):
    if color is None:
        return None
    try:
        return {"r": float(color.r), "g": float(color.g), "b": float(color.b), "a": float(color.a)}
    except Exception:
        return None


def rotator_to_dict(rotator):
    if rotator is None:
        return None
    try:
        return {"pitch": float(rotator.pitch), "yaw": float(rotator.yaw), "roll": float(rotator.roll)}
    except Exception:
        return None


def transform_to_dict(transform):
    if transform is None:
        return None
    try:
        return {
            "location": vector_to_list(transform.translation),
            "rotation": rotator_to_dict(transform.rotation.rotator()),
            "scale": vector_to_list(transform.scale3d)
        }
    except Exception:
        return None


def actor_transform(actor):
    return safe_call(lambda: transform_to_dict(actor.get_actor_transform()), None)


def component_transform(component):
    return safe_call(lambda: transform_to_dict(component.get_component_transform()), None)


def _try_refetch_stale_component(component):
    try:
        path = component.get_path_name()
    except Exception:
        return None

    if not path:
        return None

    for loader_name in ("find_object", "load_object"):
        loader = getattr(unreal, loader_name, None)
        if loader is None:
            continue
        try:
            fresh = loader(None, path)
        except Exception:
            fresh = None
        if fresh is not None:
            try:
                if unreal.SystemLibrary.is_valid(fresh):
                    return fresh
            except Exception:
                return fresh
    return None


def _relative_transform_dict(component):
    location = safe_property(component, "relative_location", None)
    rotation = safe_property(component, "relative_rotation", None)
    scale = safe_property(component, "relative_scale3d", None)

    if location is None and rotation is None and scale is None:
        return None

    location_list = vector_to_list(location) or [0.0, 0.0, 0.0]
    rotation_dict = rotator_to_dict(rotation) or {"pitch": 0.0, "yaw": 0.0, "roll": 0.0}
    scale_list = vector_to_list(scale) or [1.0, 1.0, 1.0]

    return {"location": location_list, "rotation": rotation_dict, "scale": scale_list}


def component_transform_via_properties(component, actor=None, max_depth=32):
    chain = []
    current = component
    seen = set()
    depth = 0

    while current is not None and depth < max_depth:
        try:
            key = current.get_path_name()
        except Exception:
            key = None

        if key is not None:
            if key in seen:
                break
            seen.add(key)

        parent = safe_property(current, "attach_parent", None)
        if parent is None:
            break

        relative = _relative_transform_dict(current)
        if relative is None:
            return None, "relative transform unreadable on " + str(key)

        chain.append(relative)
        current = parent
        depth += 1

    if depth >= max_depth:
        return None, "attach_parent chain exceeded max depth"

    base = actor_transform(actor) if actor is not None else None
    if base is None:
        base = _relative_transform_dict(component)
        if base is None:
            return None, "no actor transform and no readable relative transform"
        return base, None

    result = base
    for relative in reversed(chain):
        result = compose_transform_dicts(result, relative)
        if result is None:
            return None, "compose_transform_dicts() failed walking attach_parent"

    return result, None


def component_transform_diagnostic(component, actor=None):
    if unreal is None:
        return None, "Unreal API unavailable"

    is_valid_fn = getattr(unreal, "SystemLibrary", None)
    if is_valid_fn is not None:
        try:
            if not unreal.SystemLibrary.is_valid(component):
                component = _try_refetch_stale_component(component) or component
        except Exception:
            pass

    try:
        raw_transform = component.get_component_transform()
    except AttributeError as exc:
        refetched = _try_refetch_stale_component(component)
        if refetched is not None:
            try:
                raw_transform = refetched.get_component_transform()
                result = transform_to_dict(raw_transform)
                if result is not None:
                    return result, None
            except Exception:
                pass
        fallback_value, fallback_error = component_transform_via_properties(component, actor)
        if fallback_value is not None:
            return fallback_value, None
        return None, f"get_component_transform() unavailable ({exc}) and property fallback failed: {fallback_error}"
    except Exception as exc:
        fallback_value, fallback_error = component_transform_via_properties(component, actor)
        if fallback_value is not None:
            return fallback_value, None
        return None, f"get_component_transform() raised: {exc} and property fallback failed: {fallback_error}"

    try:
        result = transform_to_dict(raw_transform)
    except Exception as exc:
        return None, f"transform_to_dict() raised: {exc}"

    if result is None:
        return None, "transform_to_dict() returned None"

    return result, None


def is_blueprint_generated_actor(actor):
    cpath = class_path(actor)
    if not cpath:
        return False
    if not cpath.endswith("_C"):
        return False
    return cpath.startswith("/Game/") or cpath.startswith("/Plugins/") or cpath.startswith("/Plugin/")


def actor_category(actor):
    cls = class_name(actor) or ""
    if "WorldPartition" in cls:
        return "world_partition_system"
    if "LevelInstance" in cls:
        return "level_instance"
    if "Landscape" in cls:
        return "landscape"
    if "Foliage" in cls:
        return "foliage"
    if "Decal" in cls:
        return "decal"
    if "Light" in cls:
        return "light"
    if "Niagara" in cls or "Particle" in cls:
        return "particle"
    if "Volume" in cls:
        return "volume"
    if "Audio" in cls:
        return "audio"
    if "Sky" in cls or "Atmosphere" in cls or "Cloud" in cls or "Fog" in cls:
        return "environment"
    if "StaticMeshActor" in cls:
        return "static_mesh"
    if "SkeletalMeshActor" in cls:
        return "skeletal_mesh"
    return "other"


def component_kind(component):
    cls = class_name(component) or ""
    if "HierarchicalInstancedStaticMesh" in cls:
        return "hierarchical_instanced_mesh"
    if "InstancedStaticMesh" in cls:
        return "instanced_mesh"
    if "StaticMeshComponent" in cls:
        return "static_mesh"
    if "SkeletalMeshComponent" in cls:
        return "skeletal_mesh"
    if "Niagara" in cls:
        return "niagara"
    if "Decal" in cls:
        return "decal"
    if "Light" in cls:
        return "light"
    if "Audio" in cls:
        return "audio"
    if "Spline" in cls:
        return "spline"
    if "Particle" in cls:
        return "particle"
    if "Collision" in cls:
        return "collision"
    if "Camera" in cls:
        return "camera"
    if "Widget" in cls:
        return "ui"
    return "other"


def dict_to_unreal_transform(data):
    if not isinstance(data, dict):
        return None
    loc, rot, scl = data.get("location"), data.get("rotation"), data.get("scale")
    if not loc or not rot or not scl:
        return None
    try:
        return unreal.Transform(
            location=unreal.Vector(float(loc[0]), float(loc[1]), float(loc[2])),
            rotation=unreal.Rotator(
                roll=float(rot.get("roll", 0.0)),
                pitch=float(rot.get("pitch", 0.0)),
                yaw=float(rot.get("yaw", 0.0))
            ),
            scale=unreal.Vector(float(scl[0]), float(scl[1]), float(scl[2]))
        )
    except Exception:
        return None


def compose_unreal_transforms(a, b):
    if a is None:
        return b
    if b is None:
        return a
    try:
        return unreal.KismetMathLibrary.compose_transforms(a, b)
    except Exception:
        try:
            return a * b
        except Exception:
            return None


def compose_transform_dicts(a_dict, b_dict):
    a = dict_to_unreal_transform(a_dict)
    b = dict_to_unreal_transform(b_dict)
    res = compose_unreal_transforms(a, b)
    return transform_to_dict(res) if res is not None else None


def compose_chain_transform(chain, source_transform, li_registry, diagnostics=None, context=None):
    current = None
    for li_path in chain or []:
        li_info = li_registry.get(li_path)
        if li_info is None:
            if diagnostics is not None:
                diagnostics["broken_li_chain_links"] += 1
                diagnostics["broken_li_chain_link_details"].append({"missing_li_actor_path": li_path, "context": context})
            continue
        li_tf = li_info.get("source_transform", li_info.get("transform"))
        if li_tf is None:
            continue
        if current is None:
            current = li_tf
        else:
            current = compose_transform_dicts(li_tf, current)

    if current is None:
        return source_transform

    return compose_transform_dicts(source_transform, current)


def run(cfg: ResolvedConfig) -> StepReport:
    started_at = time.strftime("%Y-%m-%d %H:%M:%S")
    start_time = time.time()

    export_root = cfg.get("paths.ue_export_root", "C:/Export")
    manifest_path = os.path.join(export_root, "level_manifest_v10.json")
    txt_path = os.path.join(export_root, "level_manifest_v10.txt")
    os.makedirs(export_root, exist_ok=True)

    errors = []
    warnings = []
    counters = {
        "direct_actors": 0, "accessible_actors": 0, "placements": 0,
        "unique_meshes": 0, "unique_materials": 0, "level_instances": 0
    }

    if unreal is None:
        return StepReport(
            step="step1_manifest", run_id=cfg.run_id, config_hash=cfg.config_hash,
            status="FAILED", started_at=started_at, duration_s=time.time() - start_time,
            errors=["Unreal Engine Python API is unavailable."], counters=counters
        )

    actor_subsystem = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    direct_actors = actor_subsystem.get_all_level_actors()
    counters["direct_actors"] = len(direct_actors)

    manifest_data = {
        "manifest_version": "10.0",
        "pipeline": {"resolved_config": cfg.to_dict()},
        "_resolved": {"run_id": cfg.run_id, "config_hash": cfg.config_hash},
        "exporter": {"name": "UE5 -> Godot Conversion Manifest", "version": "10.0", "engine": "Unreal Engine 5.5"},
        "world": {"path": "World", "name": "MainWorld"},
        "geometry": {"unique_meshes": {}, "placements": [], "unique_skeletal_meshes": {}, "skeletal_mesh_placements": []},
        "materials": {"unique_materials": {}},
        "textures": {"unique_textures": {}},
        "level_instances": {"placements": [], "hierarchy": [], "registry": {}},
        "effects": {"niagara": [], "decals": [], "particles": []},
        "world_features": {"landscape": [], "lights": [], "foliage": [], "audio": [], "environment": []},
        "conversion_diagnostic": {},
        "reconstruction": {"ready_for_godot_geometry": True, "ready_for_godot_fx": True}
    }

    unique_meshes = {}
    placements = []

    for actor in direct_actors:
        if actor is None:
            continue
        a_path = object_path(actor)
        a_name = object_name(actor)
        a_class = class_name(actor)

        try:
            comps = actor.get_components_by_class(unreal.ActorComponent)
        except Exception:
            comps = []

        for comp in comps:
            kind = component_kind(comp)
            tf_value, tf_err = component_transform_diagnostic(comp, actor)

            if kind in ("static_mesh", "instanced_mesh", "hierarchical_instanced_mesh"):
                mesh = safe_property(comp, "static_mesh", None)
                if mesh is not None:
                    m_path = object_path(mesh)
                    m_name = object_name(mesh)
                    if m_path not in unique_meshes:
                        unique_meshes[m_path] = {"path": m_path, "name": m_name, "class": class_name(mesh)}

                    pid = stable_id("MESH", f"{a_path}_{comp.get_name()}")
                    placements.append({
                        "kind": kind,
                        "placement_id": pid,
                        "actor": {"path": a_path, "name": a_name, "class": a_class},
                        "component": {"path": object_path(comp), "name": comp.get_name(), "class": class_name(comp)},
                        "mesh": {"path": m_path, "name": m_name},
                        "source_transform": tf_value,
                        "final_world_transform": tf_value,
                        "reconstruction_transform": tf_value,
                        "transform_space": "composed_world"
                    })

            elif kind == "decal":
                mat = safe_property(comp, "decal_material", None)
                manifest_data["effects"]["decals"].append({
                    "actor": {"path": a_path, "name": a_name, "class": a_class},
                    "component": {"path": object_path(comp), "name": comp.get_name(), "class": class_name(comp)},
                    "transform": tf_value,
                    "material_path": object_path(mat) if mat else "",
                    "size": vector_to_list(safe_property(comp, "decal_size", None)) or [256.0, 256.0, 256.0]
                })

            elif kind == "niagara":
                system = safe_property(comp, "asset", None) or safe_property(comp, "template", None)
                manifest_data["effects"]["niagara"].append({
                    "actor": {"path": a_path, "name": a_name, "class": a_class},
                    "component": {"path": object_path(comp), "name": comp.get_name(), "class": class_name(comp)},
                    "transform": tf_value,
                    "system_name": object_name(system) if system else "UnknownNiagara"
                })

            elif kind == "light":
                color = safe_property(comp, "light_color", None)
                manifest_data["world_features"]["lights"].append({
                    "actor": {"path": a_path, "name": a_name, "class": a_class},
                    "component": {"path": object_path(comp), "name": comp.get_name(), "class": class_name(comp)},
                    "transform": tf_value,
                    "intensity": safe_float(safe_property(comp, "intensity", None), 0.0),
                    "color": color_to_dict(color)
                })

            elif kind == "audio":
                sound = safe_property(comp, "sound", None)
                manifest_data["world_features"]["audio"].append({
                    "actor": {"path": a_path, "name": a_name, "class": a_class},
                    "component_path": object_path(comp),
                    "sound_path": object_path(sound) if sound else "",
                    "transform": tf_value
                })

    counters["placements"] = len(placements)
    counters["unique_meshes"] = len(unique_meshes)

    manifest_data["geometry"]["unique_meshes"] = unique_meshes
    manifest_data["geometry"]["placements"] = placements

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
