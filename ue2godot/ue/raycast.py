# -*- coding: utf-8 -*-
"""Unified grid height raycasting."""

from typing import List, Optional, Tuple, Any

try:
    import unreal
except ImportError:
    unreal = None


def raycast_grid_heights(
    world: Any,
    bounds: Tuple[float, float, float, float, float, float],
    resolution: int = 256,
    max_retries: int = 12
) -> List[Optional[float]]:
    """Traces vertical lines straight down to measure landscape heights."""
    if world is None or unreal is None:
        return [None] * ((resolution + 1) * (resolution + 1))

    min_x, max_x, min_y, max_y, min_z, max_z = bounds
    side = resolution + 1
    heights: List[Optional[float]] = [None] * (side * side)

    channel = unreal.TraceTypeQuery.TRACE_TYPE_QUERY1
    ignore: List[Any] = []
    ignored_paths = set()

    for ix in range(side):
        x = min_x + (max_x - min_x) * (ix / float(resolution))
        for iy in range(side):
            y = min_y + (max_y - min_y) * (iy / float(resolution))
            start = unreal.Vector(x, y, max_z + 1000.0)
            end = unreal.Vector(x, y, min_z - 1000.0)

            for _ in range(max_retries):
                try:
                    hit = unreal.SystemLibrary.line_trace_single(
                        world, start, end, channel, True, ignore,
                        unreal.DrawDebugTrace.NONE, True
                    )
                except Exception:
                    hit = None

                if isinstance(hit, tuple):
                    hit = hit[-1] if (len(hit) > 1 and hit[0]) else None

                if hit is None:
                    break

                # Extract impact_z and actor
                impact_z = None
                actor = None
                try:
                    fields = unreal.GameplayStatics.break_hit_result(hit)
                    for f in fields:
                        if impact_z is None and hasattr(f, "z"):
                            impact_z = float(f.z)
                        if actor is None and isinstance(f, unreal.Actor):
                            actor = f
                except Exception:
                    pass

                if impact_z is None:
                    break

                if actor is None or isinstance(actor, unreal.LandscapeProxy):
                    heights[ix * side + iy] = impact_z
                    break

                try:
                    path = str(actor.get_path_name())
                except Exception:
                    break

                if path in ignored_paths:
                    break

                ignored_paths.add(path)
                ignore.append(actor)

    return heights
