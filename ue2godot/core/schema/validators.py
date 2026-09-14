# -*- coding: utf-8 -*-
"""Validation functions for manifest, asset map, and decal map JSONs."""

from typing import Dict, Any, List, Tuple


def validate_manifest_schema(data: Dict[str, Any]) -> Tuple[bool, List[str]]:
    errors = []
    if not isinstance(data, dict):
        return False, ["Manifest is not a JSON object"]

    required_keys = ["manifest_version", "geometry", "actors", "reconstruction"]
    for k in required_keys:
        if k not in data:
            errors.append(f"Missing required manifest key: '{k}'")

    geometry = data.get("geometry", {})
    if isinstance(geometry, dict):
        if "placements" not in geometry:
            errors.append("Manifest 'geometry' missing 'placements'")
        if "unique_meshes" not in geometry:
            errors.append("Manifest 'geometry' missing 'unique_meshes'")

    return len(errors) == 0, errors


def validate_asset_map_schema(data: Dict[str, Any]) -> Tuple[bool, List[str]]:
    errors = []
    if not isinstance(data, dict):
        return False, ["Asset map is not a JSON object"]

    if "assets" not in data:
        errors.append("Asset map missing 'assets' key")

    return len(errors) == 0, errors


def validate_decal_map_schema(data: Dict[str, Any]) -> Tuple[bool, List[str]]:
    errors = []
    if not isinstance(data, dict):
        return False, ["Decal map is not a JSON object"]

    if "decal_materials" not in data:
        errors.append("Decal map missing 'decal_materials' key")

    return len(errors) == 0, errors
