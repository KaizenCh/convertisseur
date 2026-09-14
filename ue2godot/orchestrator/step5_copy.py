# -*- coding: utf-8 -*-
"""Step 5: Automated copy of exported assets to Godot target project."""

import os
import shutil
import hashlib
from datetime import datetime
from typing import Dict, Any, List
from ue2godot.core.config import ResolvedConfig
from ue2godot.core.report import StepReport


def compute_file_sha256(filepath: str) -> str:
    hasher = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def copy_step5(cfg: ResolvedConfig) -> StepReport:
    started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    ue_export_root = cfg.get("paths.ue_export_root", "C:/Export")
    godot_project_root = cfg.get("paths.godot_project_root", "")

    errors = []
    warnings = []
    outputs = []
    counters = {"files_copied": 0, "verified_hashes": 0}

    if not godot_project_root or not os.path.isdir(godot_project_root):
        errors.append(f"Invalid Godot project root: '{godot_project_root}'")
        return StepReport(
            step="step5_copy", run_id=cfg.run_id, config_hash=cfg.config_hash,
            status="FAILED", started_at=started_at, duration_s=0.0,
            errors=errors, counters=counters
        )

    godot_asset_sub = cfg.get("paths.godot_asset_root", "res://UEAssets").replace("res://", "")
    target_asset_dir = os.path.join(godot_project_root, godot_asset_sub)
    source_asset_dir = os.path.join(ue_export_root, "GodotAssets")

    # Copy GodotAssets directory
    if os.path.exists(source_asset_dir):
        for root, dirs, files in os.walk(source_asset_dir):
            rel = os.path.relpath(root, source_asset_dir)
            dest_dir = os.path.join(target_asset_dir, rel) if rel != "." else target_asset_dir
            os.makedirs(dest_dir, exist_ok=True)
            for file in files:
                src_file = os.path.join(root, file)
                dst_file = os.path.join(dest_dir, file)
                shutil.copy2(src_file, dst_file)
                counters["files_copied"] += 1
                if compute_file_sha256(src_file) == compute_file_sha256(dst_file):
                    counters["verified_hashes"] += 1
                else:
                    errors.append(f"Hash mismatch after copy: {src_file} -> {dst_file}")

    # Copy JSONs to root (check both ue_export_root and GodotAssets subfolder)
    for json_name in ["level_manifest_v10.json", "ue5_godot_asset_map.json", "ue5_godot_decal_map.json"]:
        src_json = os.path.join(ue_export_root, json_name)
        if not os.path.isfile(src_json):
            src_json = os.path.join(source_asset_dir, json_name)

        if os.path.isfile(src_json):
            dst_json = os.path.join(godot_project_root, json_name)
            shutil.copy2(src_json, dst_json)
            counters["files_copied"] += 1
            if compute_file_sha256(src_json) == compute_file_sha256(dst_json):
                counters["verified_hashes"] += 1
            outputs.append({"path": dst_json, "bytes": os.path.getsize(dst_json)})

    status = "OK" if not errors else "FAILED"
    return StepReport(
        step="step5_copy", run_id=cfg.run_id, config_hash=cfg.config_hash,
        status=status, started_at=started_at, duration_s=0.0,
        outputs=outputs, counters=counters, errors=errors, warnings=warnings
    )
