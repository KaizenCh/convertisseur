# -*- coding: utf-8 -*-
"""
======================================================================
UE5.5 -> GODOT  DECAL + VFX EXPORTER  (script 5 of the pipeline)
======================================================================

RUN ORDER (this one goes after the landscape, still before copying):

    1. ue5_preprocess_detach_all.py     (once, on the working copy)
    2. unreal_export_manifest_v10.py    -> level_manifest_v10.json
    3. unreal_export_godot_assets.py    -> Meshes/*.glb + asset map
    4. unreal_export_landscape.py       -> landscape glb, registers itself
    5. unreal_export_decals_vfx.py      (THIS)  -> Decals/*.png + registers
    6. ue5_godot_map_constructor.gd

WHAT WAS MISSING
----------------
The manifest already describes 1423 decals and 422 Niagara components -
transform, size, material, system, every field correct. But nothing ever
turned that description into something Godot can show: no texture was
written to disk and no node was created. This script closes the decal
half of that gap, and gives the VFX half an honest landing place.

DECALS
------
Only five distinct decal materials cover all 1423 placements, so the
per-material work is tiny:

    MI_decal_leak_01   781      MI_decal_dirt_01    149
    MI_decal_mould_01  317      MI_decal_blood_02     9
    MI_decal_dirt_02   167

The instances override no textures - those live on the shared parent
M_Decals_01 - so the source textures are resolved from the parent's
default parameter values, which is possible here precisely because the
whole Necropolis pack is present in the project.

Getting a texture out of Unreal needs a render target, so each one is
drawn through a throwaway material into an RT and exported as PNG. The
mask that comes back is greyscale coverage; a Godot Decal wants RGBA with
coverage in the ALPHA channel. Neither Unreal's Python API nor the editor
composites two images, so png_codec.py (pure stdlib) builds the final
RGBA: colour from the material's Tint parameter, alpha from the mask.

Every composed texture is checked: if a mask comes back completely flat,
the decal would render as a solid rectangle, so that is reported loudly
instead of being shipped as if it were fine.

VFX
---
A Niagara system cannot be converted. Its behaviour lives in proprietary
modules with no Godot equivalent, and pretending otherwise would produce
something that looks converted but is not. So this exports PLACEMENT ONLY:
every Niagara component becomes an entry with its world transform and its
system name, and the constructor turns each into a marker node carrying
that metadata.

341 of the 422 systems are NS_candle_flame. Those are worth handling by
hand later in Godot (a small GPUParticles3D plus a flickering OmniLight3D
gets most of the look), and the markers put them exactly where they
belong so that work is placement-free.

OUTPUT
    C:/Export/GodotAssets/Decals/DEC_<material>.png
    C:/Export/GodotAssets/ue5_godot_decal_map.json
"""

import json
import os
import time
import traceback

import unreal


# ======================================================================
# CONFIGURATION
# ======================================================================

MANIFEST_PATH = r"C:/Export/level_manifest_v10.json"
OUTPUT_ROOT = r"C:/Export/GodotAssets"
DECAL_OUTPUT_DIR = os.path.join(OUTPUT_ROOT, "Decals")
DECAL_MAP_PATH = os.path.join(OUTPUT_ROOT, "ue5_godot_decal_map.json")

GODOT_DECAL_ROOT = "res://UEAssets/Decals"

# Where png_codec.py sits. Same folder as this script by default.
PNG_CODEC_DIR = os.path.dirname(os.path.abspath(__file__))

# Texture parameter names to try, in order, when looking for the coverage
# mask and the normal map on the parent material.
MASK_PARAMETER_NAMES = (
    "Decal Mask", "DecalMask", "Mask", "Opacity Mask",
    "Diffuse", "Difuse", "BaseColor", "Base Color", "Albedo",
)
NORMAL_PARAMETER_NAMES = (
    "Normal Map", "NormalMap", "Normal",
)

# Vector parameters to try for the decal colour, in order.
TINT_PARAMETER_NAMES = ("Tint 02", "Tint02", "Tint 01", "Tint01", "Tint", "Color")

BAKE_RESOLUTION = 1024

# Some packs author the mask so that white means "no decal". If the
# exported decals look inverted, flip this.
INVERT_MASK = False

# Take coverage from the mask image's own alpha channel instead of its
# luminance. Only useful if the source texture stores it there.
MASK_FROM_ALPHA = False

EXPORT_NORMALS = True


def log(message):
    unreal.log("[UE5->Godot Decals] " + str(message))


def warn(message):
    unreal.log_warning("[UE5->Godot Decals] " + str(message))


# ======================================================================
# PNG CODEC LOADING
# ======================================================================

def load_png_codec():
    """Loads png_codec.py as a module, without requiring sys.path setup."""
    path = os.path.join(PNG_CODEC_DIR, "png_codec.py")

    if not os.path.isfile(path):
        warn("png_codec.py not found next to this script (%s). Decal "
             "textures cannot be composed without it." % path)
        return None

    namespace = {"__name__": "png_codec", "__file__": path}

    try:
        with open(path, "r", encoding="utf-8") as handle:
            exec(compile(handle.read(), path, "exec"), namespace)
    except Exception:
        traceback.print_exc()
        return None

    module = type("png_codec", (), namespace)
    return module


# ======================================================================
# MATERIAL / TEXTURE RESOLUTION
# ======================================================================

def load_asset(path):
    if not path:
        return None
    try:
        if not unreal.EditorAssetLibrary.does_asset_exist(path):
            return None
        return unreal.load_asset(path)
    except Exception:
        return None


def resolve_texture_parameter(material, candidate_names):
    """Finds a texture on a material instance or on its parent chain.

    Instances here override no textures, so the value almost always comes
    from the parent's default - get_material_default_texture_parameter_value
    handles exactly that case.
    """
    MEL = unreal.MaterialEditingLibrary

    # 1. explicit override on the instance
    if isinstance(material, unreal.MaterialInstance):
        try:
            entries = material.get_editor_property("texture_parameter_values")
        except Exception:
            entries = []

        for entry in entries:
            try:
                name = str(entry.parameter_info.name)
                value = entry.get_editor_property("parameter_value")
            except Exception:
                continue
            if value is None:
                continue
            for candidate in candidate_names:
                if name.lower().replace(" ", "") == candidate.lower().replace(" ", ""):
                    return value, name, "instance override"

    # 2. parent material defaults
    parent = material
    if isinstance(material, unreal.MaterialInstance):
        try:
            parent = material.get_editor_property("parent")
        except Exception:
            parent = None

    if parent is None:
        return None, None, None

    for candidate in candidate_names:
        try:
            value = MEL.get_material_default_texture_parameter_value(parent, candidate)
        except Exception:
            value = None
        if isinstance(value, unreal.Texture):
            return value, candidate, "parent default"

    # 3. last resort - any texture parameter the parent exposes
    try:
        names = MEL.get_texture_parameter_names(parent)
    except Exception:
        names = []

    for name in names:
        lowered = str(name).lower()
        for candidate in candidate_names:
            if candidate.lower().replace(" ", "") in lowered.replace(" ", ""):
                try:
                    value = MEL.get_material_default_texture_parameter_value(
                        parent, name)
                except Exception:
                    value = None
                if isinstance(value, unreal.Texture):
                    return value, str(name), "parent scan"

    return None, None, None


def resolve_tint(material):
    """Returns (r, g, b) in 0..1 for the decal's colour."""
    MEL = unreal.MaterialEditingLibrary

    if isinstance(material, unreal.MaterialInstance):
        try:
            entries = material.get_editor_property("vector_parameter_values")
        except Exception:
            entries = []

        by_name = {}
        for entry in entries:
            try:
                by_name[str(entry.parameter_info.name)] = entry.get_editor_property(
                    "parameter_value")
            except Exception:
                continue

        for candidate in TINT_PARAMETER_NAMES:
            for name, value in by_name.items():
                if name.lower().replace(" ", "") == candidate.lower().replace(" ", ""):
                    if value is not None:
                        return (value.r, value.g, value.b), name

        parent = None
        try:
            parent = material.get_editor_property("parent")
        except Exception:
            parent = None

        if parent is not None:
            for candidate in TINT_PARAMETER_NAMES:
                try:
                    value = MEL.get_material_default_vector_parameter_value(
                        parent, candidate)
                except Exception:
                    value = None
                if value is not None:
                    return (value.r, value.g, value.b), candidate + " (parent)"

    # Neutral white: the mask alone then carries the whole look, which is
    # wrong but obvious, rather than silently dark.
    return (1.0, 1.0, 1.0), None


# ======================================================================
# TEXTURE -> PNG
# ======================================================================

def export_texture_to_png(world, texture, output_path):
    """Draws a Texture2D through a throwaway material into a render target
    and exports it as PNG, which is the only route Python has to get raw
    texture pixels out of Unreal.
    """
    MEL = unreal.MaterialEditingLibrary
    material = None

    try:
        package = "/Game/AutoTerrain/_DecalBake"
        name = "M_DecalBakeTemp"
        asset_path = package + "/" + name

        if unreal.EditorAssetLibrary.does_asset_exist(asset_path):
            unreal.EditorAssetLibrary.delete_asset(asset_path)

        unreal.EditorAssetLibrary.make_directory(package)

        material = unreal.AssetToolsHelpers.get_asset_tools().create_asset(
            name, package, unreal.Material, unreal.MaterialFactoryNew())

        if material is None:
            warn("Could not create the temporary bake material.")
            return False

        # Unlit + emissive: draw_material_to_render_target writes the
        # emissive output, so routing the texture there gives back the
        # raw pixels rather than something lit.
        material.set_editor_property(
            "shading_model", unreal.MaterialShadingModel.MSM_UNLIT)

        sample = MEL.create_material_expression(
            material, unreal.MaterialExpressionTextureSample, -300, 0)
        sample.set_editor_property("texture", texture)

        MEL.connect_material_property(
            sample, "RGB", unreal.MaterialProperty.MP_EMISSIVE_COLOR)
        MEL.recompile_material(material)

        render_format = getattr(
            unreal.TextureRenderTargetFormat, "RTF_RGBA8_SRGB", None)
        if render_format is None:
            render_format = unreal.TextureRenderTargetFormat.RTF_RGBA8

        render_target = unreal.RenderingLibrary.create_render_target2d(
            world, BAKE_RESOLUTION, BAKE_RESOLUTION, render_format)

        if render_target is None:
            warn("create_render_target2d returned None.")
            return False

        unreal.RenderingLibrary.draw_material_to_render_target(
            world, render_target, material)

        directory = os.path.dirname(output_path)
        filename = os.path.basename(output_path)
        os.makedirs(directory, exist_ok=True)

        unreal.RenderingLibrary.export_render_target(
            world, render_target, directory, filename)

        return os.path.isfile(output_path) and os.path.getsize(output_path) > 512

    except Exception:
        traceback.print_exc()
        return False

    finally:
        try:
            if material is not None:
                unreal.EditorAssetLibrary.delete_asset(material.get_path_name())
        except Exception:
            pass


# ======================================================================
# DECAL EXPORT
# ======================================================================

def safe_name(value):
    out = []
    for char in str(value or "decal"):
        out.append(char if (char.isalnum() or char in "_-") else "_")
    return "".join(out).strip("_") or "decal"


def export_decal_materials(world, manifest, png):
    """One composed RGBA texture per distinct decal material."""
    decals = manifest.get("effects", {}).get("decals", [])

    if not decals:
        warn("No decals in the manifest.")
        return {}

    by_material = {}
    for entry in decals:
        material_info = entry.get("material") or {}
        path = material_info.get("path")
        if path:
            by_material.setdefault(path, 0)
            by_material[path] += 1

    log("%d decal placements across %d distinct material(s)"
        % (len(decals), len(by_material)))

    results = {}

    for material_path, count in sorted(by_material.items(), key=lambda kv: -kv[1]):

        material = load_asset(material_path)

        if material is None:
            warn("Material not found: %s" % material_path)
            continue

        label = safe_name(material_path.rsplit(".", 1)[-1])
        log("  %-22s %4d placement(s)" % (label, count))

        mask_texture, mask_param, mask_origin = resolve_texture_parameter(
            material, MASK_PARAMETER_NAMES)

        if mask_texture is None:
            warn("    no mask texture found - skipped (the decal would be a "
                 "solid rectangle without it)")
            continue

        log("    mask  : %s  [%s, %s]"
            % (mask_texture.get_name(), mask_param, mask_origin))

        tint, tint_name = resolve_tint(material)
        log("    tint  : (%.3f, %.3f, %.3f)  [%s]"
            % (tint[0], tint[1], tint[2], tint_name or "none - using white"))

        raw_path = os.path.join(DECAL_OUTPUT_DIR, "_raw_%s.png" % label)
        final_path = os.path.join(DECAL_OUTPUT_DIR, "DEC_%s.png" % label)

        if not export_texture_to_png(world, mask_texture, raw_path):
            warn("    mask export failed - skipped")
            continue

        try:
            width, height, mean, lowest, highest = png.compose_tinted_rgba(
                raw_path, final_path, tint,
                mask_from_alpha=MASK_FROM_ALPHA, invert=INVERT_MASK)
        except Exception:
            traceback.print_exc()
            warn("    compositing failed - skipped")
            continue

        entry = {
            "ue_material_path": material_path,
            "name": label,
            "godot_path": GODOT_DECAL_ROOT + "/DEC_%s.png" % label,
            "disk_path": final_path.replace("\\", "/"),
            "placement_count": count,
            "tint": list(tint),
            "tint_parameter": tint_name,
            "mask_texture": mask_texture.get_name(),
            "mask_parameter": mask_param,
            "resolution": [width, height],
            "alpha_mean": round(mean, 2),
            "alpha_min": lowest,
            "alpha_max": highest,
        }

        # A mask with no variation means every decal using it renders as a
        # solid rectangle. Say so here rather than letting it be discovered
        # in the Godot viewport.
        if lowest == highest:
            entry["warning"] = (
                "mask is completely flat (alpha %d everywhere) - this decal "
                "will be a solid rectangle. Try INVERT_MASK or "
                "MASK_FROM_ALPHA, or a different mask parameter." % lowest)
            warn("    " + entry["warning"])
        else:
            log("    alpha : mean %.0f, range %d..%d  (%dx%d)"
                % (mean, lowest, highest, width, height))

        if EXPORT_NORMALS:
            normal_texture, normal_param, _ = resolve_texture_parameter(
                material, NORMAL_PARAMETER_NAMES)
            if normal_texture is not None:
                normal_path = os.path.join(
                    DECAL_OUTPUT_DIR, "DEC_%s_normal.png" % label)
                if export_texture_to_png(world, normal_texture, normal_path):
                    entry["normal_godot_path"] = (
                        GODOT_DECAL_ROOT + "/DEC_%s_normal.png" % label)
                    log("    normal: %s" % normal_texture.get_name())

        try:
            os.remove(raw_path)
        except Exception:
            pass

        results[material_path] = entry

    return results


# ======================================================================
# MAIN
# ======================================================================

def main():
    start = time.time()

    log("=" * 60)
    log("UE5.5 -> GODOT DECAL + VFX EXPORT")
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

    png = load_png_codec()
    if png is None:
        warn("Aborting: png_codec.py is required to build the decal textures.")
        return

    try:
        world = unreal.get_editor_subsystem(
            unreal.UnrealEditorSubsystem).get_editor_world()
    except Exception:
        world = None

    if world is None:
        warn("No editor world available.")
        return

    os.makedirs(DECAL_OUTPUT_DIR, exist_ok=True)

    materials = export_decal_materials(world, manifest, png)

    niagara = manifest.get("effects", {}).get("niagara", [])
    systems = {}
    for entry in niagara:
        name = (entry.get("system") or {}).get("name") or "unknown"
        systems[name] = systems.get(name, 0) + 1

    payload = {
        "exporter_version": "1.0",
        "manifest_version": str(manifest.get("manifest_version", "")),
        "godot_decal_root": GODOT_DECAL_ROOT,
        "decal_materials": materials,
        "decal_placement_total": len(
            manifest.get("effects", {}).get("decals", [])),
        "vfx": {
            "converted": False,
            "reason": (
                "Niagara systems have no Godot equivalent and their behaviour "
                "lives in engine-specific modules. Placement is exported so "
                "the effects can be rebuilt in Godot without re-placing them."
            ),
            "systems": systems,
            "placement_total": len(niagara),
        },
        "notes": [
            "Copy C:/Export/GodotAssets/Decals into the Godot project as "
            "res://UEAssets/Decals/.",
            "The constructor reads effects.decals from the manifest and this "
            "file to build Decal nodes.",
            "Decal textures are RGBA: colour from the material Tint, alpha "
            "from the mask texture.",
        ],
    }

    try:
        with open(DECAL_MAP_PATH, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
    except Exception:
        traceback.print_exc()
        return

    log("")
    log("=" * 60)
    log("DONE in %.1f s" % (time.time() - start))
    log("Decal materials exported : %d / %d"
        % (len(materials), len(set(
            (d.get("material") or {}).get("path")
            for d in manifest.get("effects", {}).get("decals", [])
            if (d.get("material") or {}).get("path")))))
    log("Decal placements covered : %d"
        % sum(m["placement_count"] for m in materials.values()))
    log("VFX placements recorded  : %d (markers only, not converted)"
        % len(niagara))
    for name, count in sorted(systems.items(), key=lambda kv: -kv[1]):
        log("    %-22s %4d" % (name, count))
    log("")
    log("Map written              : %s" % DECAL_MAP_PATH)
    log("Copy Decals/ into res://UEAssets/Decals/ and re-copy the manifest.")
    log("=" * 60)


main()
