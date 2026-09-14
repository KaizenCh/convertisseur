# -*- coding: utf-8 -*-
"""Robust transform extraction with property fallbacks."""

from typing import Any, Tuple, Optional, Dict
from ue2godot.core.safe import safe_property, safe_float

try:
    import unreal
except ImportError:
    unreal = None


def extract_transform_dict(transform_obj: Any) -> Dict[str, Any]:
    if transform_obj is None:
        return {
            "location": [0.0, 0.0, 0.0],
            "rotation": {"pitch": 0.0, "yaw": 0.0, "roll": 0.0},
            "scale": [1.0, 1.0, 1.0]
        }

    try:
        loc = transform_obj.translation
        rot = transform_obj.rotation.rotator()
        scl = transform_obj.scale3d
        return {
            "location": [float(loc.x), float(loc.y), float(loc.z)],
            "rotation": {"pitch": float(rot.pitch), "yaw": float(rot.yaw), "roll": float(rot.roll)},
            "scale": [float(scl.x), float(scl.y), float(scl.z)]
        }
    except Exception:
        pass

    return {
        "location": [0.0, 0.0, 0.0],
        "rotation": {"pitch": 0.0, "yaw": 0.0, "roll": 0.0},
        "scale": [1.0, 1.0, 1.0]
    }


def actor_transform(actor: Any) -> Dict[str, Any]:
    if actor is None or unreal is None:
        return extract_transform_dict(None)
    try:
        tf = actor.get_actor_transform()
        return extract_transform_dict(tf)
    except Exception:
        return extract_transform_dict(None)


def component_transform_diagnostic(component: Any, actor: Any) -> Tuple[Dict[str, Any], Optional[str]]:
    if component is None or unreal is None:
        return extract_transform_dict(None), "Component is None"

    # Try get_component_transform()
    try:
        tf = component.get_component_transform()
        return extract_transform_dict(tf), None
    except Exception as exc:
        # Fallback to properties: location / rotation / scale3d
        rel_loc = safe_property(component, "relative_location")
        rel_rot = safe_property(component, "relative_rotation")
        rel_scl = safe_property(component, "relative_scale3d")

        actor_tf = actor_transform(actor)

        if rel_loc and rel_rot and rel_scl:
            loc = [safe_float(getattr(rel_loc, "x", 0)), safe_float(getattr(rel_loc, "y", 0)), safe_float(getattr(rel_loc, "z", 0))]
            rot = {"pitch": safe_float(getattr(rel_rot, "pitch", 0)), "yaw": safe_float(getattr(rel_rot, "yaw", 0)), "roll": safe_float(getattr(rel_rot, "roll", 0))}
            scl = [safe_float(getattr(rel_scl, "x", 1)), safe_float(getattr(rel_scl, "y", 1)), safe_float(getattr(rel_scl, "z", 1))]

            # Combine actor transform base with relative component offset
            combined_loc = [actor_tf["location"][i] + loc[i] for i in range(3)]
            combined_scl = [actor_tf["scale"][i] * scl[i] for i in range(3)]

            return {
                "location": combined_loc,
                "rotation": rot,
                "scale": combined_scl
            }, f"Property fallback used due to: {exc}"

        return actor_tf, f"Failed get_component_transform ({exc}); used actor transform fallback"
