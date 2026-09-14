# -*- coding: utf-8 -*-
"""Crosschecking tools between manifest, asset_map, and decal_map."""

from typing import Dict, Any, List, Tuple, Optional


def crosscheck_json_outputs(
    manifest: Dict[str, Any],
    asset_map: Dict[str, Any],
    decal_map: Optional[Dict[str, Any]] = None
) -> Tuple[bool, List[str], List[str]]:
    """Vérification croisée automatique des JSONs.

    Returns: (is_valid, errors, warnings)
    """
    errors: List[str] = []
    warnings: List[str] = []

    # Check run_id / config_hash consistency if present
    m_resolved = manifest.get("_resolved", {}) or manifest.get("pipeline", {}).get("resolved_config", {}).get("_resolved", {})
    a_resolved = asset_map.get("_resolved", {})

    m_run_id = m_resolved.get("run_id")
    a_run_id = a_resolved.get("run_id")

    if m_run_id and a_run_id and m_run_id != a_run_id:
        errors.append(f"Divergence run_id entre manifest ({m_run_id}) et asset_map ({a_run_id}). Relance détectée !")

    # Unique meshes vs asset_map assets
    unique_meshes = len(manifest.get("geometry", {}).get("unique_meshes", {}))
    assets = asset_map.get("assets", {})

    has_landscape = "/AutoTerrain/Landscape.BakedLandscape" in assets
    expected_assets = unique_meshes + (1 if has_landscape else 0)

    if len(assets) != expected_assets:
        warnings.append(
            f"Compte unique_meshes ({unique_meshes}) + landscape ({1 if has_landscape else 0}) != assets ({len(assets)})"
        )

    # Check missing GLB files if disk status is available
    failures = asset_map.get("failures", [])
    if failures:
        warnings.append(f"{len(failures)} échec(s) d'export GLB signalés dans asset_map.")

    if decal_map:
        d_resolved = decal_map.get("_resolved", {})
        d_run_id = d_resolved.get("run_id")
        if m_run_id and d_run_id and m_run_id != d_run_id:
            errors.append(f"Divergence run_id entre manifest ({m_run_id}) et decal_map ({d_run_id}).")

    return len(errors) == 0, errors, warnings
