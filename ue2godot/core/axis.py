# -*- coding: utf-8 -*-
"""Single source of truth for axis conventions."""

from typing import Tuple, List, Dict, Any

UE_CM_TO_GODOT_M = 0.01

# The blessed convention matching Unreal's native glTF exporter output:
# Godot (x, y, z) = (UE.x * 0.01, UE.z * 0.01, UE.y * 0.01)
# Basis conversion matrix determinant = -1 (requires triangle winding inversion).

BLESSED_AXIS_PRESET = "unreal_gltf_exporter"


def convert_position_ue_to_godot(x: float, y: float, z: float, scale: float = UE_CM_TO_GODOT_M) -> Tuple[float, float, float]:
    """Convert UE position (cm) to Godot position (m)."""
    return (x * scale, z * scale, y * scale)


def get_axis_config(preset_name: str = BLESSED_AXIS_PRESET) -> Dict[str, Any]:
    if preset_name != BLESSED_AXIS_PRESET:
        raise ValueError(f"Unsupported axis preset: '{preset_name}'. Only '{BLESSED_AXIS_PRESET}' is blessed.")

    return {
        "preset": BLESSED_AXIS_PRESET,
        "map": ["x", "z", "y"],
        "determinant": -1,
        "unit_scale": UE_CM_TO_GODOT_M,
        "invert_winding": True,
        "label": f"(ue.x, ue.z, ue.y) * {UE_CM_TO_GODOT_M}"
    }
