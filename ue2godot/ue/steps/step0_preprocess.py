# -*- coding: utf-8 -*-
"""Step 0: Detach all attached actors (keep world transforms) and verify level instances."""

import time
from typing import Dict, Any
from ue2godot.core.config import ResolvedConfig
from ue2godot.core.report import StepReport
from ue2godot.core.ids import object_path

try:
    import unreal
except ImportError:
    unreal = None


def run(cfg: ResolvedConfig) -> StepReport:
    started_at = time.strftime("%Y-%m-%d %H:%M:%S")
    start_time = time.time()

    dry_run = cfg.get("preprocess.dry_run", False)
    errors = []
    warnings = []
    counters = {"attached_found": 0, "detached": 0, "failed": 0, "level_instances": 0}

    if unreal is None:
        return StepReport(
            step="step0_preprocess",
            run_id=cfg.run_id,
            config_hash=cfg.config_hash,
            status="FAILED",
            started_at=started_at,
            duration_s=time.time() - start_time,
            errors=["Unreal Engine Python API is unavailable."],
            counters=counters
        )

    actor_subsystem = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    all_actors = actor_subsystem.get_all_level_actors()

    attached = []
    for actor in all_actors:
        if actor is None:
            continue
        try:
            parent = actor.get_attach_parent_actor()
            if parent is not None:
                attached.append((actor, parent))
            cname = str(actor.get_class().get_name())
            if "LevelInstance" in cname:
                counters["level_instances"] += 1
        except Exception:
            pass

    counters["attached_found"] = len(attached)

    if not dry_run:
        for child, parent in attached:
            try:
                child.detach_from_actor(
                    unreal.DetachmentRule.KEEP_WORLD,
                    unreal.DetachmentRule.KEEP_WORLD,
                    unreal.DetachmentRule.KEEP_WORLD
                )
                counters["detached"] += 1
            except Exception as exc:
                counters["failed"] += 1
                errors.append(f"Failed to detach {object_path(child)}: {exc}")

    if counters["level_instances"] > 0:
        warnings.append(f"{counters['level_instances']} LevelInstance actors still present. Break them manually if needed.")

    status = "OK" if not errors else "FAILED"
    if warnings and status == "OK":
        status = "OK_WITH_WARNINGS"

    return StepReport(
        step="step0_preprocess",
        run_id=cfg.run_id,
        config_hash=cfg.config_hash,
        status=status,
        started_at=started_at,
        duration_s=time.time() - start_time,
        counters=counters,
        errors=errors,
        warnings=warnings
    )
