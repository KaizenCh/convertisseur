# -*- coding: utf-8 -*-
"""In-editor Unreal execution entry point."""

import os
import json
from typing import Dict, Any
from ue2godot.core.config import ResolvedConfig
from ue2godot.core.report import StepReport

from ue2godot.ue.steps import (
    step0_preprocess,
    step1_manifest,
    step2_meshes,
    step3_landscape,
    step4_decals_vfx
)

STEPS = {
    "preprocess": step0_preprocess.run,
    "manifest": step1_manifest.run,
    "meshes": step2_meshes.run,
    "landscape": step3_landscape.run,
    "decals_vfx": step4_decals_vfx.run
}


def run_step(step_name: str, run_config_path: str) -> str:
    """Executes a single step given its name and run_config.json path."""
    if step_name not in STEPS:
        raise ValueError(f"Unknown step '{step_name}'. Valid steps: {list(STEPS.keys())}")

    if not os.path.isfile(run_config_path):
        raise FileNotFoundError(f"Run config file not found: {run_config_path}")

    with open(run_config_path, "r", encoding="utf-8") as f:
        config_data = json.load(f)

    resolved_info = config_data.get("_resolved", {})
    run_id = resolved_info.get("run_id", "default_run")
    config_hash = resolved_info.get("config_hash", "default_hash")

    cfg = ResolvedConfig(data=config_data, run_id=run_id, config_hash=config_hash)

    step_func = STEPS[step_name]
    report: StepReport = step_func(cfg)

    report_dir = cfg.get("paths.report_dir", os.path.join(cfg.get("paths.ue_export_root", "C:/Export"), "reports"))
    os.makedirs(report_dir, exist_ok=True)
    report_path = os.path.join(report_dir, f"step_{step_name}_{run_id}.json")

    report.save(report_path)
    print(f"[ue2godot] Step '{step_name}' finished with status {report.status}. Report saved to {report_path}")
    return report_path
