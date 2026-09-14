# -*- coding: utf-8 -*-
"""
======================================================================
UE5.5 -> GODOT  LANDSCAPE EXPORTER  (script 4 of the pipeline)
======================================================================

Run AFTER unreal_export_manifest_v10.py, and BEFORE (or after) the asset
exporter - order does not matter, this script only appends.

    1. unreal_export_manifest_v10.py      -> level_manifest_v10.json
    2. unreal_export_godot_assets.py      -> Meshes/*.glb + asset map
    3. unreal_export_landscape.py  (THIS) -> Landscape glb + registers it
    4. ue5_godot_map_constructor.gd       -> Map--_REBUILT.tscn

WHY THIS SCRIPT EXISTS
----------------------
The manifest records the Landscape actor (world_features.landscape) with
its transform and bounds, but no geometry and no texture: it is a
specification with no payload, and the manifest itself flags it as a HIGH
conversion risk. Meanwhile ALandscapeProxy::ExportToRawMesh() exists in
C++ but is NOT exposed to the official Python API, and "File > Export
Selected > GLB" on a Landscape is known to produce broken output.

So the geometry is rebuilt here, from the outside:

  GEOMETRY  vertical line traces on a regular grid (the same technique the
            terrain material generator already uses to measure the ground,
            including its occluder ignore-list so props standing on the
            terrain are traced through instead of being sampled as ground).

  TEXTURE   an orthographic SceneCapture2D above the terrain, rendering
            ONLY the landscape (PRM_USE_SHOW_ONLY_LIST) with
            SCS_BASE_COLOR, into a TextureRenderTarget2D, exported to PNG
            with RenderingLibrary.export_render_target().
            BaseColor = unlit albedo on purpose: the props exported by the
            rest of the pipeline are re-lit by Godot, so baking Unreal's
            lighting into the terrain would make the two disagree.

  PACKAGING a GLB written directly here. A heightfield is a regular grid,
            so the geometry is trivial (positions, planar UVs, computed
            normals, triangles) and the PNG is embedded in the binary
            chunk. No unexposed engine API is involved.

COORDINATE SPACE - IMPORTANT
----------------------------
The .gd converts each placement's TRANSFORM, but never touches the vertex
data inside a GLB. So the vertices written here must already be in the
same space the .gd produces for everything else, otherwise the terrain
would not line up with the props standing on it.

The constructor maps a UE position (X, Y, Z) in centimetres to Godot:

        godot = (X, Z, -Y) * 0.01

This script writes its vertices with exactly that mapping (AXIS_MAP
below) and gives the landscape placement an IDENTITY transform. The
mapping is also recorded in the asset-map entry as "axis_map", so a future
mismatch between the two scripts is detectable instead of silent.

That mapping has determinant +1, so triangle winding is preserved and no
index flip is needed. If you ever change the mapping on either side,
change it on BOTH, and re-check the determinant.

Usage (Unreal Output Log, Cmd mode):
    py "C:/.../unreal_export_landscape.py"
"""

import json
import math
import os
import struct
import time
import traceback

import unreal


# ======================================================================
# CONFIGURATION
# ======================================================================

MANIFEST_PATH = r"C:/Export/level_manifest_v10.json"
OUTPUT_ROOT = r"C:/Export/GodotAssets"
MESH_OUTPUT_DIR = os.path.join(OUTPUT_ROOT, "Meshes")
ASSET_MAP_PATH = os.path.join(OUTPUT_ROOT, "ue5_godot_asset_map.json")

GODOT_MESH_ROOT = "res://UEAssets/Meshes"

# Synthetic Unreal object path for the generated terrain. It is not a real
# asset - it only has to be a stable key shared by the manifest placement
# and the asset map, which is exactly what the .gd resolves through.
LANDSCAPE_UE_PATH = "/AutoTerrain/Landscape.BakedLandscape"
LANDSCAPE_GLB_NAME = "BakedLandscape.glb"
LANDSCAPE_PNG_NAME = "BakedLandscape_BaseColor.png"

# Geometry resolution: GRID_RESOLUTION x GRID_RESOLUTION quads.
# 256 -> 257^2 = 66049 traces, roughly 20-60 s. 512 quadruples both the
# time and the triangle count; 128 halves them and stays fine for distant
# terrain. This is the single biggest cost knob in the script.
GRID_RESOLUTION = 256

# Baked BaseColor texture resolution (square).
TEXTURE_RESOLUTION = 4096

# Vertical margin added above/below the landscape bounds when tracing.
TRACE_MARGIN_CM = 50000.0

# A trace that hits something that is not the landscape adds that actor to
# a shared ignore list and retries. This caps the retries per trace so a
# pathological column cannot stall the export.
MAX_TRACE_RETRIES = 12

# UE (X, Y, Z) cm -> Godot. Must match the constructor's _transform_from_v10.
UE_CM_TO_GODOT_M = 0.01


def AXIS_MAP(x, y, z):
    """UE centimetres -> Godot metres. Keep in sync with the .gd.

    V2: the sign on Y is gone. The constructor used to map UE Y to -Godot Z,
    a determinant +1 mapping, which does NOT flip handedness - while the GLB
    meshes coming out of Unreal's own glTF exporter DO have their handedness
    flipped. Symmetric props (pillars, walls, tombs) hid the disagreement;
    stairs made it obvious. The constructor now uses (x, z, y), determinant
    -1, matching the exporter, so the terrain has to follow.

    Determinant is now -1, which REVERSES triangle winding - see
    build_grid_mesh(), where the index order is flipped to compensate.
    """
    return (
        x * UE_CM_TO_GODOT_M,
        z * UE_CM_TO_GODOT_M,
        y * UE_CM_TO_GODOT_M,
    )


AXIS_MAP_LABEL = "(ue.x, ue.z, ue.y) * 0.01"


def log(message):
    unreal.log("[UE5->Godot Landscape] " + str(message))


def warn(message):
    unreal.log_warning("[UE5->Godot Landscape] " + str(message))


# ======================================================================
# LANDSCAPE DISCOVERY
# ======================================================================

def get_editor_world():
    try:
        return unreal.get_editor_subsystem(
            unreal.UnrealEditorSubsystem
        ).get_editor_world()
    except Exception:
        return None


def find_landscape_actors():
    """Returns (primary_landscape, all_landscape_actors).

    all_landscape_actors includes the streaming proxies, because the
    SceneCapture show-only list and the traces must consider the whole
    terrain, not just the parent actor.
    """
    try:
        actors = unreal.get_editor_subsystem(
            unreal.EditorActorSubsystem
        ).get_all_level_actors()
    except Exception:
        traceback.print_exc()
        return None, []

    landscapes = []
    proxies = []

    for actor in actors:
        if isinstance(actor, unreal.Landscape):
            landscapes.append(actor)
        elif isinstance(actor, unreal.LandscapeProxy):
            proxies.append(actor)

    if not landscapes and not proxies:
        return None, []

    primary = landscapes[0] if landscapes else proxies[0]

    if len(landscapes) > 1:
        warn(
            "%d Landscape actors found - using '%s'. The whole terrain is "
            "still captured, but a multi-landscape level was not the "
            "designed case." % (len(landscapes), primary.get_actor_label())
        )

    return primary, landscapes + proxies


def landscape_bounds(actors):
    """Combined world-space bounds of every landscape actor."""
    bmin = [math.inf, math.inf, math.inf]
    bmax = [-math.inf, -math.inf, -math.inf]

    for actor in actors:
        try:
            origin, extent = actor.get_actor_bounds(False)
        except Exception:
            continue

        for i, (o, e) in enumerate((
            (origin.x, extent.x),
            (origin.y, extent.y),
            (origin.z, extent.z),
        )):
            bmin[i] = min(bmin[i], o - e)
            bmax[i] = max(bmax[i], o + e)

    if bmin[0] == math.inf:
        return None

    return bmin, bmax


# ======================================================================
# HEIGHTFIELD SAMPLING
# ======================================================================

def _parse_hit(hit):
    """Extracts (actor, impact_z) from a hit result across API variants."""
    fields = None

    for getter in (
        lambda h: unreal.GameplayStatics.break_hit_result(h),
        lambda h: h.to_tuple(),
    ):
        try:
            fields = getter(hit)
            if fields:
                break
        except Exception:
            fields = None

    if not fields:
        return None, None

    impact_z = None
    actor = None

    for field in fields:
        if (impact_z is None
                and not isinstance(field, (bool, int, float, str))
                and hasattr(field, "z")):
            try:
                impact_z = float(field.z)
            except Exception:
                pass
        if actor is None and isinstance(field, unreal.Actor):
            actor = field

    return actor, impact_z


def sample_heightfield(world, x_min, x_max, y_min, y_max, z_top, z_bottom,
                       resolution):
    """Traces a (resolution+1)^2 grid straight down.

    Returns a flat list of heights (None where nothing was hit), row-major
    with X as the outer axis, matching build_grid_mesh().
    """
    side = resolution + 1
    heights = [None] * (side * side)

    channel = unreal.TraceTypeQuery.TRACE_TYPE_QUERY1
    ignore = []
    ignored_paths = set()

    total = side * side
    reported = 0
    start_time = time.time()

    for ix in range(side):

        x = x_min + (x_max - x_min) * (ix / float(resolution))

        for iy in range(side):

            y = y_min + (y_max - y_min) * (iy / float(resolution))

            start = unreal.Vector(x, y, z_top)
            end = unreal.Vector(x, y, z_bottom)

            for _ in range(MAX_TRACE_RETRIES):

                hit = unreal.SystemLibrary.line_trace_single(
                    world, start, end, channel, True, ignore,
                    unreal.DrawDebugTrace.NONE, True
                )

                if isinstance(hit, tuple):
                    hit = hit[-1] if (len(hit) > 1 and hit[0]) else None

                if hit is None:
                    break

                actor, impact_z = _parse_hit(hit)

                if impact_z is None:
                    break

                # Landscape (or an unidentifiable hit) -> accept it.
                if actor is None or isinstance(actor, unreal.LandscapeProxy):
                    heights[ix * side + iy] = impact_z
                    break

                # A prop standing on the terrain: ignore it from now on and
                # trace again through the same column.
                try:
                    path = actor.get_path_name()
                except Exception:
                    break

                if path in ignored_paths:
                    break

                ignored_paths.add(path)
                ignore.append(actor)

        done = (ix + 1) * side
        if done - reported >= max(total // 10, 1):
            reported = done
            log("  height sampling %d%%" % int(done * 100.0 / total))

    hits = sum(1 for h in heights if h is not None)

    log("Height sampling: %d/%d points hit the landscape in %.1f s "
        "(%d occluding actors traced through)"
        % (hits, total, time.time() - start_time, len(ignore)))

    return heights, side


# ======================================================================
# BASE COLOR BAKE
# ======================================================================

def _get_capture_component(capture_actor):
    """Returns the live SceneCaptureComponent2D instance.

    get_editor_property("capture_component2d") hands back the actor's
    component TEMPLATE, and Unreal refuses writes to several properties on
    a template ("cannot be edited on templates"). Asking the spawned actor
    for its component by class returns the real instance instead.
    """
    component = None

    try:
        component = capture_actor.get_component_by_class(
            unreal.SceneCaptureComponent2D
        )
    except Exception:
        component = None

    if component is None:
        try:
            components = capture_actor.get_components_by_class(
                unreal.SceneCaptureComponent2D
            )
            if components:
                component = components[0]
        except Exception:
            component = None

    if component is None:
        warn("Falling back to the component template - some properties may "
             "refuse to be set.")
        component = capture_actor.get_editor_property("capture_component2d")

    return component


def _restrict_capture_to(component, landscape_actors):
    """Makes the capture render the landscape and nothing else.

    Without this the gravestones standing on the terrain would be painted
    into the terrain's own texture. Three strategies are tried in order,
    because which one is writable depends on the engine version and on
    whether the component is a template.

    Returns True if the capture is genuinely restricted.
    """
    try:
        component.set_editor_property(
            "primitive_render_mode",
            unreal.SceneCapturePrimitiveRenderMode.PRM_USE_SHOW_ONLY_LIST,
        )
    except Exception:
        warn("primitive_render_mode could not be set.")
        return False

    # 1. The plain property - works when we hold a real instance.
    try:
        component.set_editor_property("show_only_actors", landscape_actors)
        log("Capture restricted to the landscape (show_only_actors).")
        return True
    except Exception as exc:
        log("show_only_actors property refused (%s) - trying the method API."
            % exc)

    # 2. The dedicated method, which appends instead of assigning.
    added = 0
    for actor in landscape_actors:
        for method_name in ("show_only_actor_components",
                            "show_only_actors_components"):
            method = getattr(component, method_name, None)
            if method is None:
                continue
            try:
                method(actor)
                added += 1
                break
            except Exception:
                continue

    if added:
        log("Capture restricted to the landscape via %s (%d actor(s))."
            % ("show_only_actor_components", added))
        return True

    # 3. Last resort: hide everything else explicitly.
    try:
        all_actors = unreal.get_editor_subsystem(
            unreal.EditorActorSubsystem
        ).get_all_level_actors()

        keep = set()
        for actor in landscape_actors:
            try:
                keep.add(actor.get_path_name())
            except Exception:
                pass

        hidden = [
            a for a in all_actors
            if a.get_path_name() not in keep
        ]

        component.set_editor_property(
            "primitive_render_mode",
            unreal.SceneCapturePrimitiveRenderMode.PRM_LEGACY_SCENE_CAPTURE,
        )
        component.set_editor_property("hidden_actors", hidden)

        log("Capture restricted by hiding %d other actor(s)." % len(hidden))
        return True

    except Exception as exc:
        warn("Could not restrict the capture to the landscape (%s). "
             "Props would be baked into the terrain texture, so the bake "
             "is skipped rather than producing a wrong texture." % exc)
        return False


def bake_base_color(world, landscape_actors, center_x, center_y, half_size,
                    z_top, output_png_path):
    """Top-down orthographic BaseColor capture of the landscape only.

    Returns True on success. The capture actor is always destroyed, even
    on failure, so repeated runs cannot litter the level.
    """
    capture_actor = None

    try:
        # GAMMA: SCS_BASE_COLOR writes LINEAR values. Godot reads an albedo
        # PNG as sRGB, so a linearly-encoded PNG would come out washed out.
        # An sRGB render target encodes on write, which makes the PNG match
        # what Godot expects. Older/other engine versions may not expose the
        # sRGB variant, hence the fallback.
        render_format = getattr(
            unreal.TextureRenderTargetFormat, "RTF_RGBA8_SRGB", None
        )

        if render_format is None:
            render_format = unreal.TextureRenderTargetFormat.RTF_RGBA8
            warn("RTF_RGBA8_SRGB unavailable - baking to a linear RGBA8 "
                 "target. If the terrain looks washed out in Godot, set the "
                 "imported texture to non-sRGB, or bake through a material.")

        render_target = unreal.RenderingLibrary.create_render_target2d(
            world,
            TEXTURE_RESOLUTION,
            TEXTURE_RESOLUTION,
            render_format,
        )

        if render_target is None:
            warn("create_render_target2d returned None - no texture baked.")
            return False

        # Camera straight above the centre, looking down.
        # With pitch = -90, yaw = 0: screen right = world +Y, screen up =
        # world +X. build_grid_mesh() derives its UVs from exactly that.
        location = unreal.Vector(center_x, center_y, z_top)
        rotation = unreal.Rotator(roll=0.0, pitch=-90.0, yaw=0.0)

        capture_actor = None

        try:
            capture_actor = unreal.get_editor_subsystem(
                unreal.EditorActorSubsystem
            ).spawn_actor_from_class(
                unreal.SceneCapture2D, location, rotation
            )
        except Exception:
            capture_actor = None

        if capture_actor is None:
            capture_actor = unreal.EditorLevelLibrary.spawn_actor_from_class(
                unreal.SceneCapture2D, location, rotation
            )

        if capture_actor is None:
            warn("Could not spawn SceneCapture2D - no texture baked.")
            return False

        component = _get_capture_component(capture_actor)

        component.set_editor_property("texture_target", render_target)
        component.set_editor_property(
            "capture_source", unreal.SceneCaptureSource.SCS_BASE_COLOR
        )
        component.set_editor_property(
            "projection_type", unreal.CameraProjectionMode.ORTHOGRAPHIC
        )
        component.set_editor_property("ortho_width", half_size * 2.0)

        # Only the landscape. Without this the props standing on the
        # terrain would be painted into the terrain texture itself, so a
        # failure here aborts the bake instead of producing a wrong image.
        if not _restrict_capture_to(component, landscape_actors):
            return False

        component.set_editor_property("capture_every_frame", False)
        component.set_editor_property("capture_on_movement", False)

        try:
            component.set_editor_property("always_persist_rendering_state", True)
        except Exception:
            pass

        component.capture_scene()

        directory = os.path.dirname(output_png_path)
        filename = os.path.basename(output_png_path)

        os.makedirs(directory, exist_ok=True)

        unreal.RenderingLibrary.export_render_target(
            world, render_target, directory, filename
        )

        if not os.path.isfile(output_png_path):
            warn("export_render_target did not produce %s" % output_png_path)
            return False

        size = os.path.getsize(output_png_path)

        if size < 1024:
            warn("Baked texture is suspiciously small (%d bytes)." % size)
            return False

        log("BaseColor baked: %s (%.1f MB, %d x %d)"
            % (output_png_path, size / (1024.0 * 1024.0),
               TEXTURE_RESOLUTION, TEXTURE_RESOLUTION))

        return True

    except Exception:
        traceback.print_exc()
        return False

    finally:
        if capture_actor is not None:
            destroyed = False
            try:
                unreal.get_editor_subsystem(
                    unreal.EditorActorSubsystem
                ).destroy_actor(capture_actor)
                destroyed = True
            except Exception:
                pass

            if not destroyed:
                try:
                    capture_actor.destroy_actor()
                    destroyed = True
                except Exception:
                    pass

            if not destroyed:
                warn("Could not destroy the temporary SceneCapture2D - "
                     "delete it by hand if it is still in the level.")


# ======================================================================
# MESH CONSTRUCTION
# ======================================================================

def build_grid_mesh(heights, side, x_min, x_max, y_min, y_max):
    """Turns the sampled heightfield into indexed triangles.

    Vertices are emitted in Godot space (AXIS_MAP). Only grid points that
    were actually hit become vertices, and a quad is emitted only when its
    four corners were all hit, so holes stay holes instead of being filled
    with an invented height.

    Returns (positions, normals, uvs, indices) as flat Python lists.
    """
    resolution = side - 1
    span_x = x_max - x_min
    span_y = y_max - y_min

    def height_at(ix, iy):
        if ix < 0 or iy < 0 or ix >= side or iy >= side:
            return None
        return heights[ix * side + iy]

    # --- vertex compaction -------------------------------------------
    index_of = [-1] * (side * side)
    positions = []
    normals = []
    uvs = []

    step_x = span_x / float(resolution)
    step_y = span_y / float(resolution)

    for ix in range(side):
        for iy in range(side):

            z = height_at(ix, iy)
            if z is None:
                continue

            x = x_min + span_x * (ix / float(resolution))
            y = y_min + span_y * (iy / float(resolution))

            index_of[ix * side + iy] = len(positions) // 3
            positions.extend(AXIS_MAP(x, y, z))

            # Normal from central differences on the heightfield, falling
            # back to one-sided differences at the border and around holes.
            zx_prev = height_at(ix - 1, iy)
            zx_next = height_at(ix + 1, iy)
            zy_prev = height_at(ix, iy - 1)
            zy_next = height_at(ix, iy + 1)

            if zx_prev is not None and zx_next is not None:
                dzdx = (zx_next - zx_prev) / (2.0 * step_x)
            elif zx_next is not None:
                dzdx = (zx_next - z) / step_x
            elif zx_prev is not None:
                dzdx = (z - zx_prev) / step_x
            else:
                dzdx = 0.0

            if zy_prev is not None and zy_next is not None:
                dzdy = (zy_next - zy_prev) / (2.0 * step_y)
            elif zy_next is not None:
                dzdy = (zy_next - z) / step_y
            elif zy_prev is not None:
                dzdy = (z - zy_prev) / step_y
            else:
                dzdy = 0.0

            # Surface normal in UE space, then mapped like a direction.
            nx, ny, nz = -dzdx, -dzdy, 1.0
            length = math.sqrt(nx * nx + ny * ny + nz * nz) or 1.0
            gx, gy, gz = AXIS_MAP(nx / length, ny / length, nz / length)

            # AXIS_MAP also applies the cm->m scale, which is uniform, so
            # re-normalising restores a unit direction.
            glen = math.sqrt(gx * gx + gy * gy + gz * gz) or 1.0
            normals.extend((gx / glen, gy / glen, gz / glen))

            # Planar UVs matching the orthographic capture:
            # screen right = world +Y, screen up = world +X, and glTF v
            # grows downwards from the top of the image.
            # UVs describe where each vertex samples the capture, which is
            # unaffected by the axis mapping: the camera framing did not
            # change. Left as-is on purpose.
            u = (y - y_min) / span_y if span_y else 0.0
            v = (x_max - x) / span_x if span_x else 0.0
            uvs.extend((u, v))

    # --- triangles ----------------------------------------------------
    indices = []
    skipped_quads = 0

    for ix in range(resolution):
        for iy in range(resolution):

            a = index_of[ix * side + iy]
            b = index_of[ix * side + (iy + 1)]
            c = index_of[(ix + 1) * side + iy]
            d = index_of[(ix + 1) * side + (iy + 1)]

            if a < 0 or b < 0 or c < 0 or d < 0:
                skipped_quads += 1
                continue

            # V2: AXIS_MAP now has determinant -1 (handedness flip), which
            # reverses triangle winding. The index order is flipped here to
            # compensate, otherwise every face would point downwards and the
            # terrain would be invisible from above (doubleSided is false).
            indices.extend((a, b, c))
            indices.extend((b, d, c))

    if skipped_quads:
        log("  %d quad(s) skipped (untraced holes)" % skipped_quads)

    return positions, normals, uvs, indices


# ======================================================================
# GLB WRITER
# ======================================================================

def _pad(data, alignment=4, filler=b"\x00"):
    remainder = len(data) % alignment
    if remainder == 0:
        return data
    return data + filler * (alignment - remainder)


def write_glb(path, positions, normals, uvs, indices, png_bytes, name):
    """Writes a self-contained binary glTF 2.0 file.

    Layout: one buffer, five bufferViews (positions, normals, uvs,
    indices, embedded PNG), four accessors, one PBR material with the
    baked BaseColor as its base colour texture.
    """
    vertex_count = len(positions) // 3

    if vertex_count == 0 or not indices:
        raise ValueError("refusing to write an empty landscape mesh")

    pos_bytes = struct.pack("<%df" % len(positions), *positions)
    nrm_bytes = struct.pack("<%df" % len(normals), *normals)
    uv_bytes = struct.pack("<%df" % len(uvs), *uvs)
    idx_bytes = struct.pack("<%dI" % len(indices), *indices)

    chunks = []
    views = []
    offset = 0

    def add_view(data, target=None):
        nonlocal offset
        padded = _pad(data)
        view = {
            "buffer": 0,
            "byteOffset": offset,
            "byteLength": len(data),
        }
        if target is not None:
            view["target"] = target
        views.append(view)
        chunks.append(padded)
        offset += len(padded)
        return len(views) - 1

    ARRAY_BUFFER = 34962
    ELEMENT_ARRAY_BUFFER = 34963

    pos_view = add_view(pos_bytes, ARRAY_BUFFER)
    nrm_view = add_view(nrm_bytes, ARRAY_BUFFER)
    uv_view = add_view(uv_bytes, ARRAY_BUFFER)
    idx_view = add_view(idx_bytes, ELEMENT_ARRAY_BUFFER)

    # Accessor min/max is required by the spec for POSITION.
    xs = positions[0::3]
    ys = positions[1::3]
    zs = positions[2::3]

    accessors = [
        {
            "bufferView": pos_view,
            "componentType": 5126,          # FLOAT
            "count": vertex_count,
            "type": "VEC3",
            "min": [min(xs), min(ys), min(zs)],
            "max": [max(xs), max(ys), max(zs)],
        },
        {
            "bufferView": nrm_view,
            "componentType": 5126,
            "count": vertex_count,
            "type": "VEC3",
        },
        {
            "bufferView": uv_view,
            "componentType": 5126,
            "count": vertex_count,
            "type": "VEC2",
        },
        {
            "bufferView": idx_view,
            "componentType": 5125,          # UNSIGNED_INT
            "count": len(indices),
            "type": "SCALAR",
        },
    ]

    gltf = {
        "asset": {
            "version": "2.0",
            "generator": "unreal_export_landscape.py (UE5 -> Godot pipeline)",
        },
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0, "name": name}],
        "meshes": [{
            "name": name,
            "primitives": [{
                "attributes": {
                    "POSITION": 0,
                    "NORMAL": 1,
                    "TEXCOORD_0": 2,
                },
                "indices": 3,
                "material": 0,
                "mode": 4,                  # TRIANGLES
            }],
        }],
        "accessors": accessors,
        "bufferViews": views,
        "materials": [{
            "name": name + "_Material",
            "pbrMetallicRoughness": {
                "metallicFactor": 0.0,
                "roughnessFactor": 1.0,
            },
            "doubleSided": False,
        }],
    }

    if png_bytes:
        image_view = add_view(png_bytes)
        gltf["images"] = [{
            "bufferView": image_view,
            "mimeType": "image/png",
            "name": name + "_BaseColor",
        }]
        # Clamp: the baked texture covers the terrain exactly once, so
        # repeating it would smear the edges.
        gltf["samplers"] = [{
            "magFilter": 9729,              # LINEAR
            "minFilter": 9987,              # LINEAR_MIPMAP_LINEAR
            "wrapS": 33071,                 # CLAMP_TO_EDGE
            "wrapT": 33071,
        }]
        gltf["textures"] = [{"sampler": 0, "source": 0}]
        gltf["materials"][0]["pbrMetallicRoughness"]["baseColorTexture"] = {
            "index": 0
        }
    else:
        gltf["materials"][0]["pbrMetallicRoughness"]["baseColorFactor"] = [
            0.35, 0.32, 0.28, 1.0
        ]

    binary = b"".join(chunks)
    gltf["buffers"] = [{"byteLength": len(binary)}]

    json_bytes = _pad(
        json.dumps(gltf, separators=(",", ":")).encode("utf-8"),
        4,
        b" ",
    )
    binary = _pad(binary)

    total = 12 + 8 + len(json_bytes) + 8 + len(binary)

    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)

    with open(path, "wb") as handle:
        handle.write(b"glTF")
        handle.write(struct.pack("<I", 2))
        handle.write(struct.pack("<I", total))
        handle.write(struct.pack("<I", len(json_bytes)))
        handle.write(b"JSON")
        handle.write(json_bytes)
        handle.write(struct.pack("<I", len(binary)))
        handle.write(b"BIN\x00")
        handle.write(binary)

    return total


# ======================================================================
# REGISTRATION
# ======================================================================

def register_in_asset_map(godot_path, disk_path, triangle_count, vertex_count):
    """Adds the landscape to the asset map the constructor resolves through."""
    payload = {}

    if os.path.isfile(ASSET_MAP_PATH):
        try:
            with open(ASSET_MAP_PATH, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except Exception:
            traceback.print_exc()
            warn("Asset map unreadable - a new one will be written.")
            payload = {}

    if not isinstance(payload, dict):
        payload = {}

    # The constructor validates this against the manifest version.
    payload.setdefault("manifest_version", "10.0")
    payload.setdefault("format", "glb")
    payload.setdefault("godot_asset_root", "res://UEAssets")
    payload.setdefault("godot_mesh_root", GODOT_MESH_ROOT)
    payload.setdefault("assets", {})
    payload.setdefault("failures", [])

    payload["assets"][LANDSCAPE_UE_PATH] = {
        "ue_path": LANDSCAPE_UE_PATH,
        "ue_name": "BakedLandscape",
        "godot_path": godot_path,
        "disk_path": disk_path.replace("\\", "/"),
        "format": "glb",
        "status": "EXPORTED",
        "source": "unreal_export_landscape.py",
        "axis_map": AXIS_MAP_LABEL,
        "vertex_count": vertex_count,
        "triangle_count": triangle_count,
        "note": (
            "Vertices are pre-converted to Godot space; the matching "
            "manifest placement therefore carries an identity transform."
        ),
    }

    # exported_count is only a consistency hint for the constructor; keep
    # it truthful now that one more asset exists.
    if isinstance(payload.get("exported_count"), int):
        payload["exported_count"] = len(payload["assets"])

    with open(ASSET_MAP_PATH, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)

    log("Asset map updated: %s" % ASSET_MAP_PATH)


def identity_transform():
    return {
        "location": [0.0, 0.0, 0.0],
        "rotation": {"pitch": 0.0, "yaw": 0.0, "roll": 0.0},
        "scale": [1.0, 1.0, 1.0],
    }


def register_in_manifest(manifest, triangle_count, vertex_count, bounds):
    """Appends the landscape as a normal geometry placement.

    The constructor needs no change: it looks the mesh path up in the
    asset map and applies reconstruction_transform, which is identity
    here because the vertices are already in Godot space.
    """
    geometry = manifest.setdefault("geometry", {})
    placements = geometry.setdefault("placements", [])
    unique_meshes = geometry.setdefault("unique_meshes", {})

    placements[:] = [
        p for p in placements
        if (p.get("mesh") or {}).get("path") != LANDSCAPE_UE_PATH
    ]

    unique_meshes[LANDSCAPE_UE_PATH] = {
        "path": LANDSCAPE_UE_PATH,
        "name": "BakedLandscape",
        "class": "StaticMesh",
        "source": "unreal_export_landscape.py",
        "triangle_count": triangle_count,
        "vertex_count": vertex_count,
    }

    placements.append({
        "kind": "static_mesh",
        "actor": {
            "path": LANDSCAPE_UE_PATH,
            "name": "BakedLandscape",
            "class": "BakedLandscape",
        },
        "component": {
            "path": LANDSCAPE_UE_PATH + ".Mesh",
            "name": "BakedLandscapeMesh",
            "class": "StaticMeshComponent",
        },
        "source_level": None,
        "level_instance_chain": [],
        "mesh": {"path": LANDSCAPE_UE_PATH, "name": "BakedLandscape"},
        "materials": [],
        "instance_count": 1,
        "placement_id": "MESH_BAKED_LANDSCAPE",
        "transform": identity_transform(),
        "source_transform": identity_transform(),
        "final_world_transform": identity_transform(),
        "reconstruction_transform": identity_transform(),
        "transform_space": "godot_space_baked",
        "baked_landscape": {
            "axis_map": AXIS_MAP_LABEL,
            "grid_resolution": GRID_RESOLUTION,
            "texture_resolution": TEXTURE_RESOLUTION,
            "capture_source": "SCS_BASE_COLOR",
            "ue_bounds_min": bounds[0],
            "ue_bounds_max": bounds[1],
        },
    })

    # The manifest flags Landscape as a HIGH conversion risk. It is no
    # longer unresolved, so say so instead of leaving a stale warning.
    for risk in manifest.get("conversion_risks", []):
        if risk.get("type") == "LANDSCAPE":
            risk["severity"] = "INFO"
            risk["message"] = (
                "Landscape exported as a baked GLB (geometry + BaseColor) "
                "by unreal_export_landscape.py."
            )

    with open(MANIFEST_PATH, "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2, ensure_ascii=False)

    log("Manifest updated: %s" % MANIFEST_PATH)


# ======================================================================
# MAIN
# ======================================================================

def main():
    start = time.time()

    log("=" * 60)
    log("UE5.5 -> GODOT LANDSCAPE EXPORT")
    log("=" * 60)

    if not os.path.isfile(MANIFEST_PATH):
        warn("Manifest not found: %s - run the manifest script first."
             % MANIFEST_PATH)
        return

    try:
        with open(MANIFEST_PATH, "r", encoding="utf-8") as handle:
            manifest = json.load(handle)
    except Exception:
        traceback.print_exc()
        return

    world = get_editor_world()
    if world is None:
        warn("No editor world available.")
        return

    primary, landscape_actors = find_landscape_actors()
    if primary is None:
        warn("No Landscape actor in the level - nothing to export.")
        return

    log("Landscape: '%s' (%d actor(s) including proxies)"
        % (primary.get_actor_label(), len(landscape_actors)))

    bounds = landscape_bounds(landscape_actors)
    if bounds is None:
        warn("Landscape bounds unavailable.")
        return

    bmin, bmax = bounds

    center_x = (bmin[0] + bmax[0]) * 0.5
    center_y = (bmin[1] + bmax[1]) * 0.5

    # A square footprint keeps the orthographic capture and the UVs on the
    # same grid: the render target is square, so its vertical extent equals
    # ortho_width. Covering the larger axis guarantees nothing is cropped.
    half_size = max(bmax[0] - bmin[0], bmax[1] - bmin[1]) * 0.5

    x_min, x_max = center_x - half_size, center_x + half_size
    y_min, y_max = center_y - half_size, center_y + half_size

    log("Bounds  X %.0f..%.0f  Y %.0f..%.0f  Z %.0f..%.0f (cm)"
        % (bmin[0], bmax[0], bmin[1], bmax[1], bmin[2], bmax[2]))
    log("Square footprint: %.0f x %.0f cm, grid %d x %d"
        % (half_size * 2, half_size * 2, GRID_RESOLUTION, GRID_RESOLUTION))

    # ---- geometry ----------------------------------------------------
    heights, side = sample_heightfield(
        world, x_min, x_max, y_min, y_max,
        bmax[2] + TRACE_MARGIN_CM, bmin[2] - TRACE_MARGIN_CM,
        GRID_RESOLUTION,
    )

    if not any(h is not None for h in heights):
        warn("No trace hit the landscape. Is its collision enabled?")
        return

    positions, normals, uvs, indices = build_grid_mesh(
        heights, side, x_min, x_max, y_min, y_max
    )

    vertex_count = len(positions) // 3
    triangle_count = len(indices) // 3

    log("Mesh: %d vertices, %d triangles" % (vertex_count, triangle_count))

    # ---- texture -----------------------------------------------------
    png_path = os.path.join(MESH_OUTPUT_DIR, LANDSCAPE_PNG_NAME)

    baked = bake_base_color(
        world, landscape_actors, center_x, center_y, half_size,
        bmax[2] + TRACE_MARGIN_CM, png_path,
    )

    png_bytes = b""
    if baked:
        try:
            with open(png_path, "rb") as handle:
                png_bytes = handle.read()
        except Exception:
            traceback.print_exc()
            png_bytes = b""

    if not png_bytes:
        warn("No baked texture - the terrain will use a flat colour. "
             "The geometry is still correct.")

    # ---- package -----------------------------------------------------
    glb_path = os.path.join(MESH_OUTPUT_DIR, LANDSCAPE_GLB_NAME)

    try:
        total = write_glb(
            glb_path, positions, normals, uvs, indices,
            png_bytes, "BakedLandscape",
        )
    except Exception:
        traceback.print_exc()
        return

    log("GLB written: %s (%.1f MB)" % (glb_path, total / (1024.0 * 1024.0)))

    # ---- register ----------------------------------------------------
    godot_path = GODOT_MESH_ROOT + "/" + LANDSCAPE_GLB_NAME

    register_in_asset_map(godot_path, glb_path, triangle_count, vertex_count)
    register_in_manifest(manifest, triangle_count, vertex_count, (bmin, bmax))

    log("")
    log("=" * 60)
    log("DONE in %.1f s" % (time.time() - start))
    log("Vertices / triangles : %d / %d" % (vertex_count, triangle_count))
    log("Baked BaseColor      : %s" % ("yes" if png_bytes else "NO"))
    log("Axis map             : %s" % AXIS_MAP_LABEL)
    log("")
    log("Copy C:/Export/GodotAssets into the Godot project as res://UEAssets/")
    log("and re-copy level_manifest_v10.json, then run the constructor.")
    log("The constructor needs no change - it resolves the terrain through")
    log("the asset map like any other mesh.")
    log("=" * 60)


main()