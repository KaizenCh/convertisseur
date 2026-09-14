# -*- coding: utf-8 -*-
"""Landscape Material and Paint Layer Auto-Configurator."""

from typing import Dict, Any, List, Optional

try:
    import unreal
except ImportError:
    unreal = None


def auto_configure_landscape_material(landscape_actor: Any, target_folder: str = "/Game/LandscapeMaterials") -> Dict[str, Any]:
    """Inspects and auto-configures landscape material and paint layers.

    If the landscape lacks a custom material, searches `target_folder` for candidate materials
    and assigns the best matching material instance or parent material.
    """
    result = {
        "status": "OK",
        "assigned_material": None,
        "paint_layers": [],
        "auto_configured": False,
        "notes": []
    }

    if unreal is None or landscape_actor is None:
        result["status"] = "UNAVAILABLE"
        return result

    # Check existing material
    mat = None
    try:
        mat = landscape_actor.get_editor_property("landscape_material")
    except Exception:
        pass

    if mat is None:
        # Search target_folder for candidate materials
        try:
            asset_reg = unreal.get_editor_subsystem(unreal.AssetRegistrySubsystem)
            assets = asset_reg.get_assets_by_path(target_folder, recursive=True)
            for a in assets:
                cname = str(a.asset_class_path.asset_name) if hasattr(a, "asset_class_path") else str(a.asset_class)
                if cname in ("Material", "MaterialInstanceConstant"):
                    loaded = a.get_asset()
                    if loaded is not None:
                        landscape_actor.set_editor_property("landscape_material", loaded)
                        mat = loaded
                        result["auto_configured"] = True
                        result["notes"].append(f"Auto-assigned material from {target_folder}: {loaded.get_name()}")
                        break
        except Exception as exc:
            result["notes"].append(f"Auto-assignment search failed: {exc}")

    if mat is not None:
        try:
            result["assigned_material"] = str(mat.get_path_name())
        except Exception:
            result["assigned_material"] = str(mat)

        # Inspect landscape material parameters safely
        try:
            if hasattr(mat, "get_editor_property"):
                params = mat.get_editor_property("vector_parameter_values")
                if params:
                    for p in params:
                        pname = str(getattr(p, "parameter_info", p))
                        result["paint_layers"].append(pname)
        except Exception:
            pass

    return result
