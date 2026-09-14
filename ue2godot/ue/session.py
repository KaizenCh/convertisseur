# -*- coding: utf-8 -*-
"""Session and actor discovery logic (V4/V5/V6 ObjectIterator method - DO NOT REPLACE)."""

from typing import List, Any
from ue2godot.core.ids import object_path

try:
    import unreal
except ImportError:
    unreal = None


def discover_level_actors(level: Any = None) -> List[Any]:
    """Scans all level actors using the proven ObjectIterator + level filtering method."""
    if unreal is None:
        return []

    result = []
    seen = set()

    # Priority 1: Subsystem actors
    try:
        actor_subsystem = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
        actors = actor_subsystem.get_all_level_actors()
        for actor in actors:
            if actor is not None:
                path = object_path(actor)
                if path and path not in seen:
                    seen.add(path)
                    result.append(actor)
    except Exception:
        pass

    # Priority 2: Proven ObjectIterator fallback (DO NOT REPLACE)
    try:
        for actor in unreal.ObjectIterator(unreal.Actor):
            if actor is None:
                continue
            path = object_path(actor)
            if path and path not in seen:
                if level is None or (hasattr(actor, "get_level") and actor.get_level() == level):
                    seen.add(path)
                    result.append(actor)
    except Exception:
        pass

    return result
