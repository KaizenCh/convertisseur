# -*- coding: utf-8 -*-
"""Render target baking utilities for Landscape and Decals."""

import os
from typing import Any, Optional

try:
    import unreal
except ImportError:
    unreal = None


def bake_unlit_material_to_png(
    world: Any,
    material_asset: Any,
    output_png_path: str,
    resolution: int = 1024
) -> bool:
    """Draws a material to a RenderTarget and exports to PNG."""
    if world is None or unreal is None:
        return False

    try:
        render_target = unreal.RenderingLibrary.create_render_target2d(
            world, resolution, resolution, unreal.TextureRenderTargetFormat.RTF_RGBA8_SRGB
        )
        if render_target is None:
            return False

        unreal.MaterialEditingLibrary.draw_material_to_render_target(
            world, render_target, material_asset
        )

        directory = os.path.dirname(output_png_path)
        filename = os.path.basename(output_png_path)
        os.makedirs(directory, exist_ok=True)

        unreal.RenderingLibrary.export_render_target(world, render_target, directory, filename)
        return os.path.isfile(output_png_path)
    except Exception:
        return False
