

"""
============================================================
UE5 -> GODOT CONVERSION MANIFEST - V10
Unreal Engine 5.5
============================================================

V10 = FINAL UE-SIDE MANIFEST FOR GODOT RECONSTRUCTION

BASE:
    V4 / V5 / V6 / V7 / V8

PRESERVES:
    - proven LevelInstance enumeration
    - direct actor enumeration
    - ObjectIterator(Actor) + actor.get_level()
    - nested LevelInstance traversal
    - canonical LevelInstance registry
    - LI deduplication by ACTOR PATH
    - placement preservation
    - source World registry
    - hierarchy
    - mesh registry
    - material registry
    - transforms
    - Blueprint classification
    - component inventory
    - conversion diagnostics

V10 ADDS:
    - material parent information
    - Material Instance parameters
    - referenced texture registry
    - texture/material reverse references
    - additional StaticMesh diagnostics
    - collision / LOD / Nanite best-effort information
    - detailed Blueprint component inventory
    - detailed component diagnostics
    - detailed Niagara / Decal / Light / Audio information
    - correct LevelInstance chain on component records
    - scan timings
    - stronger consistency diagnostics
    - TXT summary
    - final world-space reconstruction transforms

IMPORTANT:
    The V8 LevelInstance scanner is NOT replaced.

LevelInstance enumeration remains:

    actor.get_loaded_level()
    +
    unreal.ObjectIterator(unreal.Actor)
    filtered by:

        actor.get_level() == loaded_level

IMPORTANT:
    V10 composes nested LevelInstance transforms using Unreal FTransform
    composition. The original source transforms remain preserved.

V10.5 FIX (transform composition order):
    compose_chain_transform(), and the equivalent standalone LevelInstance
    final-transform pass, were calling compose_transform_dicts(A, B) with
    A = the already-accumulated parent transform and B = the next (more
    nested) local transform - i.e. parent * child. Unreal's FTransform
    composes the other way around: child * parent (see USceneComponent's
    NewTransform = RelativeTransform * ParentToWorld). Pure translations
    are order-independent, so this only produced visibly wrong results
    once a LevelInstance in the chain had a non-identity rotation - at
    that point descendant objects were rotated by the wrong basis and
    ended up scattered across effectively unrelated positions in the
    final scene. Both call sites now pass (child, parent) in that order.
    This affects meshes, lights, audio, Niagara, decals, particles, and
    Blueprint components alike, since they all resolve their final world
    transform through compose_chain_transform().

No destructive operation is performed.

Outputs:
    C:/Export/level_manifest_v9.json
    C:/Export/level_manifest_v9.txt
============================================================
"""

import unreal
import json
import os
import traceback
import time
import hashlib
from collections import Counter


# ============================================================
# CONFIGURATION
# ============================================================

OUTPUT_PATH = r"C:/Export/level_manifest_v10.json"
TXT_OUTPUT_PATH = r"C:/Export/level_manifest_v10.txt"

MAX_LEVEL_INSTANCE_DEPTH = 64


# ============================================================
# TIMINGS
# ============================================================

TIMINGS = {}


# ============================================================
# SAFE HELPERS
# ============================================================

def safe_call(function, default=None):
    try:
        return function()
    except Exception:
        return default


def safe_property(obj, property_name, default=None):
    try:
        return obj.get_editor_property(property_name)
    except Exception:
        return default


def object_path(obj):

    if obj is None:
        return None

    try:
        return obj.get_path_name()
    except Exception:
        pass

    try:
        return obj.get_name()
    except Exception:
        return str(obj)


def object_name(obj):

    if obj is None:
        return None

    try:
        return obj.get_name()
    except Exception:
        return str(obj)


def class_name(obj):

    if obj is None:
        return None

    try:
        return obj.get_class().get_name()
    except Exception:

        try:
            return obj.__class__.__name__
        except Exception:
            return None


def class_path(obj):

    if obj is None:
        return None

    try:
        return obj.get_class().get_path_name()
    except Exception:
        return None


def safe_int(value, default=None):

    try:
        return int(value)
    except Exception:
        return default


def safe_float(value, default=None):

    try:
        return float(value)
    except Exception:
        return default


def safe_bool(value, default=None):

    try:
        return bool(value)
    except Exception:
        return default


# ============================================================
# SERIALIZATION HELPERS
# ============================================================

def vector_to_list(vector):

    if vector is None:
        return None

    try:
        return [
            float(vector.x),
            float(vector.y),
            float(vector.z)
        ]
    except Exception:
        return None


def color_to_dict(color):

    if color is None:
        return None

    try:
        return {
            "r": float(color.r),
            "g": float(color.g),
            "b": float(color.b),
            "a": float(color.a)
        }
    except Exception:
        return None


def rotator_to_dict(rotator):

    if rotator is None:
        return None

    try:
        return {
            "pitch": float(rotator.pitch),
            "yaw": float(rotator.yaw),
            "roll": float(rotator.roll)
        }
    except Exception:
        return None


def transform_to_dict(transform):

    if transform is None:
        return None

    try:

        return {
            "location":
                vector_to_list(
                    transform.translation
                ),

            "rotation":
                rotator_to_dict(
                    transform.rotation.rotator()
                ),

            "scale":
                vector_to_list(
                    transform.scale3d
                )
        }

    except Exception:
        return None


def actor_transform(actor):

    return safe_call(
        lambda:
            transform_to_dict(
                actor.get_actor_transform()
            ),
        None
    )


def component_transform(component):

    return safe_call(
        lambda:
            transform_to_dict(
                component.get_component_transform()
            ),
        None
    )


def _try_refetch_stale_component(component):
    """
    Attempts to get a fresh, live handle to the same component by its
    stable object path, to work around a stale ObjectIterator reference.
    Returns the fresh component, or None if that isn't possible - never
    raises, so it's always safe to call speculatively.
    """

    try:
        path = component.get_path_name()
    except Exception:
        return None

    if not path:
        return None

    for loader_name in ("find_object", "load_object"):
        loader = getattr(unreal, loader_name, None)
        if loader is None:
            continue
        try:
            fresh = loader(None, path)
        except Exception:
            fresh = None
        if fresh is not None:
            try:
                if unreal.SystemLibrary.is_valid(fresh):
                    return fresh
            except Exception:
                return fresh

    return None


def _relative_transform_dict(component):
    """Reads a component's relative transform through the PROPERTY system
    (get_editor_property), not through method bindings.

    V10.3 root cause: on this project every DecalComponent /
    NiagaraComponent / LightComponent / AudioComponent raises
    AttributeError on get_component_transform(), while
    get_editor_property() on the very same objects returns their material,
    size, intensity, colour, volume and pitch perfectly well. The objects
    are alive - only the transform *method* is unavailable on those
    classes' Python bindings, which is why the stale-reference re-fetch
    could never fix it. Relative location/rotation/scale are UPROPERTYs,
    so they are readable exactly like every other property that works."""

    location = safe_property(component, "relative_location", None)
    rotation = safe_property(component, "relative_rotation", None)
    scale = safe_property(component, "relative_scale3d", None)

    if location is None and rotation is None and scale is None:
        return None

    location_list = vector_to_list(location) or [0.0, 0.0, 0.0]
    rotation_dict = rotator_to_dict(rotation) or {
        "pitch": 0.0, "yaw": 0.0, "roll": 0.0
    }
    scale_list = vector_to_list(scale) or [1.0, 1.0, 1.0]

    return {
        "location": location_list,
        "rotation": rotation_dict,
        "scale": scale_list
    }


def component_transform_via_properties(component, actor=None, max_depth=32):
    """Rebuilds a component's world transform from PROPERTIES only.

    V10.8 FIX (measured: 327 placements landed on the world origin).

    The previous version walked attach_parent to the ROOT component and
    used the root's own relative transform as the world transform. That
    holds for a plain StaticMeshActor, but NOT for a Blueprint actor: there
    the root component's relative transform is identity and the world
    position lives on the ACTOR. The walk therefore returned (0,0,0) with
    no error at all, and every Blueprint-owned mesh - 94 small pillars, 50
    big pillars, 39 torch sconces, fences, statues, pots - was piled up on
    the origin while its actor_transform sat there, correct, unused.

    So the actor's world transform is the base (get_actor_transform() is
    reliable: zero failures across the whole scene), and only the relative
    transforms of NON-ROOT components are composed onto it. Skipping the
    root's own relative avoids both the identity case above and
    double-counting on actors where root-relative does equal the actor
    transform.

    Returns (transform_dict, error_message).
    """

    chain = []
    current = component
    seen = set()
    depth = 0

    while current is not None and depth < max_depth:

        try:
            key = current.get_path_name()
        except Exception:
            key = None

        if key is not None:
            if key in seen:
                break
            seen.add(key)

        parent = safe_property(current, "attach_parent", None)

        # The root component (no attach parent) is deliberately NOT added:
        # the actor transform below already represents it.
        if parent is None:
            break

        relative = _relative_transform_dict(current)

        if relative is None:
            return None, (
                "relative_location/relative_rotation/relative_scale3d "
                "unreadable on " + str(key)
            )

        chain.append(relative)
        current = parent
        depth += 1

    if depth >= max_depth:
        return None, "attach_parent chain exceeded max depth"

    base = None

    if actor is not None:
        base = actor_transform(actor)

    if base is None:
        # No actor available: fall back to the component's own relative
        # transform, which is right for a root component on an unattached
        # actor and is still better than returning nothing.
        base = _relative_transform_dict(component)

        if base is None:
            return None, "no actor transform and no readable relative transform"

        return base, None

    result = base

    for relative in reversed(chain):

        result = compose_transform_dicts(result, relative)

        if result is None:
            return None, "compose_transform_dicts() failed while walking attach_parent"

    return result, None


def component_transform_diagnostic(component, actor=None):
    """
    V10.1: root-cause capture for geometry transform failures.
    component_transform() above is left completely untouched (still used
    everywhere it was before). This is a separate function, used only for
    StaticMesh/ISM/HISM placements, that returns (value, error_message)
    instead of silently swallowing the exception, so a failure can be
    traced to an actual cause instead of just a count.

    V10.1.1: some components come back from ObjectIterator as STALE
    references - their class name still reflects correctly (so they were
    classified as "static_mesh" fine), but the derived class' Python
    methods are gone, raising AttributeError on get_component_transform().
    This typically happens when a Blueprint's Construction Script
    re-generated its instance components while Python still held a
    reference to the old ones (e.g. a LevelInstance sub-level finishing
    streaming mid-scan). We detect this and attempt one live re-fetch of
    the *current* component by its stable path before giving up - this
    can genuinely recover the transform instead of just explaining why
    it was missing.
    """

    is_valid_fn = getattr(unreal, "SystemLibrary", None)
    if is_valid_fn is not None:
        try:
            if not unreal.SystemLibrary.is_valid(component):
                component = _try_refetch_stale_component(component) or component
        except Exception:
            pass

    try:
        raw_transform = component.get_component_transform()
    except AttributeError as exc:
        # The class name still says StaticMeshComponent, but the actual
        # bound methods are gone -> classic stale/pending-kill reference.
        refetched = _try_refetch_stale_component(component)
        if refetched is not None:
            try:
                raw_transform = refetched.get_component_transform()
                result = transform_to_dict(raw_transform)
                if result is not None:
                    return result, None
            except Exception:
                pass
        # V10.3: the method is simply not available on this component class'
        # Python bindings (proven: properties on the same object read fine).
        # Rebuild the transform from relative_* properties instead.
        fallback_value, fallback_error = component_transform_via_properties(component, actor)
        if fallback_value is not None:
            return fallback_value, None
        return None, (
            "get_component_transform() unavailable ("
            + str(exc)
            + ") and property fallback failed: "
            + str(fallback_error)
        )
    except Exception as exc:
        fallback_value, fallback_error = component_transform_via_properties(component, actor)
        if fallback_value is not None:
            return fallback_value, None
        return None, (
            "get_component_transform() raised: "
            + str(exc)
            + " and property fallback failed: "
            + str(fallback_error)
        )

    try:
        result = transform_to_dict(raw_transform)
    except Exception as exc:
        return None, "transform_to_dict() raised: " + str(exc)

    if result is None:
        return None, "transform_to_dict() returned None (unreadable transform fields)"

    return result, None


# ============================================================
# BLUEPRINT DETECTION
# ============================================================

def is_blueprint_generated_actor(actor):

    cpath = class_path(actor)

    if not cpath:
        return False

    if not cpath.endswith("_C"):
        return False

    if cpath.startswith("/Game/"):
        return True

    if cpath.startswith("/Plugins/"):
        return True

    if cpath.startswith("/Plugin/"):
        return True

    return False


def actor_origin_type(actor):

    category = actor_category(actor)

    if category == "level_instance":
        return "level_instance"

    if category == "world_partition_system":
        return "world_partition_system"

    if is_blueprint_generated_actor(actor):
        return "blueprint"

    cls = class_name(actor)

    if cls:
        return "native"

    return "unknown"


# ============================================================
# ACTOR CATEGORY
# ============================================================

def actor_category(actor):

    cls = class_name(actor)

    if not cls:
        return "other"

    if "WorldPartition" in cls:
        return "world_partition_system"

    if "LevelInstance" in cls:
        return "level_instance"

    if "Landscape" in cls:
        return "landscape"

    if "Foliage" in cls:
        return "foliage"

    if "Decal" in cls:
        return "decal"

    if "Light" in cls:
        return "light"

    if "Niagara" in cls or "Particle" in cls:
        return "particle"

    if "Volume" in cls:
        return "volume"

    if "Audio" in cls:
        return "audio"

    if (
        "Sky" in cls
        or "Atmosphere" in cls
        or "Cloud" in cls
        or "Fog" in cls
    ):
        return "environment"

    if "StaticMeshActor" in cls:
        return "static_mesh"

    if "SkeletalMeshActor" in cls:
        return "skeletal_mesh"

    return "other"


# ============================================================
# COMPONENT CLASSIFICATION
# ============================================================

def component_kind(component):

    cls = class_name(component)

    if not cls:
        return "other"

    if "HierarchicalInstancedStaticMesh" in cls:
        return "hierarchical_instanced_mesh"

    if "InstancedStaticMesh" in cls:
        return "instanced_mesh"

    if "StaticMeshComponent" in cls:
        return "static_mesh"

    if "SkeletalMeshComponent" in cls:
        return "skeletal_mesh"

    if "Niagara" in cls:
        return "niagara"

    if "Decal" in cls:
        return "decal"

    if "Light" in cls:
        return "light"

    if "Audio" in cls:
        return "audio"

    if "Spline" in cls:
        return "spline"

    if "Particle" in cls:
        return "particle"

    if "Collision" in cls:
        return "collision"

    if "Camera" in cls:
        return "camera"

    if "Widget" in cls:
        return "ui"

    return "other"


# ============================================================
# GENERIC ASSET INFO
# ============================================================

def asset_info(asset):

    if asset is None:
        return None

    return {
        "path": object_path(asset),
        "name": object_name(asset),
        "class": class_name(asset),
        "class_path": class_path(asset)
    }


# ============================================================
# MATERIAL
# ============================================================

def material_info(material):

    if material is None:
        return None

    return {
        "path":
            object_path(material),

        "name":
            object_name(material),

        "class":
            class_name(material),

        "class_path":
            class_path(material)
    }


def is_material_instance(material):

    if material is None:
        return False

    cls = class_name(material)

    if not cls:
        return False

    return "MaterialInstance" in cls


def get_material_parent(material):

    if material is None:
        return None

    parent = safe_call(
        lambda:
            material.get_editor_property(
                "parent"
            ),
        None
    )

    if parent is not None:
        return parent

    parent = safe_call(
        lambda:
            material.get_editor_property(
                "parent_material"
            ),
        None
    )

    return parent


def parameter_value_to_dict(value):

    if value is None:
        return None

    result = {
        "python_type":
            type(value).__name__,
        "value":
            None
    }

    try:

        if hasattr(value, "r"):

            result["value"] = {
                "r": float(value.r),
                "g": float(value.g),
                "b": float(value.b),
                "a": float(value.a)
            }

            return result

    except Exception:
        pass

    try:

        if hasattr(value, "x"):

            result["value"] = {
                "x": float(value.x),
                "y": float(value.y),
                "z": float(value.z),
                "w": float(value.w)
                if hasattr(value, "w")
                else None
            }

            return result

    except Exception:
        pass

    try:

        result["value"] = float(value)
        return result

    except Exception:
        pass

    try:

        result["value"] = str(value)

    except Exception:
        result["value"] = None

    return result


def material_instance_parameters(material):

    result = {

        "is_material_instance":
            is_material_instance(material),

        "parent":
            material_info(
                get_material_parent(material)
            ),

        "scalar_parameters": [],
        "vector_parameters": [],
        "texture_parameters": [],
        "runtime_virtual_texture_parameters": [],
        "static_switch_parameters": [],
        "extraction_notes": []
    }

    if material is None:
        return result

    # --------------------------------------------------------
    # Scalar
    # --------------------------------------------------------

    scalar_values = safe_call(
        lambda:
            material.get_editor_property(
                "scalar_parameter_values"
            ),
        None
    )

    if scalar_values is not None:

        try:

            for entry in scalar_values:

                name = safe_property(
                    entry,
                    "parameter_info",
                    None
                )

                parameter_name = None

                if name is not None:
                    parameter_name = safe_property(
                        name,
                        "name",
                        None
                    )

                if parameter_name is None:
                    parameter_name = safe_property(
                        entry,
                        "parameter_name",
                        None
                    )

                value = safe_property(
                    entry,
                    "parameter_value",
                    None
                )

                result[
                    "scalar_parameters"
                ].append({

                    "name":
                        str(parameter_name)
                        if parameter_name is not None
                        else None,

                    "value":
                        safe_float(
                            value,
                            None
                        )
                })

        except Exception:
            result[
                "extraction_notes"
            ].append(
                "scalar_parameter_values_unreadable"
            )

    # --------------------------------------------------------
    # Vector
    # --------------------------------------------------------

    vector_values = safe_call(
        lambda:
            material.get_editor_property(
                "vector_parameter_values"
            ),
        None
    )

    if vector_values is not None:

        try:

            for entry in vector_values:

                info = safe_property(
                    entry,
                    "parameter_info",
                    None
                )

                parameter_name = None

                if info is not None:
                    parameter_name = safe_property(
                        info,
                        "name",
                        None
                    )

                if parameter_name is None:
                    parameter_name = safe_property(
                        entry,
                        "parameter_name",
                        None
                    )

                value = safe_property(
                    entry,
                    "parameter_value",
                    None
                )

                result[
                    "vector_parameters"
                ].append({

                    "name":
                        str(parameter_name)
                        if parameter_name is not None
                        else None,

                    "value":
                        color_to_dict(value)
                        if value is not None
                        else None
                })

        except Exception:

            result[
                "extraction_notes"
            ].append(
                "vector_parameter_values_unreadable"
            )

    # --------------------------------------------------------
    # Texture
    # --------------------------------------------------------

    texture_values = safe_call(
        lambda:
            material.get_editor_property(
                "texture_parameter_values"
            ),
        None
    )

    if texture_values is not None:

        try:

            for entry in texture_values:

                info = safe_property(
                    entry,
                    "parameter_info",
                    None
                )

                parameter_name = None

                if info is not None:
                    parameter_name = safe_property(
                        info,
                        "name",
                        None
                    )

                if parameter_name is None:
                    parameter_name = safe_property(
                        entry,
                        "parameter_name",
                        None
                    )

                value = safe_property(
                    entry,
                    "parameter_value",
                    None
                )

                result[
                    "texture_parameters"
                ].append({

                    "name":
                        str(parameter_name)
                        if parameter_name is not None
                        else None,

                    "texture":
                        asset_info(value)
                })

        except Exception:

            result[
                "extraction_notes"
            ].append(
                "texture_parameter_values_unreadable"
            )

    # --------------------------------------------------------
    # Static switches
    # --------------------------------------------------------

    switch_values = safe_call(
        lambda:
            material.get_editor_property(
                "static_parameters"
            ),
        None
    )

    if switch_values is not None:

        try:

            result[
                "static_switch_parameters"
            ].append({
                "raw_class":
                    class_name(switch_values)
            })

        except Exception:
            pass

    # --------------------------------------------------------
    # Best-effort note
    # --------------------------------------------------------

    if not result[
        "texture_parameters"
    ]:

        result[
            "extraction_notes"
        ].append(
            "No explicit texture parameter records "
            "were exposed by the current UE Python API."
        )

    return result


def material_full_info(material):

    if material is None:
        return None

    result = material_info(material)

    result[
        "material_instance"
    ] = material_instance_parameters(
        material
    )

    return result


def component_materials(component):

    result = []

    try:

        count = component.get_num_materials()

        for index in range(count):

            material = component.get_material(
                index
            )

            if material:

                result.append({

                    "slot":
                        index,

                    "path":
                        object_path(material),

                    "name":
                        object_name(material),

                    "class":
                        class_name(material),

                    "class_path":
                        class_path(material)
                })

    except Exception:
        pass

    return result


# ============================================================
# TEXTURE REGISTRY HELPERS
# ============================================================

def texture_info(texture):

    if texture is None:
        return None

    result = {

        "path":
            object_path(texture),

        "name":
            object_name(texture),

        "class":
            class_name(texture),

        "class_path":
            class_path(texture),

        "width":
            None,

        "height":
            None,

        "size_x":
            None,

        "size_y":
            None,

        "srgb":
            None,

        "format":
            None,

        "compression":
            None
    }

    for property_name, output_key in (
        ("size_x", "size_x"),
        ("size_y", "size_y"),
        ("width", "width"),
        ("height", "height")
    ):

        value = safe_property(
            texture,
            property_name,
            None
        )

        if value is not None:

            result[
                output_key
            ] = safe_int(
                value,
                None
            )

    srgb = safe_property(
        texture,
        "srgb",
        None
    )

    if srgb is not None:
        result["srgb"] = safe_bool(
            srgb,
            None
        )

    pixel_format = safe_property(
        texture,
        "pixel_format",
        None
    )

    if pixel_format is not None:
        result["format"] = str(
            pixel_format
        )

    compression = safe_property(
        texture,
        "compression_settings",
        None
    )

    if compression is not None:
        result["compression"] = str(
            compression
        )

    return result


# ============================================================
# STATIC MESH
# ============================================================

def mesh_collision_info(mesh):

    result = {

        "body_setup":
            None,

        "collision_mesh":
            None,

        "customized_collision":
            None,

        "collision_profile":
            None,

        "lod_for_collision":
            None
    }

    if mesh is None:
        return result

    body_setup = safe_property(
        mesh,
        "body_setup",
        None
    )

    if body_setup is not None:

        result[
            "body_setup"
        ] = asset_info(
            body_setup
        )

        complex_collision = safe_property(
            body_setup,
            "complex_collision_mesh",
            None
        )

        if complex_collision is not None:

            result[
                "collision_mesh"
            ] = asset_info(
                complex_collision
            )

        customized = safe_property(
            body_setup,
            "customized_collision",
            None
        )

        if customized is not None:

            result[
                "customized_collision"
            ] = safe_bool(
                customized,
                None
            )

    lod = safe_property(
        mesh,
        "lod_for_collision",
        None
    )

    if lod is not None:

        result[
            "lod_for_collision"
        ] = safe_int(
            lod,
            None
        )

    return result


def mesh_lod_info(mesh):

    result = {

        "lod_count":
            None,

        "lods":
            []
    }

    if mesh is None:
        return result

    lod_count = safe_call(
        lambda:
            mesh.get_num_lods(),
        None
    )

    if lod_count is not None:

        result[
            "lod_count"
        ] = safe_int(
            lod_count,
            None
        )

    if lod_count is None:
        return result

    for index in range(
        int(lod_count)
    ):

        lod_record = {
            "lod_index": index,
            "screen_size": None
        }

        # Best effort only.
        try:

            screen_size = mesh.get_lod_screen_size(
                index
            )

            lod_record[
                "screen_size"
            ] = safe_float(
                screen_size,
                None
            )

        except Exception:
            pass

        result[
            "lods"
        ].append(
            lod_record
        )

    return result


def mesh_nanite_info(mesh):

    result = {

        "enabled":
            None,

        "fallback_percent_triangles":
            None,

        "fallback_relative_error":
            None
    }

    if mesh is None:
        return result

    # UE Python API varies between versions.
    # Never assume availability.

    for property_name in (
        "nanite_enabled",
        "enable_nanite"
    ):

        value = safe_property(
            mesh,
            property_name,
            None
        )

        if value is not None:

            result[
                "enabled"
            ] = safe_bool(
                value,
                None
            )

            break

    for property_name in (
        "nanite_fallback_percent_triangles",
        "fallback_percent_triangles"
    ):

        value = safe_property(
            mesh,
            property_name,
            None
        )

        if value is not None:

            result[
                "fallback_percent_triangles"
            ] = safe_float(
                value,
                None
            )

            break

    for property_name in (
        "nanite_fallback_relative_error",
        "fallback_relative_error"
    ):

        value = safe_property(
            mesh,
            property_name,
            None
        )

        if value is not None:

            result[
                "fallback_relative_error"
            ] = safe_float(
                value,
                None
            )

            break

    return result


def mesh_material_slot_info(mesh):

    result = []

    if mesh is None:
        return result

    count = safe_call(
        lambda:
            mesh.get_num_materials(),
        None
    )

    if count is None:
        return result

    for index in range(
        int(count)
    ):

        material = safe_call(
            lambda i=index:
                mesh.get_material(i),
            None
        )

        result.append({

            "slot":
                index,

            "material":
                material_info(material)
        })

    return result


def mesh_info(mesh):

    if mesh is None:
        return None

    result = {

        "path":
            object_path(mesh),

        "name":
            object_name(mesh),

        "class":
            class_name(mesh),

        "class_path":
            class_path(mesh),

        "bounds":
            None,

        "lod_count":
            None,

        "lod":
            None,

        "collision":
            None,

        "nanite":
            None,

        "material_slots":
            [],

        "material_slot_count":
            None
    }

    try:

        bounds = mesh.get_bounds()

        result[
            "bounds"
        ] = {

            "origin":
                vector_to_list(
                    bounds.origin
                ),

            "box_extent":
                vector_to_list(
                    bounds.box_extent
                ),

            "sphere_radius":
                float(
                    bounds.sphere_radius
                )
        }

    except Exception:
        pass

    result[
        "lod"
    ] = mesh_lod_info(mesh)

    result[
        "lod_count"
    ] = result[
        "lod"
    ].get(
        "lod_count"
    )

    result[
        "collision"
    ] = mesh_collision_info(
        mesh
    )

    result[
        "nanite"
    ] = mesh_nanite_info(
        mesh
    )

    result[
        "material_slots"
    ] = mesh_material_slot_info(
        mesh
    )

    result[
        "material_slot_count"
    ] = len(
        result[
            "material_slots"
        ]
    )

    return result


# ============================================================
# STATIC MESH COMPONENT
# ============================================================

def static_mesh_component_info(
    actor,
    component,
    source_level=None,
    level_instance_chain=None
):

    mesh = safe_property(
        component,
        "static_mesh",
        None
    )

    cls = class_name(component)

    if cls is None:
        cls = ""

    if "HierarchicalInstancedStaticMesh" in cls:

        kind = (
            "hierarchical_instanced_mesh"
        )

    elif "InstancedStaticMesh" in cls:

        kind = "instanced_mesh"

    else:

        kind = "static_mesh"

    instance_count = 1

    instance_transforms = None
    instance_transform_errors = []

    if kind in (
        "instanced_mesh",
        "hierarchical_instanced_mesh"
    ):

        try:

            instance_count = int(
                component.get_instance_count()
            )

        except Exception:

            instance_count = 0

        # V10.4: previously only instance_count (a number) was stored -
        # the manifest never captured WHERE each of those instances
        # actually is, so only the component's own single transform ever
        # reached Godot. On this project that silently dropped up to 165
        # instances down to 1 for a single foliage ISM. Read every real
        # instance transform now.
        if instance_count > 0:

            instance_transforms = []

            for _instance_index in range(instance_count):

                _raw_instance_transform = None

                for _attempt in (
                    lambda: component.get_instance_transform(
                        _instance_index, True
                    ),
                    lambda: component.get_instance_transform(
                        _instance_index, world_space=True
                    ),
                    lambda: component.get_instance_transform(
                        instance_index=_instance_index,
                        world_space=True
                    ),
                ):
                    try:
                        _candidate = _attempt()
                    except Exception:
                        _candidate = None
                    if _candidate is not None:
                        _raw_instance_transform = _candidate
                        break

                if _raw_instance_transform is None:
                    instance_transform_errors.append(_instance_index)
                    instance_transforms.append(None)
                    continue

                try:
                    instance_transforms.append(
                        transform_to_dict(_raw_instance_transform)
                    )
                except Exception:
                    instance_transform_errors.append(_instance_index)
                    instance_transforms.append(None)

    collision = {}

    try:

        collision[
            "enabled"
        ] = str(
            component.get_editor_property(
                "collision_enabled"
            )
        )

    except Exception:
        pass

    try:

        body_instance = (
            component.get_editor_property(
                "body_instance"
            )
        )

        collision[
            "profile"
        ] = str(
            body_instance.get_editor_property(
                "collision_profile_name"
            )
        )

    except Exception:
        pass

    _component_transform_value, _component_transform_error = (
        component_transform_diagnostic(component, actor)
    )

    return {

        "kind":
            kind,

        "actor": {

            "path":
                object_path(actor),

            "name":
                object_name(actor),

            "class":
                class_name(actor),

            "class_path":
                class_path(actor),

            "origin_type":
                actor_origin_type(actor)
        },

        "component": {

            "path":
                object_path(component),

            "name":
                object_name(component),

            "class":
                cls,

            "class_path":
                class_path(component)
        },

        "source_level":
            object_path(source_level),

        "level_instance_chain":
            level_instance_chain or [],

        "mesh":
            mesh_info(mesh),

        "transform":
            _component_transform_value,

        "transform_extraction_error":
            _component_transform_error,

        "actor_transform":
            actor_transform(actor),

        "materials":
            component_materials(component),

        "mobility":
            str(
                safe_property(
                    component,
                    "mobility",
                    None
                )
            ),

        "collision":
            collision,

        "instance_count":
            instance_count,

        "instance_transforms":
            instance_transforms,

        "instance_transform_errors":
            instance_transform_errors
    }


# ============================================================
# DECAL
# ============================================================

def decal_component_info(
    actor,
    component,
    source_level=None,
    level_instance_chain=None
):

    material = safe_property(
        component,
        "decal_material",
        None
    )

    size = safe_property(
        component,
        "decal_size",
        None
    )

    # V10.2: same stale-reference diagnostic + live re-fetch already proven
    # for StaticMesh (component_transform_diagnostic), now applied here too -
    # DecalComponent placements inside Blueprint LevelInstances hit the exact
    # same stale ObjectIterator reference, and were previously silently
    # returning transform=None via the plain component_transform().
    _transform_value, _transform_error = component_transform_diagnostic(component, actor)

    return {

        "actor": {

            "path":
                object_path(actor),

            "name":
                object_name(actor),

            "class":
                class_name(actor),

            "class_path":
                class_path(actor)
        },

        "component": {

            "path":
                object_path(component),

            "name":
                object_name(component),

            "class":
                class_name(component),

            "class_path":
                class_path(component)
        },

        "source_level":
            object_path(source_level),

        "level_instance_chain":
            level_instance_chain or [],

        "transform":
            _transform_value,

        "transform_extraction_error":
            _transform_error,

        "material":
            material_full_info(material),

        "size":
            vector_to_list(size)
    }


# ============================================================
# NIAGARA
# ============================================================

def niagara_component_info(
    actor,
    component,
    source_level=None,
    level_instance_chain=None
):

    system = None

    for property_name in (
        "asset",
        "template"
    ):

        value = safe_property(
            component,
            property_name,
            None
        )

        if value:

            system = asset_info(
                value
            )

            break

    _transform_value, _transform_error = component_transform_diagnostic(component, actor)

    return {

        "actor": {

            "path":
                object_path(actor),

            "name":
                object_name(actor),

            "class":
                class_name(actor),

            "class_path":
                class_path(actor)
        },

        "component": {

            "path":
                object_path(component),

            "name":
                object_name(component),

            "class":
                class_name(component),

            "class_path":
                class_path(component)
        },

        "source_level":
            object_path(source_level),

        "level_instance_chain":
            level_instance_chain or [],

        "transform":
            _transform_value,

        "transform_extraction_error":
            _transform_error,

        "system":
            system,

        "auto_activate":
            safe_property(
                component,
                "auto_activate",
                None
            ),

        "active":
            safe_call(
                lambda:
                    component.is_active(),
                None
            )
    }


# ============================================================
# LIGHT
# ============================================================

def light_component_info(
    actor,
    component,
    source_level=None,
    level_instance_chain=None
):

    _transform_value, _transform_error = component_transform_diagnostic(component, actor)

    result = {

        "actor": {

            "path":
                object_path(actor),

            "name":
                object_name(actor),

            "class":
                class_name(actor),

            "class_path":
                class_path(actor)
        },

        "component": {

            "path":
                object_path(component),

            "name":
                object_name(component),

            "class":
                class_name(component),

            "class_path":
                class_path(component)
        },

        "source_level":
            object_path(source_level),

        "level_instance_chain":
            level_instance_chain or [],

        "transform":
            _transform_value,

        "transform_extraction_error":
            _transform_error,

        "type":
            class_name(component),

        "intensity":
            None,

        "color":
            None,

        "attenuation_radius":
            None,

        "cast_shadows":
            None,

        "mobility":
            None
    }

    result[
        "intensity"
    ] = safe_float(
        safe_property(
            component,
            "intensity",
            None
        ),
        None
    )

    color = safe_property(
        component,
        "light_color",
        None
    )

    result[
        "color"
    ] = color_to_dict(
        color
    )

    result[
        "attenuation_radius"
    ] = safe_float(
        safe_property(
            component,
            "attenuation_radius",
            None
        ),
        None
    )

    result[
        "cast_shadows"
    ] = safe_bool(
        safe_property(
            component,
            "cast_shadows",
            None
        ),
        None
    )

    mobility = safe_property(
        component,
        "mobility",
        None
    )

    if mobility is not None:
        result[
            "mobility"
        ] = str(
            mobility
        )

    return result


# ============================================================
# AUDIO
# ============================================================

def audio_component_info(
    actor,
    component,
    source_level=None,
    level_instance_chain=None
):

    sound = None

    for property_name in (
        "sound",
        "sound_asset"
    ):

        value = safe_property(
            component,
            property_name,
            None
        )

        if value:

            sound = asset_info(
                value
            )

            break

    _transform_value, _transform_error = component_transform_diagnostic(component, actor)

    return {

        "actor": {

            "path":
                object_path(actor),

            "name":
                object_name(actor),

            "class":
                class_name(actor)
        },

        "component": {

            "path":
                object_path(component),

            "name":
                object_name(component),

            "class":
                class_name(component)
        },

        "source_level":
            object_path(source_level),

        "level_instance_chain":
            level_instance_chain or [],

        "transform":
            _transform_value,

        "transform_extraction_error":
            _transform_error,

        "sound":
            sound,

        "volume":
            safe_float(
                safe_property(
                    component,
                    "volume_multiplier",
                    None
                ),
                None
            ),

        "pitch":
            safe_float(
                safe_property(
                    component,
                    "pitch_multiplier",
                    None
                ),
                None
            ),

        "attenuation":
            asset_info(
                safe_property(
                    component,
                    "attenuation_settings",
                    None
                )
            )
    }


# ============================================================
# LANDSCAPE
# ============================================================

def landscape_info(actor):

    result = {

        "actor": {

            "path":
                object_path(actor),

            "name":
                object_name(actor),

            "class":
                class_name(actor),

            "class_path":
                class_path(actor)
        },

        "transform":
            actor_transform(actor),

        "bounds":
            None
    }

    try:

        origin, extent = actor.get_actor_bounds(
            False
        )

        result[
            "bounds"
        ] = {

            "origin":
                vector_to_list(origin),

            "extent":
                vector_to_list(extent)
        }

    except Exception:
        pass

    return result


# ============================================================
# COMPONENT SCAN
# ============================================================

def get_actor_components(actor):

    try:

        return actor.get_components_by_class(
            unreal.ActorComponent
        )

    except Exception:

        return []


def component_statistics(components):

    counter = Counter()

    for component in components:

        counter[
            component_kind(component)
        ] += 1

    return dict(counter)


# ============================================================
# BLUEPRINT / CUSTOM ACTOR
# ============================================================

def blueprint_component_detail(
    component
):

    kind = component_kind(
        component
    )

    detail = {

        "path":
            object_path(component),

        "name":
            object_name(component),

        "class":
            class_name(component),

        "class_path":
            class_path(component),

        "kind":
            kind,

        "transform":
            component_transform(component)
    }

    if kind in (
        "static_mesh",
        "instanced_mesh",
        "hierarchical_instanced_mesh"
    ):

        mesh = safe_property(
            component,
            "static_mesh",
            None
        )

        detail[
            "mesh"
        ] = asset_info(
            mesh
        )

        detail[
            "materials"
        ] = component_materials(
            component
        )

        if kind in (
            "instanced_mesh",
            "hierarchical_instanced_mesh"
        ):

            detail[
                "instance_count"
            ] = safe_int(
                safe_call(
                    lambda:
                        component.get_instance_count(),
                    0
                ),
                0
            )

    elif kind == "niagara":

        for property_name in (
            "asset",
            "template"
        ):

            value = safe_property(
                component,
                property_name,
                None
            )

            if value:

                detail[
                    "system"
                ] = asset_info(
                    value
                )

                break

    elif kind == "decal":

        detail[
            "material"
        ] = material_full_info(
            safe_property(
                component,
                "decal_material",
                None
            )
        )

        detail[
            "size"
        ] = vector_to_list(
            safe_property(
                component,
                "decal_size",
                None
            )
        )

    elif kind == "light":

        detail[
            "intensity"
        ] = safe_float(
            safe_property(
                component,
                "intensity",
                None
            ),
            None
        )

        detail[
            "color"
        ] = color_to_dict(
            safe_property(
                component,
                "light_color",
                None
            )
        )

    elif kind == "audio":

        detail[
            "sound"
        ] = asset_info(
            safe_property(
                component,
                "sound",
                None
            )
        )

    return detail


def blueprint_actor_info(
    actor,
    components,
    source_level=None,
    level_instance_chain=None
):

    component_counter = Counter()

    component_details = []

    for component in components:

        kind = component_kind(
            component
        )

        component_counter[
            kind
        ] += 1

        component_details.append(
            blueprint_component_detail(
                component
            )
        )

    meaningful = {

        key: value

        for key, value
        in component_counter.items()

        if key != "other"
    }

    return {

        "actor_path":
            object_path(actor),

        "actor_name":
            object_name(actor),

        "class":
            class_name(actor),

        "class_path":
            class_path(actor),

        "origin_type":
            actor_origin_type(actor),

        "source_level":
            object_path(source_level),

        "level_instance_chain":
            level_instance_chain or [],

        "transform":
            actor_transform(actor),

        "components":
            dict(component_counter),

        "meaningful_components":
            meaningful,

        "component_details":
            component_details,

        "conversion_risk":
            "medium"
            if meaningful
            else "high"
    }


# ============================================================
# ACTOR BASIC INFO
# ============================================================

def actor_basic_info(
    actor,
    source_level=None,
    level_instance_chain=None
):

    return {

        "path":
            object_path(actor),

        "name":
            object_name(actor),

        "class":
            class_name(actor),

        "class_path":
            class_path(actor),

        "category":
            actor_category(actor),

        "origin_type":
            actor_origin_type(actor),

        "source_level":
            object_path(source_level),

        "level_instance_chain":
            level_instance_chain or [],

        "transform":
            actor_transform(actor)
    }


# ============================================================
# LEVEL INSTANCE INFO
# ============================================================

def level_instance_info(
    actor,
    parent_chain=None,
    parent_actor_path=None,
    depth=0
):

    world_asset = safe_call(
        lambda:
            actor.get_world_asset(),
        None
    )

    loaded_level = safe_call(
        lambda:
            actor.get_loaded_level(),
        None
    )

    chain = list(
        parent_chain or []
    )

    actor_path = object_path(
        actor
    )

    return {

        "actor_path":
            actor_path,

        "actor_name":
            object_name(actor),

        "class":
            class_name(actor),

        "class_path":
            class_path(actor),

        "world_asset":
            asset_info(
                world_asset
            ),

        "loaded":
            bool(
                safe_call(
                    lambda:
                        actor.is_loaded(),
                    False
                )
            ),

        "loaded_level":
            object_path(
                loaded_level
            ),

        "transform":
            actor_transform(actor),

        "parent_level_instance":
            parent_actor_path,

        "parent_level_instance_chain":
            chain,

        "instance_chain":
            chain + [
                actor_path
            ],

        "depth":
            depth
    }


# ============================================================
# LEVEL ENUMERATION
# ============================================================
#
# IMPORTANT:
# THIS IS THE V4/V5/V6 PROVEN METHOD.
#
# DO NOT REPLACE THIS WITH:
#
#     world.get_current_level()
#
# ============================================================

def enumerate_level_actors(level):

    result = []

    if level is None:
        return result

    try:

        for actor in unreal.ObjectIterator(
            unreal.Actor
        ):

            try:

                if actor.get_level() == level:

                    result.append(
                        actor
                    )

            except Exception:
                continue

    except Exception:

        traceback.print_exc()

    return result


# ============================================================
# WORLD RETRIEVAL
# ============================================================

def get_editor_world():

    try:

        subsystem = unreal.get_editor_subsystem(
            unreal.UnrealEditorSubsystem
        )

        world = subsystem.get_editor_world()

        if world:

            return (
                world,
                "UnrealEditorSubsystem"
            )

    except Exception:
        pass

    try:

        world = (
            unreal.EditorLevelLibrary
            .get_editor_world()
        )

        if world:

            return (
                world,
                "EditorLevelLibrary_fallback"
            )

    except Exception:
        pass

    return (
        None,
        None
    )


# ============================================================
# DIRECT ACTORS
# ============================================================

def get_direct_actors():

    try:

        subsystem = unreal.get_editor_subsystem(
            unreal.EditorActorSubsystem
        )

        actors = (
            subsystem.get_all_level_actors()
        )

        if actors:

            return list(
                actors
            )

    except Exception:
        pass

    try:

        return list(
            unreal.EditorLevelLibrary
            .get_all_level_actors()
        )

    except Exception:

        return []


# ============================================================
# TEXTURE REGISTRY
# ============================================================

def register_texture(
    texture,
    material_path,
    texture_registry
):

    if texture is None:
        return

    path = object_path(
        texture
    )

    if not path:
        return

    if path not in texture_registry:

        texture_registry[
            path
        ] = {

            **texture_info(
                texture
            ),

            "material_references":
                [],

            "parameter_references":
                []
        }

    entry = texture_registry[
        path
    ]

    if (
        material_path
        and
        material_path not in
        entry[
            "material_references"
        ]
    ):

        entry[
            "material_references"
        ].append(
            material_path
        )


def register_material_textures(
    material,
    texture_registry
):

    if material is None:
        return

    material_path = object_path(
        material
    )

    parameter_data = (
        material_instance_parameters(
            material
        )
    )

    for parameter in parameter_data.get(
        "texture_parameters",
        []
    ):

        texture_record = parameter.get(
            "texture"
        )

        if not texture_record:
            continue

        texture_path = texture_record.get(
            "path"
        )

        if not texture_path:
            continue

        texture_obj = None

        # Best-effort resolve.
        try:

            texture_obj = (
                unreal.load_object(
                    None,
                    texture_path
                )
            )

        except Exception:
            texture_obj = None

        if texture_obj is not None:

            register_texture(
                texture_obj,
                material_path,
                texture_registry
            )

        else:

            if texture_path not in texture_registry:

                texture_registry[
                    texture_path
                ] = {

                    **texture_record,

                    "material_references":
                        [material_path]
                        if material_path
                        else [],

                    "parameter_references":
                        []
                }

        entry = texture_registry.get(
            texture_path
        )

        if entry is not None:

            parameter_name = parameter.get(
                "name"
            )

            if parameter_name:

                reference = {

                    "material":
                        material_path,

                    "parameter":
                        parameter_name
                }

                if reference not in entry[
                    "parameter_references"
                ]:

                    entry[
                        "parameter_references"
                    ].append(
                        reference
                    )


# ============================================================
# MATERIAL REGISTRY
# ============================================================

def register_material(
    material,
    material_registry,
    texture_registry
):

    if material is None:
        return

    path = object_path(
        material
    )

    if not path:
        return

    if path not in material_registry:

        material_registry[
            path
        ] = {

            **material_full_info(
                material
            ),

            "usage_count":
                0,

            "mesh_references":
                []
        }

    register_material_textures(
        material,
        texture_registry
    )



# ============================================================
# V10 FINAL WORLD-SPACE TRANSFORM COMPOSITION
# ============================================================
#
# Unreal's FTransform composition is used deliberately instead of
# manually multiplying Euler angles. Epic documents A * B as the
# canonical transform composition operation; order matters.
#
# Source transforms are NEVER discarded. V10 adds:
#   - placement_id
#   - final_world_transform
#   - reconstruction_transform
#   - transform_space
#
# For a nested LevelInstance chain:
#     Final = LI_root * LI_child * source_transform
#
# This is exactly the information the Godot reconstructer needs.
# ============================================================


def stable_id(prefix, path):

    if not path:
        return None

    digest = hashlib.sha1(
        str(path).encode("utf-8")
    ).hexdigest()[:16]

    return prefix + "_" + digest


def dict_to_unreal_transform(data):

    if not isinstance(data, dict):
        return None

    location = data.get("location")
    rotation = data.get("rotation")
    scale = data.get("scale")

    if not location or not rotation or not scale:
        return None

    try:
        return unreal.Transform(
            location=unreal.Vector(
                float(location[0]),
                float(location[1]),
                float(location[2])
            ),
            # V10.6 FIX: unreal.Rotator's PYTHON signature is
            # Rotator(roll, pitch, yaw) - NOT (pitch, yaw, roll) like the C++
            # FRotator constructor. Passing them positionally in C++ order
            # silently wrote pitch into roll, yaw into pitch and roll into
            # yaw, scrambling every rotation before composition. Keyword
            # arguments make the mapping explicit and immune to the ordering.
            rotation=unreal.Rotator(
                roll=float(rotation.get("roll", 0.0)),
                pitch=float(rotation.get("pitch", 0.0)),
                yaw=float(rotation.get("yaw", 0.0))
            ),
            scale=unreal.Vector(
                float(scale[0]),
                float(scale[1]),
                float(scale[2])
            )
        )
    except Exception:
        pass

    try:
        result = unreal.Transform()

        result.translation = unreal.Vector(
            float(location[0]),
            float(location[1]),
            float(location[2])
        )

        # V10.6 FIX: see above - Rotator(roll, pitch, yaw) in Python.
        result.rotation = unreal.Rotator(
            roll=float(rotation.get("roll", 0.0)),
            pitch=float(rotation.get("pitch", 0.0)),
            yaw=float(rotation.get("yaw", 0.0))
        ).quaternion()

        result.scale3d = unreal.Vector(
            float(scale[0]),
            float(scale[1]),
            float(scale[2])
        )

        return result

    except Exception:
        return None


def compose_unreal_transforms(a, b):

    if a is None:
        return b

    if b is None:
        return a

    try:
        return unreal.KismetMathLibrary.compose_transforms(
            a,
            b
        )
    except Exception:
        pass

    try:
        return a * b
    except Exception:
        return None


def compose_transform_dicts(a_dict, b_dict):

    a = dict_to_unreal_transform(a_dict)
    b = dict_to_unreal_transform(b_dict)

    result = compose_unreal_transforms(a, b)

    if result is None:
        return None

    return transform_to_dict(result)


def compose_chain_transform(
    chain,
    source_transform,
    li_registry,
    diagnostics=None,
    context=None
):

    current = None

    for li_path in chain or []:

        li_info = li_registry.get(
            li_path
        )

        if li_info is None:
            # V10.1: previously silently skipped, which meant the composed
            # transform below was missing this ancestor's contribution
            # with zero trace of it. Behavior is unchanged (still skipped
            # the same way, since we cannot compose a transform we don't
            # have), but it is now recorded instead of being invisible.
            if diagnostics is not None:
                diagnostics["broken_li_chain_links"] += 1
                diagnostics["broken_li_chain_link_details"].append({
                    "missing_li_actor_path": li_path,
                    "context": context
                })
            continue

        # IMPORTANT: use the LI's LOCAL/source transform here.
        # The registry's final_world_transform already contains its
        # parents and must never be multiplied again.
        li_transform = li_info.get(
            "source_transform",
            li_info.get("transform")
        )

        if li_transform is None:
            continue

        if current is None:
            current = li_transform
        else:
            # FIX (transform-order bug): Unreal's FTransform composition
            # is CHILD * PARENT (see USceneComponent: NewTransform =
            # RelativeTransform * ParentToWorld). `li_transform` here is
            # the NEXT (more nested) LevelInstance's LOCAL transform - i.e.
            # the "child" relative to everything accumulated so far in
            # `current` (the "parent"). The previous code called
            # compose_transform_dicts(current, li_transform), which is
            # parent * child - backwards. This only "worked" when every
            # ancestor LI had an identity rotation (pure translation is
            # order-independent); as soon as any LI in the chain is
            # rotated, translations got rotated by the wrong basis and
            # objects ended up scattered essentially at random across the
            # level. Swapping the arguments fixes this.
            current = compose_transform_dicts(
                li_transform,
                current
            )

    if current is None:
        return source_transform

    # FIX (same bug, final step): `source_transform` (the mesh/actor's own
    # local transform) is the "child" here, and `current` (the fully
    # composed LevelInstance chain) is the "parent". Must be
    # child * parent, i.e. source_transform * current - not the reverse.
    return compose_transform_dicts(
        source_transform,
        current
    )


def assign_final_transform(
    record,
    li_registry,
    source_key="transform"
):

    if not isinstance(record, dict):
        return False

    source_transform = record.get(source_key)

    if source_transform is None:
        return False

    chain = record.get(
        "level_instance_chain",
        []
    )

    final_transform = compose_chain_transform(
        chain,
        source_transform,
        li_registry
    )

    if final_transform is None:
        return False

    record["source_transform"] = source_transform

    record["final_world_transform"] = (
        final_transform
    )

    record["reconstruction_transform"] = (
        final_transform
    )

    record["transform_space"] = (
        "world"
        if not chain
        else "composed_world"
    )

    return True


def recursively_update_hierarchy_node(
    node,
    final_li_transforms
):

    if not isinstance(node, dict):
        return

    actor_path = node.get("actor_path")

    if actor_path in final_li_transforms:

        final_transform = final_li_transforms[
            actor_path
        ]

        node["final_world_transform"] = final_transform
        node["reconstruction_transform"] = final_transform
        node["transform_space"] = "composed_world"

    for child in node.get("children", []):
        recursively_update_hierarchy_node(
            child,
            final_li_transforms
        )


def finalize_v10_transforms(manifest):

    diagnostics = {
        "transform_composition": "FTransform_A_B",
        "li_final_transform_count": 0,
        "li_transform_failures": 0,
        "geometry_final_transform_count": 0,
        "geometry_transform_failures": 0,
        "actor_final_transform_count": 0,
        "actor_transform_failures": 0,
        "secondary_final_transform_count": 0,
        "secondary_transform_failures": 0,
        "duplicate_placement_ids": 0,
        "missing_mesh_references": 0,
        "missing_material_references": 0,
        "missing_li_parent_references": 0,
        "invalid_world_chains": 0,

        # --- V10.1 additive diagnostics (do not change any existing count) ---
        # A component whose mesh slot was never assigned (legitimate, e.g.
        # an anchor/socket StaticMeshComponent). Previously miscounted as
        # a "missing_mesh_reference" false positive.
        "empty_mesh_slot_count": 0,
        # Full detail for every REAL missing mesh reference (mesh was
        # assigned but its path is absent from geometry.unique_meshes).
        "missing_mesh_reference_details": [],
        # Full detail for every geometry transform failure, so failures
        # are traceable to a specific actor/component instead of just a count.
        "geometry_transform_failure_details": [],
        # A level_instance_chain pointed to an LI actor path that is not in
        # li_registry. Previously silently skipped inside
        # compose_chain_transform (the transform was composed missing that
        # link, with no diagnostic at all). Now counted AND still resolved
        # the same way as before (no behavior change to the actual math).
        "broken_li_chain_links": 0,
        "broken_li_chain_link_details": [],

        # V10.4: instance-level (ISM/HISM) transform composition tracking.
        "instance_transform_total": 0,
        "instance_transform_failures": 0,
        "instance_transform_failure_details": []
    }

    li_registry = manifest.get(
        "level_instances",
        {}
    ).get(
        "registry",
        {}
    )

    # --------------------------------------------------------
    # 1. Stable LI placement IDs + final transforms
    # --------------------------------------------------------

    final_li_transforms = {}
    placement_ids = set()

    for actor_path, info in li_registry.items():

        placement_id = stable_id(
            "LI",
            actor_path
        )

        info["placement_id"] = placement_id
        info["parent_placement_id"] = (
            stable_id(
                "LI",
                info.get("parent_level_instance")
            )
            if info.get("parent_level_instance")
            else None
        )

        if placement_id in placement_ids:
            diagnostics["duplicate_placement_ids"] += 1
        else:
            placement_ids.add(placement_id)

        parent_path = info.get(
            "parent_level_instance"
        )

        local_transform = info.get(
            "transform"
        )

        if not local_transform:
            diagnostics["li_transform_failures"] += 1
            continue

        if parent_path:

            parent_final = final_li_transforms.get(
                parent_path
            )

            if parent_final is None:
                diagnostics["invalid_world_chains"] += 1
                diagnostics["li_transform_failures"] += 1
                continue

            # FIX (same transform-order bug as compose_chain_transform):
            # this LI's own local_transform is the "child", parent_final is
            # the "parent" it sits inside. Unreal composes child * parent,
            # so local_transform must come first.
            final_transform = compose_transform_dicts(
                local_transform,
                parent_final
            )

        else:
            final_transform = local_transform

        if final_transform is None:
            diagnostics["li_transform_failures"] += 1
            continue

        info["source_transform"] = local_transform
        info["final_world_transform"] = final_transform
        info["reconstruction_transform"] = final_transform
        info["transform_space"] = (
            "world"
            if not parent_path
            else "composed_world"
        )

        final_li_transforms[
            actor_path
        ] = final_transform

        diagnostics["li_final_transform_count"] += 1

    # --------------------------------------------------------
    # 2. LI placements mirror the registry exactly
    # --------------------------------------------------------

    for placement in manifest.get(
        "level_instances",
        {}
    ).get("placements", []):

        actor_path = placement.get(
            "actor_path"
        )

        info = li_registry.get(
            actor_path
        )

        if info is None:
            continue

        for key in (
            "placement_id",
            "parent_placement_id",
            "source_transform",
            "final_world_transform",
            "reconstruction_transform",
            "transform_space"
        ):
            if key in info:
                placement[key] = info[key]

    # --------------------------------------------------------
    # 3. Hierarchy gets final world transforms too
    # --------------------------------------------------------

    for root in manifest.get(
        "level_instances",
        {}
    ).get("hierarchy", []):

        recursively_update_hierarchy_node(
            root,
            final_li_transforms
        )

    # --------------------------------------------------------
    # 4. Every actor gets a reconstruction transform
    # --------------------------------------------------------

    for actor in manifest.get(
        "actors",
        {}
    ).get("records", []):

        actor_path = actor.get("path")
        actor_chain = actor.get(
            "level_instance_chain",
            []
        )

        source_transform = actor.get(
            "transform"
        )

        if source_transform is None:
            continue

        final_transform = compose_chain_transform(
            actor_chain,
            source_transform,
            li_registry
        )

        if final_transform is None:
            diagnostics["actor_transform_failures"] += 1
            continue

        actor["source_transform"] = source_transform
        actor["final_world_transform"] = final_transform
        actor["reconstruction_transform"] = final_transform
        actor["transform_space"] = (
            "world"
            if not actor_chain
            else "composed_world"
        )

        diagnostics["actor_final_transform_count"] += 1

    # --------------------------------------------------------
    # 5. Geometry: the most important importer payload
    # --------------------------------------------------------

    geometry = manifest.get(
        "geometry",
        {}
    )

    for index, placement in enumerate(
        geometry.get("placements", [])
    ):

        component_path = (
            placement.get("component", {})
            .get("path")
        )

        actor_path = (
            placement.get("actor", {})
            .get("path")
        )

        stable_source = (
            component_path
            or actor_path
            or "geometry_" + str(index)
        )

        placement["placement_id"] = stable_id(
            "MESH",
            stable_source
        )

        placement["source_transform"] = placement.get(
            "transform"
        )

        final_transform = compose_chain_transform(
            placement.get("level_instance_chain", []),
            placement.get("transform"),
            li_registry,
            diagnostics=diagnostics,
            context={"placement_id": placement.get("placement_id"), "kind": "component_transform"}
        )

        actor_final = compose_chain_transform(
            placement.get("level_instance_chain", []),
            placement.get("actor_transform"),
            li_registry,
            diagnostics=diagnostics,
            context={"placement_id": placement.get("placement_id"), "kind": "actor_transform"}
        )

        if final_transform is None:
            diagnostics["geometry_transform_failures"] += 1
            diagnostics["geometry_transform_failure_details"].append({
                "placement_id": placement.get("placement_id"),
                "actor_path": actor_path,
                "component_path": component_path,
                "reason": (
                    "component_transform_unavailable"
                    if not placement.get("level_instance_chain")
                    else "component_transform_unavailable_or_broken_li_chain"
                ),
                # V10.1: the real root cause captured live in UE, at the
                # moment component.get_component_transform() was called -
                # not a guess made after the fact from the JSON.
                "transform_extraction_error": placement.get("transform_extraction_error")
            })
        else:
            placement["final_world_transform"] = final_transform
            placement["reconstruction_transform"] = final_transform
            placement["transform_space"] = (
                "world"
                if not placement.get("level_instance_chain")
                else "composed_world"
            )
            diagnostics["geometry_final_transform_count"] += 1

        if actor_final is not None:
            placement[
                "final_actor_world_transform"
            ] = actor_final

        # V10.4: compose every REAL instance transform (not just the
        # component's own single transform) through the same LI chain.
        # This is what actually lets Godot spawn all N instances of an
        # ISM/HISM component instead of just one.
        raw_instance_transforms = placement.get("instance_transforms")

        if raw_instance_transforms:

            instance_final_world_transforms = []
            instance_failures_here = 0

            for _inst_idx, _inst_transform in enumerate(raw_instance_transforms):

                if _inst_transform is None:
                    instance_final_world_transforms.append(None)
                    instance_failures_here += 1
                    continue

                _composed = compose_chain_transform(
                    placement.get("level_instance_chain", []),
                    _inst_transform,
                    li_registry,
                    diagnostics=diagnostics,
                    context={
                        "placement_id": placement.get("placement_id"),
                        "kind": "instance_transform",
                        "instance_index": _inst_idx
                    }
                )

                instance_final_world_transforms.append(_composed)

                if _composed is None:
                    instance_failures_here += 1

            placement["instance_final_world_transforms"] = instance_final_world_transforms

            diagnostics["instance_transform_failures"] += instance_failures_here
            diagnostics["instance_transform_total"] += len(raw_instance_transforms)

            if instance_failures_here > 0:
                diagnostics["instance_transform_failure_details"].append({
                    "placement_id": placement.get("placement_id"),
                    "actor_path": actor_path,
                    "component_path": component_path,
                    "instance_count": len(raw_instance_transforms),
                    "failed_instance_count": instance_failures_here
                })

        mesh_field = placement.get("mesh")

        if mesh_field is None:
            # No StaticMesh was ever assigned to this component (anchor,
            # socket, empty ISM slot, etc.). This is NOT a broken reference,
            # so it must not be counted as one.
            diagnostics["empty_mesh_slot_count"] += 1
        else:
            mesh_path = mesh_field.get("path")

            if not mesh_path or mesh_path not in geometry.get(
                "unique_meshes", {}
            ):
                diagnostics["missing_mesh_references"] += 1
                diagnostics["missing_mesh_reference_details"].append({
                    "placement_id": placement.get("placement_id"),
                    "actor_path": actor_path,
                    "component_path": component_path,
                    "mesh_path": mesh_path,
                    "reason": (
                        "mesh_path_unresolved"
                        if not mesh_path
                        else "mesh_path_not_in_unique_meshes_registry"
                    )
                })

        for material in placement.get(
            "materials", []
        ):
            material_path = material.get(
                "path"
            )
            if material_path and material_path not in manifest.get(
                "materials", {}
            ).get("unique_materials", {}):
                diagnostics["missing_material_references"] += 1

    # --------------------------------------------------------
    # 6. Secondary spatial systems
    # --------------------------------------------------------

    spatial_lists = (
        ("lights", manifest.get("world_features", {}).get("lights", [])),
        ("audio", manifest.get("world_features", {}).get("audio", [])),
        ("niagara", manifest.get("effects", {}).get("niagara", [])),
        ("decals", manifest.get("effects", {}).get("decals", [])),
        ("particles", manifest.get("effects", {}).get("particles", []))
    )

    for _, records in spatial_lists:

        for record in records:

            chain = record.get(
                "level_instance_chain",
                []
            )

            source_transform = record.get(
                "transform"
            )

            if source_transform is None:
                continue

            final_transform = compose_chain_transform(
                chain,
                source_transform,
                li_registry
            )

            if final_transform is None:
                diagnostics["secondary_transform_failures"] += 1
            else:
                record["source_transform"] = source_transform
                record["final_world_transform"] = final_transform
                record["reconstruction_transform"] = final_transform
                record["transform_space"] = (
                    "world"
                    if not chain
                    else "composed_world"
                )
                diagnostics["secondary_final_transform_count"] += 1

    # --------------------------------------------------------
    # 7. Blueprint component transforms
    # --------------------------------------------------------

    for blueprint in manifest.get(
        "blueprints", {}
    ).get("actors", []):

        chain = blueprint.get(
            "level_instance_chain",
            []
        )

        for component in blueprint.get(
            "component_details", []
        ):

            source_transform = component.get(
                "transform"
            )

            if source_transform is None:
                continue

            final_transform = compose_chain_transform(
                chain,
                source_transform,
                li_registry
            )

            if final_transform is not None:
                component["source_transform"] = source_transform
                component["final_world_transform"] = final_transform
                component["reconstruction_transform"] = final_transform
                component["transform_space"] = (
                    "world"
                    if not chain
                    else "composed_world"
                )

    # --------------------------------------------------------
    # 8. Parent references
    # --------------------------------------------------------

    for actor_path, info in li_registry.items():

        parent = info.get(
            "parent_level_instance"
        )

        if parent and parent not in li_registry:
            diagnostics["missing_li_parent_references"] += 1

    # --------------------------------------------------------
    # 8b. FX transform diagnostics (V10.2)
    # --------------------------------------------------------
    # ready_for_godot_geometry above is scoped to StaticMesh/LevelInstance
    # geometry only, by design - it says nothing about decals, Niagara,
    # lights or audio. Those categories can silently sit at a 100% transform
    # failure rate (the same stale-reference issue geometry has, now fixed
    # for these four too - see component_transform_diagnostic) while the
    # top-level summary still read "ready". This block makes that failure
    # visible at the same top level instead of only inside each item's own
    # transform_extraction_error field.

    def _fx_category_diagnostic(items):
        total = len(items)
        failed = [item for item in items if item.get("transform") is None]
        return {
            "total": total,
            "with_transform": total - len(failed),
            "failed": len(failed),
            "failure_examples": [
                {
                    "component_path": (item.get("component") or {}).get("path"),
                    "error": item.get("transform_extraction_error")
                }
                for item in failed[:5]
            ]
        }

    fx_transform_diagnostics = {
        "decals": _fx_category_diagnostic(
            manifest.get("effects", {}).get("decals", [])
        ),
        "niagara": _fx_category_diagnostic(
            manifest.get("effects", {}).get("niagara", [])
        ),
        "lights": _fx_category_diagnostic(
            manifest.get("world_features", {}).get("lights", [])
        ),
        "audio": _fx_category_diagnostic(
            manifest.get("world_features", {}).get("audio", [])
        )
    }

    ready_for_godot_fx = all(
        category["failed"] == 0
        for category in fx_transform_diagnostics.values()
    )

    # --------------------------------------------------------
    # 9. Top-level reconstruction contract
    # --------------------------------------------------------

    manifest["reconstruction"] = {
        "ready_for_godot_geometry": (
            diagnostics["geometry_transform_failures"] == 0
            and diagnostics["missing_mesh_references"] == 0
            and diagnostics["li_transform_failures"] == 0
            # V10.4: an ISM/HISM whose per-instance transforms failed to
            # compose is just as much a geometry gap as a missing mesh -
            # previously this could sit at 0/458 instances placed while
            # still reporting "ready".
            and diagnostics["instance_transform_failures"] == 0
        ),
        "ready_for_godot_geometry_note": (
            "Scoped to StaticMesh/LevelInstance geometry only. See "
            "ready_for_godot_fx for decals/Niagara/lights/audio. Also "
            "requires every ISM/HISM instance_transform to have composed "
            "successfully (see instance_transform_failures)."
        ),
        "ready_for_godot_fx": ready_for_godot_fx,
        "fx_transform_diagnostics": fx_transform_diagnostics,
        "transform_space": "Unreal_world_space",
        "source_transform_preserved": True,
        "final_world_transform_present": True,
        "level_instance_transform_composition": True,
        "composition_order": "parent * child * source",
        "asset_payload_included": False,
        "asset_payload_note": (
            "The manifest is a specification. Actual mesh/texture files "
            "still require export/conversion into Godot-readable assets."
        ),
        "blueprint_logic_included": False,
        "world_partition_complete": not manifest.get(
            "world_partition", {}
        ).get("detected", False)
    }

    manifest["conversion_diagnostic"][
        "v10_transform_diagnostic"
    ] = diagnostics

    manifest["conversion_diagnostic"][
        "v10_scope"
    ] = {
        "nested_transform_composition": True,
        "stable_placement_ids": True,
        "final_world_transforms": True,
        "asset_payload_export": False,
        "blueprint_logic_extraction": False
    }

    return diagnostics


# ============================================================
# MAIN
# ============================================================

def main():

    total_start = time.time()

    print("")
    print("============================================================")
    print(" UE5 -> GODOT CONVERSION MANIFEST - V10")
    print("============================================================")

    # ========================================================
    # WORLD
    # ========================================================

    t0 = time.time()

    world, world_resolution = (
        get_editor_world()
    )

    TIMINGS[
        "world_resolution"
    ] = time.time() - t0

    if world is None:

        print(
            "ERROR: impossible de récupérer le Editor World."
        )

        return

    print(
        "World                         :",
        object_path(world)
    )

    print(
        "World resolution              :",
        world_resolution
    )

    # ========================================================
    # DIRECT ACTORS
    # ========================================================

    t0 = time.time()

    direct_actors = get_direct_actors()

    direct_actors = [

        actor

        for actor in direct_actors

        if actor is not None
    ]

    TIMINGS[
        "direct_actor_collection"
    ] = time.time() - t0

    print(
        "Direct actors                 :",
        len(direct_actors)
    )

    # ========================================================
    # MANIFEST ROOT
    # ========================================================

    manifest = {

        "manifest_version":
            "10.0",

        "previous_versions_preserved": [

            "V4",
            "V5",
            "V6",
            "V7",
            "V8",
            "V9"
        ],

        "exporter": {

            "name":
                "UE5 -> Godot Conversion Manifest",

            "version":
                "10.0",

            "engine":
                "Unreal Engine 5.5",

            "world_resolution":
                world_resolution
        },

        "world": {

            "path":
                object_path(world),

            "name":
                object_name(world),

            "class":
                class_name(world)
        },

        "actors": {

            "direct_count":
                len(direct_actors),

            "accessible_unique_count":
                0,

            "by_category":
                {},

            "by_origin_type":
                {},

            "records":
                []
        },

        "level_instances": {

            "placements":
                [],

            "hierarchy":
                [],

            "unique_world_assets":
                {},

            "registry":
                {},

            "total_placements":
                0,

            "unique_world_count":
                0,

            "diagnostic":
                {}
        },

        "geometry": {

            "unique_meshes":
                {},

            "placements":
                [],

            "component_counts":
                {},

            # V10.1: skeletal meshes now actually captured (see below),
            # instead of only being counted at the actor level with no
            # asset path ever recorded.
            "unique_skeletal_meshes":
                {},

            "skeletal_mesh_placements":
                []
        },

        "materials": {

            "unique_materials":
                {}
        },

        "textures": {

            "unique_textures":
                {}
        },

        "blueprints": {

            "actors":
                [],

            "class_counts":
                {},

            "native_actor_count":
                0,

            "blueprint_actor_count":
                0,

            "unknown_actor_count":
                0
        },

        "effects": {

            "niagara":
                [],

            "decals":
                [],

            "particles":
                []
        },

        "world_features": {

            "landscape":
                [],

            "lights":
                [],

            "foliage":
                [],

            "audio":
                [],

            "environment":
                []
        },

        "world_partition": {

            "detected":
                False,

            "system_actor_count":
                0,

            "counted_as_content":
                False
        },

        "component_inventory": {

            "counts":
                {}
        },

        "asset_inventory": {

            "static_mesh_assets":
                0,

            "material_assets":
                0,

            "texture_assets":
                0,

            "skeletal_mesh_assets":
                0,

            "level_instance_world_assets":
                0
        },

        "timings": {},

        "conversion_diagnostic":
            {},

        "conversion_risks":
            [],

        "warnings":
            []
    }

    # ========================================================
    # GLOBAL REGISTRIES
    # ========================================================

    all_actors = []

    seen_actor_paths = set()

    mesh_registry = {}

    material_registry = {}

    texture_registry = {}

    component_counter = Counter()

    actor_category_counter = Counter()

    actor_origin_counter = Counter()

    blueprint_class_counter = Counter()

    level_instance_registry = {}

    li_occurrence_count = 0

    li_duplicate_encounters = 0

    # ========================================================
    # ACTOR ADDER
    # ========================================================

    def add_actor(actor):

        if actor is None:
            return

        path = object_path(
            actor
        )

        if not path:
            return

        if path in seen_actor_paths:
            return

        seen_actor_paths.add(
            path
        )

        all_actors.append(
            actor
        )

    # ========================================================
    # CANONICAL LEVEL INSTANCE REGISTRATION
    # ========================================================

    def register_level_instance(
        actor,
        parent_chain=None,
        parent_actor_path=None,
        depth=0,
        source="unknown"
    ):

        nonlocal li_occurrence_count
        nonlocal li_duplicate_encounters

        if actor is None:
            return None

        actor_path = object_path(
            actor
        )

        if not actor_path:
            return None

        li_occurrence_count += 1

        # ----------------------------------------------------
        # Existing canonical actor path
        # ----------------------------------------------------

        if actor_path in level_instance_registry:

            existing = (
                level_instance_registry[
                    actor_path
                ]
            )

            existing[
                "encounter_count"
            ] += 1

            existing[
                "encounter_sources"
            ].append(
                source
            )

            li_duplicate_encounters += 1

            return existing

        # ----------------------------------------------------
        # First encounter
        # ----------------------------------------------------

        info = level_instance_info(
            actor,
            parent_chain=parent_chain,
            parent_actor_path=parent_actor_path,
            depth=depth
        )

        info[
            "encounter_count"
        ] = 1

        info[
            "encounter_sources"
        ] = [
            source
        ]

        info[
            "children_actor_paths"
        ] = []

        info[
            "status"
        ] = (

            "LOADED"

            if info.get(
                "loaded"
            )

            else "NOT_LOADED"
        )

        level_instance_registry[
            actor_path
        ] = info

        return info

    # ========================================================
    # REGISTER DIRECT ACTORS
    # ========================================================

    t0 = time.time()

    for actor in direct_actors:

        add_actor(
            actor
        )

    TIMINGS[
        "direct_actor_registration"
    ] = time.time() - t0

    # ========================================================
    # ROOT LEVEL INSTANCES
    # ========================================================

    root_level_instances = [

        actor

        for actor in direct_actors

        if actor_category(actor)
        ==
        "level_instance"
    ]

    print(
        "Direct LevelInstances          :",
        len(root_level_instances)
    )

    # --------------------------------------------------------
    # Register direct roots
    # --------------------------------------------------------

    for root_li in root_level_instances:

        register_level_instance(

            root_li,

            parent_chain=[],

            parent_actor_path=None,

            depth=0,

            source="direct_root"
        )

    # ========================================================
    # RECURSIVE ACCESSIBLE CONTENT COLLECTION
    # ========================================================

    visited_levels = set()

    def collect_level_contents(
        level,
        parent_chain=None,
        parent_actor_path=None,
        depth=0
    ):

        if level is None:
            return

        if depth > MAX_LEVEL_INSTANCE_DEPTH:
            return

        level_path = object_path(
            level
        )

        if level_path in visited_levels:
            return

        visited_levels.add(
            level_path
        )

        actors = enumerate_level_actors(
            level
        )

        for actor in actors:

            add_actor(
                actor
            )

            category = actor_category(
                actor
            )

            if category != "level_instance":
                continue

            actor_path = object_path(
                actor
            )

            # ------------------------------------------------
            # Register canonical LI
            # ------------------------------------------------

            li_info = register_level_instance(

                actor,

                parent_chain=parent_chain,

                parent_actor_path=parent_actor_path,

                depth=depth,

                source="recursive_level_scan"
            )

            if li_info is None:
                continue

            # ------------------------------------------------
            # Link parent -> child
            # ------------------------------------------------

            if parent_actor_path:

                parent_info = (
                    level_instance_registry.get(
                        parent_actor_path
                    )
                )

                if parent_info is not None:

                    children = (
                        parent_info[
                            "children_actor_paths"
                        ]
                    )

                    if actor_path not in children:

                        children.append(
                            actor_path
                        )

            # ------------------------------------------------
            # Recurse into child level
            # ------------------------------------------------

            child_level = safe_call(

                lambda:
                    actor.get_loaded_level(),

                None
            )

            if child_level:

                child_chain = (
                    parent_chain or []
                ) + [
                    actor_path
                ]

                collect_level_contents(

                    child_level,

                    parent_chain=child_chain,

                    parent_actor_path=actor_path,

                    depth=depth + 1
                )

    # ========================================================
    # START FROM DIRECT LEVEL INSTANCES
    # ========================================================

    t0 = time.time()

    for root_li in root_level_instances:

        loaded_level = safe_call(

            lambda li=root_li:
                li.get_loaded_level(),

            None
        )

        if loaded_level:

            collect_level_contents(

                loaded_level,

                parent_chain=[
                    object_path(
                        root_li
                    )
                ],

                parent_actor_path=
                    object_path(
                        root_li
                    ),

                depth=1
            )

    TIMINGS[
        "level_instance_collection"
    ] = time.time() - t0

    print(
        "Accessible unique actors        :",
        len(all_actors)
    )

    # ========================================================
    # BUILD ACTOR -> LI CHAIN MAP
    # ========================================================

    actor_li_chain_map = {}

    for info in level_instance_registry.values():

        actor_path = info.get(
            "actor_path"
        )

        if not actor_path:
            continue

        actor_li_chain_map[
            actor_path
        ] = info.get(
            "instance_chain",
            []
        )

    # --------------------------------------------------------
    # Helper for all accessible actors
    # --------------------------------------------------------

    def get_actor_li_chain(actor):

        actor_path = object_path(
            actor
        )

        if not actor_path:
            return []

        actor_level = safe_call(
            lambda:
                actor.get_level(),
            None
        )

        actor_level_path = object_path(
            actor_level
        )

        # Find the LI whose loaded level contains this actor.
        # This is deliberately conservative.
        #
        # We do NOT compose transforms here.

        best_chain = []

        best_depth = -1

        for info in level_instance_registry.values():

            loaded_level_path = info.get(
                "loaded_level"
            )

            if (
                loaded_level_path
                and
                loaded_level_path
                ==
                actor_level_path
            ):

                depth = safe_int(
                    info.get(
                        "depth",
                        0
                    ),
                    0
                )

                if depth > best_depth:

                    best_depth = depth

                    best_chain = list(
                        info.get(
                            "instance_chain",
                            []
                        )
                    )

        return best_chain

    # ========================================================
    # CANONICAL LI PLACEMENTS
    # ========================================================

    t0 = time.time()

    canonical_li_records = list(
        level_instance_registry.values()
    )

    for info in canonical_li_records:

        placement = {

            "actor_path":
                info.get(
                    "actor_path"
                ),

            "actor_name":
                info.get(
                    "actor_name"
                ),

            "class":
                info.get(
                    "class"
                ),

            "class_path":
                info.get(
                    "class_path"
                ),

            "world_asset":
                info.get(
                    "world_asset"
                ),

            "transform":
                info.get(
                    "transform"
                ),

            "loaded":
                info.get(
                    "loaded"
                ),

            "loaded_level":
                info.get(
                    "loaded_level"
                ),

            "parent_level_instance":
                info.get(
                    "parent_level_instance"
                ),

            "parent_level_instance_chain":
                info.get(
                    "parent_level_instance_chain",
                    []
                ),

            "instance_chain":
                info.get(
                    "instance_chain",
                    []
                ),

            "depth":
                info.get(
                    "depth",
                    0
                ),

            "accessible_actor_count":
                0,

            "status":
                info.get(
                    "status"
                ),

            "encounter_count":
                info.get(
                    "encounter_count",
                    1
                )
        }

        loaded_level = safe_call(

            lambda:
                next(

                    (
                        actor.get_loaded_level()

                        for actor in all_actors

                        if object_path(actor)
                        ==
                        info.get(
                            "actor_path"
                        )
                    ),

                    None
                ),

            None
        )

        if loaded_level is not None:

            placement[
                "accessible_actor_count"
            ] = len(
                enumerate_level_actors(
                    loaded_level
                )
            )

        manifest[
            "level_instances"
        ][
            "placements"
        ].append(
            placement
        )

    TIMINGS[
        "li_placement_build"
    ] = time.time() - t0

    # ========================================================
    # BUILD CANONICAL HIERARCHY
    # ========================================================

    t0 = time.time()

    hierarchy_by_root = {}

    for info in canonical_li_records:

        actor_path = info.get(
            "actor_path"
        )

        if not actor_path:
            continue

        parent_path = info.get(
            "parent_level_instance"
        )

        if parent_path:

            parent_info = (
                level_instance_registry.get(
                    parent_path
                )
            )

            if parent_info:

                children = (
                    parent_info[
                        "children_actor_paths"
                    ]
                )

                if actor_path not in children:

                    children.append(
                        actor_path
                    )

        else:

            hierarchy_by_root[
                actor_path
            ] = True

    def build_hierarchy_node(
        actor_path,
        visited=None
    ):

        if visited is None:
            visited = set()

        if actor_path in visited:

            return {

                "status":
                    "ALREADY_VISITED",

                "actor_path":
                    actor_path,

                "children":
                    []
            }

        visited.add(
            actor_path
        )

        info = level_instance_registry.get(
            actor_path
        )

        if info is None:
            return None

        node = dict(
            info
        )

        node[
            "children"
        ] = []

        for child_path in info.get(
            "children_actor_paths",
            []
        ):

            child_node = (
                build_hierarchy_node(
                    child_path,
                    visited
                )
            )

            if child_node:

                node[
                    "children"
                ].append(
                    child_node
                )

        return node

    for root_path in hierarchy_by_root:

        node = build_hierarchy_node(
            root_path
        )

        if node:

            manifest[
                "level_instances"
            ][
                "hierarchy"
            ].append(
                node
            )

    TIMINGS[
        "li_hierarchy"
    ] = time.time() - t0

    # ========================================================
    # SOURCE WORLD REGISTRY
    # ========================================================

    t0 = time.time()

    level_instance_world_assets = {}

    for info in canonical_li_records:

        world_asset = info.get(
            "world_asset"
        )

        if not world_asset:
            continue

        world_path = world_asset.get(
            "path"
        )

        if not world_path:
            continue

        if (
            world_path
            not in
            level_instance_world_assets
        ):

            level_instance_world_assets[
                world_path
            ] = {

                "path":
                    world_path,

                "name":
                    world_asset.get(
                        "name"
                    ),

                "class":
                    world_asset.get(
                        "class"
                    ),

                "placement_count":
                    0,

                "placements":
                    []
            }

        entry = (
            level_instance_world_assets[
                world_path
            ]
        )

        entry[
            "placement_count"
        ] += 1

        entry[
            "placements"
        ].append(
            info.get(
                "actor_path"
            )
        )

    manifest[
        "level_instances"
    ][
        "unique_world_assets"
    ] = level_instance_world_assets

    manifest[
        "level_instances"
    ][
        "registry"
    ] = level_instance_registry

    manifest[
        "level_instances"
    ][
        "total_placements"
    ] = len(
        canonical_li_records
    )

    manifest[
        "level_instances"
    ][
        "unique_world_count"
    ] = len(
        level_instance_world_assets
    )

    TIMINGS[
        "li_world_registry"
    ] = time.time() - t0

    # ========================================================
    # LI DIAGNOSTICS
    # ========================================================

    hierarchy_node_count = 0

    def count_hierarchy_nodes(node):

        if not node:
            return 0

        count = 1

        for child in node.get(
            "children",
            []
        ):

            count += (
                count_hierarchy_nodes(
                    child
                )
            )

        return count

    for root in manifest[
        "level_instances"
    ][
        "hierarchy"
    ]:

        hierarchy_node_count += (
            count_hierarchy_nodes(
                root
            )
        )

    manifest[
        "level_instances"
    ][
        "diagnostic"
    ] = {

        "occurrences_found":
            li_occurrence_count,

        "unique_actor_paths":
            len(
                level_instance_registry
            ),

        "duplicate_encounters_ignored":
            li_duplicate_encounters,

        "unique_source_worlds":
            len(
                level_instance_world_assets
            ),

        "hierarchy_nodes":
            hierarchy_node_count,

        "placements_equal_unique_actor_paths":
            (
                len(canonical_li_records)
                ==
                len(level_instance_registry)
            ),

        "hierarchy_equal_unique_actor_paths":
            (
                hierarchy_node_count
                ==
                len(level_instance_registry)
            )
    }

    # ========================================================
    # ANALYZE ALL ACCESSIBLE ACTORS
    # ========================================================

    t0 = time.time()

    for actor in all_actors:

        category = actor_category(
            actor
        )

        origin_type = actor_origin_type(
            actor
        )

        # ----------------------------------------------------
        # WORLD PARTITION
        # ----------------------------------------------------

        if category == "world_partition_system":

            manifest[
                "world_partition"
            ][
                "detected"
            ] = True

            manifest[
                "world_partition"
            ][
                "system_actor_count"
            ] += 1

            continue

        actor_category_counter[
            category
        ] += 1

        actor_origin_counter[
            origin_type
        ] += 1

        # ----------------------------------------------------
        # ACTOR LI CHAIN
        # ----------------------------------------------------

        actor_chain = get_actor_li_chain(
            actor
        )

        source_level = safe_call(
            lambda:
                actor.get_level(),
            None
        )

        # ----------------------------------------------------
        # COMPONENTS
        # ----------------------------------------------------

        components = get_actor_components(
            actor
        )

        component_types = (
            component_statistics(
                components
            )
        )

        for key, value in (
            component_types.items()
        ):

            component_counter[
                key
            ] += value

        # ----------------------------------------------------
        # ACTOR RECORD
        # ----------------------------------------------------

        manifest[
            "actors"
        ][
            "records"
        ].append(

            actor_basic_info(

                actor,

                source_level=
                    source_level,

                level_instance_chain=
                    actor_chain
            )
        )

        # ----------------------------------------------------
        # LANDSCAPE
        # ----------------------------------------------------

        if category == "landscape":

            manifest[
                "world_features"
            ][
                "landscape"
            ].append(

                landscape_info(
                    actor
                )
            )

        # ----------------------------------------------------
        # FOLIAGE
        # ----------------------------------------------------

        elif category == "foliage":

            manifest[
                "world_features"
            ][
                "foliage"
            ].append(

                actor_basic_info(

                    actor,

                    source_level=
                        source_level,

                    level_instance_chain=
                        actor_chain
                )
            )

        # ----------------------------------------------------
        # ENVIRONMENT
        # ----------------------------------------------------

        elif category == "environment":

            manifest[
                "world_features"
            ][
                "environment"
            ].append(

                actor_basic_info(

                    actor,

                    source_level=
                        source_level,

                    level_instance_chain=
                        actor_chain
                )
            )

        # ----------------------------------------------------
        # AUDIO ACTOR
        # ----------------------------------------------------

        elif category == "audio":

            manifest[
                "world_features"
            ][
                "audio"
            ].append(

                actor_basic_info(

                    actor,

                    source_level=
                        source_level,

                    level_instance_chain=
                        actor_chain
                )
            )

        # ----------------------------------------------------
        # BLUEPRINT
        # ----------------------------------------------------

        if origin_type == "blueprint":

            blueprint_info = (
                blueprint_actor_info(

                    actor,

                    components,

                    source_level=
                        source_level,

                    level_instance_chain=
                        actor_chain
                )
            )

            manifest[
                "blueprints"
            ][
                "actors"
            ].append(
                blueprint_info
            )

            blueprint_class_counter[
                class_name(actor)
            ] += 1

        # ====================================================
        # COMPONENT PROCESSING
        # ====================================================

        for component in components:

            kind = component_kind(
                component
            )

            # ------------------------------------------------
            # STATIC MESH
            # ------------------------------------------------

            if kind in (

                "static_mesh",

                "instanced_mesh",

                "hierarchical_instanced_mesh"
            ):

                placement = (
                    static_mesh_component_info(

                        actor,

                        component,

                        source_level,

                        actor_chain
                    )
                )

                manifest[
                    "geometry"
                ][
                    "placements"
                ].append(
                    placement
                )

                mesh = safe_property(

                    component,

                    "static_mesh",

                    None
                )

                if mesh:

                    mesh_path = object_path(
                        mesh
                    )

                    if mesh_path not in mesh_registry:

                        mesh_registry[
                            mesh_path
                        ] = {

                            **mesh_info(mesh),

                            "usage_count":
                                0,

                            "instance_total":
                                0,

                            "materials":
                                []
                        }

                    mesh_registry[
                        mesh_path
                    ][
                        "usage_count"
                    ] += 1

                    mesh_registry[
                        mesh_path
                    ][
                        "instance_total"
                    ] += placement.get(
                        "instance_count",
                        1
                    )

                    for material in placement[
                        "materials"
                    ]:

                        material_path = (
                            material.get(
                                "path"
                            )
                        )

                        if not material_path:
                            continue

                        if material_path not in (
                            mesh_registry[
                                mesh_path
                            ][
                                "materials"
                            ]
                        ):

                            mesh_registry[
                                mesh_path
                            ][
                                "materials"
                            ].append(
                                material_path
                            )

                        material_object = None

                        try:

                            material_object = (
                                unreal.load_object(
                                    None,
                                    material_path
                                )
                            )

                        except Exception:
                            material_object = None

                        if (
                            material_path
                            not in
                            material_registry
                        ):

                            if material_object:

                                material_registry[
                                    material_path
                                ] = {

                                    **material_full_info(
                                        material_object
                                    ),

                                    "usage_count":
                                        0,

                                    "mesh_references":
                                        []
                                }

                            else:

                                material_registry[
                                    material_path
                                ] = {

                                    **material,

                                    "material_instance":
                                        {
                                            "is_material_instance":
                                                "MaterialInstance"
                                                in
                                                str(
                                                    material.get(
                                                        "class",
                                                        ""
                                                    )
                                                ),

                                            "parent":
                                                None,

                                            "scalar_parameters":
                                                [],

                                            "vector_parameters":
                                                [],

                                            "texture_parameters":
                                                [],

                                            "runtime_virtual_texture_parameters":
                                                [],

                                            "static_switch_parameters":
                                                [],

                                            "extraction_notes":
                                                [
                                                    "Material asset "
                                                    "could not be resolved."
                                                ]
                                        },

                                    "usage_count":
                                        0,

                                    "mesh_references":
                                        []
                                }

                        if material_object:

                            register_material(

                                material_object,

                                material_registry,

                                texture_registry
                            )

                        material_registry[
                            material_path
                        ][
                            "usage_count"
                        ] += 1

                        if mesh_path not in (
                            material_registry[
                                material_path
                            ][
                                "mesh_references"
                            ]
                        ):

                            material_registry[
                                material_path
                            ][
                                "mesh_references"
                            ].append(
                                mesh_path
                            )

            # ------------------------------------------------
            # SKELETAL MESH (V10.1: previously counted but never
            # actually registered anywhere, so skinned meshes were
            # silently dropped from every downstream export step)
            # ------------------------------------------------

            elif kind == "skeletal_mesh":

                skel_mesh = safe_property(
                    component,
                    "skeletal_mesh_asset",
                    None
                )

                if skel_mesh is None:
                    # UE < 5.something used "skeletal_mesh" instead of
                    # "skeletal_mesh_asset". Try the legacy name too so
                    # this keeps working across engine versions.
                    skel_mesh = safe_property(
                        component,
                        "skeletal_mesh",
                        None
                    )

                skel_placement = {
                    "kind": "skeletal_mesh",
                    "actor": {
                        "path": object_path(actor),
                        "name": object_name(actor),
                        "class": class_name(actor),
                        "class_path": class_path(actor),
                        "origin_type": actor_origin_type(actor)
                    },
                    "component": {
                        "path": object_path(component),
                        "name": object_name(component),
                        "class": class_name(component),
                        "class_path": class_path(component)
                    },
                    "source_level": object_path(source_level),
                    "level_instance_chain": actor_chain or [],
                    "mesh": mesh_info(skel_mesh),
                    "transform": component_transform(component),
                    "actor_transform": actor_transform(actor),
                    "materials": component_materials(component)
                }

                manifest[
                    "geometry"
                ][
                    "skeletal_mesh_placements"
                ].append(
                    skel_placement
                )

                if skel_mesh:

                    skel_path = object_path(skel_mesh)

                    if skel_path not in manifest["geometry"]["unique_skeletal_meshes"]:

                        manifest["geometry"]["unique_skeletal_meshes"][skel_path] = {
                            **mesh_info(skel_mesh),
                            "usage_count": 0,
                            "materials": []
                        }

                    manifest["geometry"]["unique_skeletal_meshes"][skel_path]["usage_count"] += 1

                    for material in skel_placement["materials"]:

                        material_path = material.get("path")

                        if not material_path:
                            continue

                        if material_path not in manifest["geometry"]["unique_skeletal_meshes"][skel_path]["materials"]:
                            manifest["geometry"]["unique_skeletal_meshes"][skel_path]["materials"].append(material_path)

                        material_object = None

                        try:
                            material_object = unreal.load_object(None, material_path)
                        except Exception:
                            material_object = None

                        if material_object:
                            register_material(
                                material_object,
                                material_registry,
                                texture_registry
                            )

            # ------------------------------------------------
            # DECAL
            # ------------------------------------------------

            elif kind == "decal":

                manifest[
                    "effects"
                ][
                    "decals"
                ].append(

                    decal_component_info(

                        actor,

                        component,

                        source_level,

                        actor_chain
                    )
                )

                material = safe_property(
                    component,
                    "decal_material",
                    None
                )

                if material:

                    material_path = object_path(
                        material
                    )

                    register_material(
                        material,
                        material_registry,
                        texture_registry
                    )

                    if material_path in material_registry:

                        material_registry[
                            material_path
                        ][
                            "usage_count"
                        ] += 1

            # ------------------------------------------------
            # NIAGARA
            # ------------------------------------------------

            elif kind == "niagara":

                manifest[
                    "effects"
                ][
                    "niagara"
                ].append(

                    niagara_component_info(

                        actor,

                        component,

                        source_level,

                        actor_chain
                    )
                )

            # ------------------------------------------------
            # LIGHT
            # ------------------------------------------------

            elif kind == "light":

                manifest[
                    "world_features"
                ][
                    "lights"
                ].append(

                    light_component_info(

                        actor,

                        component,

                        source_level,

                        actor_chain
                    )
                )

            # ------------------------------------------------
            # AUDIO
            # ------------------------------------------------

            elif kind == "audio":

                audio_info = (
                    audio_component_info(

                        actor,

                        component,

                        source_level,

                        actor_chain
                    )
                )

                manifest[
                    "world_features"
                ][
                    "audio"
                ].append(
                    audio_info
                )

            # ------------------------------------------------
            # PARTICLE
            # ------------------------------------------------

            elif kind == "particle":

                manifest[
                    "effects"
                ][
                    "particles"
                ].append({

                    "actor": {

                        "path":
                            object_path(actor),

                        "name":
                            object_name(actor),

                        "class":
                            class_name(actor)
                    },

                    "component": {

                        "path":
                            object_path(component),

                        "name":
                            object_name(component),

                        "class":
                            class_name(component)
                    },

                    "source_level":
                        object_path(
                            source_level
                        ),

                    "level_instance_chain":
                        actor_chain,

                    "transform":
                        component_transform(
                            component
                        )
                })

    TIMINGS[
        "actor_component_analysis"
    ] = time.time() - t0

    # ========================================================
    # FINALIZE REGISTRIES
    # ========================================================

    t0 = time.time()

    manifest[
        "geometry"
    ][
        "unique_meshes"
    ] = mesh_registry

    manifest[
        "materials"
    ][
        "unique_materials"
    ] = material_registry

    manifest[
        "textures"
    ][
        "unique_textures"
    ] = texture_registry

    manifest[
        "geometry"
    ][
        "component_counts"
    ] = dict(
        component_counter
    )

    manifest[
        "component_inventory"
    ][
        "counts"
    ] = dict(
        component_counter
    )

    manifest[
        "actors"
    ][
        "accessible_unique_count"
    ] = len(
        all_actors
    )

    manifest[
        "actors"
    ][
        "by_category"
    ] = dict(
        actor_category_counter
    )

    manifest[
        "actors"
    ][
        "by_origin_type"
    ] = dict(
        actor_origin_counter
    )

    TIMINGS[
        "registry_finalization"
    ] = time.time() - t0

    # ========================================================
    # BLUEPRINT FINALIZATION
    # ========================================================

    blueprint_count = len(
        manifest[
            "blueprints"
        ][
            "actors"
        ]
    )

    native_count = (
        actor_origin_counter[
            "native"
        ]
    )

    unknown_count = (
        actor_origin_counter[
            "unknown"
        ]
    )

    manifest[
        "blueprints"
    ][
        "class_counts"
    ] = dict(
        blueprint_class_counter
    )

    manifest[
        "blueprints"
    ][
        "native_actor_count"
    ] = native_count

    manifest[
        "blueprints"
    ][
        "blueprint_actor_count"
    ] = blueprint_count

    manifest[
        "blueprints"
    ][
        "unknown_actor_count"
    ] = unknown_count

    # ========================================================
    # ASSET INVENTORY
    # ========================================================

    unique_mesh_count = len(
        mesh_registry
    )

    unique_material_count = len(
        material_registry
    )

    unique_texture_count = len(
        texture_registry
    )

    unique_li_world_count = len(
        level_instance_world_assets
    )

    manifest[
        "asset_inventory"
    ] = {

        "static_mesh_assets":
            unique_mesh_count,

        "material_assets":
            unique_material_count,

        "texture_assets":
            unique_texture_count,

        "skeletal_mesh_assets":
            0,

        "level_instance_world_assets":
            unique_li_world_count
    }

    # ========================================================
    # COUNTS
    # ========================================================

    static_mesh_placements = len(
        manifest[
            "geometry"
        ][
            "placements"
        ]
    )

    niagara_count = len(
        manifest[
            "effects"
        ][
            "niagara"
        ]
    )

    decal_count = len(
        manifest[
            "effects"
        ][
            "decals"
        ]
    )

    light_count = len(
        manifest[
            "world_features"
        ][
            "lights"
        ]
    )

    audio_count = (
        component_counter[
            "audio"
        ]
    )

    landscape_count = len(
        manifest[
            "world_features"
        ][
            "landscape"
        ]
    )

    foliage_count = len(
        manifest[
            "world_features"
        ][
            "foliage"
        ]
    )

    level_instance_count = len(
        level_instance_registry
    )

    # ========================================================
    # V10 FINALIZE RECONSTRUCTION TRANSFORMS
    # ========================================================

    t0 = time.time()

    v10_transform_diagnostic = finalize_v10_transforms(
        manifest
    )

    TIMINGS[
        "v10_transform_finalization"
    ] = time.time() - t0

    # ========================================================
    # STRONG CONSISTENCY CHECKS
    # ========================================================

    consistency_checks = {

        "direct_actors_nonzero":
            len(direct_actors) > 0,

        "li_registry_matches_placements":
            (
                level_instance_count
                ==
                len(
                    canonical_li_records
                )
            ),

        "li_hierarchy_matches_registry":
            (
                hierarchy_node_count
                ==
                level_instance_count
            ),

        "li_unique_paths_match_registry":
            (
                len(
                    level_instance_registry
                )
                ==
                len(
                    set(
                        level_instance_registry.keys()
                    )
                )
            ),

        "accessible_actor_count_nonzero":
            len(all_actors) > 0,

        "v10_li_transforms_complete":
            v10_transform_diagnostic["li_transform_failures"] == 0,

        "v10_geometry_transforms_complete":
            v10_transform_diagnostic["geometry_transform_failures"] == 0,

        "v10_mesh_references_valid":
            v10_transform_diagnostic["missing_mesh_references"] == 0,

        "v10_material_references_valid":
            v10_transform_diagnostic["missing_material_references"] == 0,

        "v10_li_parents_valid":
            v10_transform_diagnostic["missing_li_parent_references"] == 0,

        "v10_placement_ids_unique":
            v10_transform_diagnostic["duplicate_placement_ids"] == 0
    }

    critical_warnings = []

    # --------------------------------------------------------
    # Important regression guard.
    #
    # If direct actors exist but the scene contains a LevelInstance
    # and none were discovered, this is NOT a valid empty result.
    # --------------------------------------------------------

    if (
        len(direct_actors) > 0
        and
        len(root_level_instances) > 0
        and
        level_instance_count == 0
    ):

        critical_warnings.append({

            "severity":
                "CRITICAL",

            "type":
                "LEVEL_INSTANCE_SCAN_REGRESSION",

            "message":
                "Direct LevelInstances exist but the canonical "
                "LevelInstance registry is empty. The recursive "
                "V4/V5/V6 scanner did not recover their loaded content."
        })

    if (
        len(direct_actors) > 0
        and
        len(all_actors) <= len(direct_actors)
        and
        len(root_level_instances) > 0
    ):

        critical_warnings.append({

            "severity":
                "WARNING",

            "type":
                "NO_DEEP_LEVEL_INSTANCE_CONTENT",

            "message":
                "Direct LevelInstances were found but no additional "
                "accessible actors were discovered from their loaded levels."
        })

    # --------------------------------------------------------
    # Empty LI registry is suspicious when roots exist.
    # --------------------------------------------------------

    if (
        len(root_level_instances) > 0
        and
        level_instance_count == 0
    ):

        consistency_checks[
            "root_li_discovery"
        ] = False

    else:

        consistency_checks[
            "root_li_discovery"
        ] = True

    consistency_ok = all(
        consistency_checks.values()
    )

    # ========================================================
    # CONVERSION DIAGNOSTIC
    # ========================================================

    manifest[
        "conversion_diagnostic"
    ] = {

        "scan_status":
            (
                "COMPLETE_FOR_ACCESSIBLE_CONTENT"
                if consistency_ok
                else "COMPLETED_WITH_DIAGNOSTIC_WARNINGS"
            ),

        "direct_actor_count":
            len(direct_actors),

        "accessible_unique_actor_count":
            len(all_actors),

        "static_mesh_actor_count":
            actor_category_counter[
                "static_mesh"
            ],

        "skeletal_mesh_actor_count":
            actor_category_counter[
                "skeletal_mesh"
            ],

        "static_mesh_component_count":
            component_counter[
                "static_mesh"
            ],

        "instanced_mesh_component_count":
            component_counter[
                "instanced_mesh"
            ],

        "hierarchical_instanced_mesh_component_count":
            component_counter[
                "hierarchical_instanced_mesh"
            ],

        "static_mesh_placements":
            static_mesh_placements,

        "unique_static_mesh_assets":
            unique_mesh_count,

        "unique_material_assets":
            unique_material_count,

        "unique_texture_assets":
            unique_texture_count,

        "level_instance_placements":
            level_instance_count,

        "unique_level_instance_world_assets":
            unique_li_world_count,

        "level_instance_occurrences_found":
            li_occurrence_count,

        "level_instance_unique_actor_paths":
            level_instance_count,

        "level_instance_duplicate_encounters_ignored":
            li_duplicate_encounters,

        "blueprint_actor_count":
            blueprint_count,

        "native_actor_count":
            native_count,

        "unknown_actor_count":
            unknown_count,

        "niagara_component_count":
            niagara_count,

        "decal_component_count":
            decal_count,

        "light_component_count":
            light_count,

        "audio_component_count":
            audio_count,

        "landscape_actor_count":
            landscape_count,

        "foliage_actor_count":
            foliage_count,

        "world_partition_system_actor_count":
            manifest[
                "world_partition"
            ][
                "system_actor_count"
            ],

        "consistency":
            consistency_checks,

        "critical_warnings":
            critical_warnings,

        "deduplication": {

            "mesh_assets":
                True,

            "material_assets":
                True,

            "texture_assets":
                True,

            "level_instance_world_assets":
                True,

            "level_instance_actor_paths":
                True,

            "placements_preserved":
                True,

            "level_instance_placements_preserved":
                True
        },

        "v10_scope": {

            "nested_transform_composition":
                True,

            "nested_transform_composition_reserved_for":
                None,

            "material_parameter_extraction":
                "BEST_EFFORT",

            "texture_reference_extraction":
                "BEST_EFFORT",

            "nanite_extraction":
                "BEST_EFFORT",

            "blueprint_logic_extraction":
                False
        }
    }

    # V10.1 fix: the dict literal above completely REPLACES
    # manifest["conversion_diagnostic"], which silently destroyed the
    # "v10_transform_diagnostic" key written earlier by
    # finalize_v10_transforms() (including geometry_transform_failure_details
    # and missing_mesh_reference_details). This was a pre-existing bug,
    # not something introduced by the earlier patch - the console output
    # was correct because it reads the local variable, but the JSON file
    # itself never actually contained this breakdown. Re-attaching it here.
    manifest["conversion_diagnostic"]["v10_transform_diagnostic"] = v10_transform_diagnostic

    # ========================================================
    # CONVERSION RISKS
    # ========================================================

    risks = []

    if level_instance_count > 0:

        risks.append({

            "severity":
                "HIGH",

            "type":
                "LEVEL_INSTANCES",

            "count":
                level_instance_count,

            "message":
                "LevelInstances detected. Godot reconstruction "
                "must preserve source scenes and placement transforms."
        })

    if landscape_count > 0:

        risks.append({

            "severity":
                "HIGH",

            "type":
                "LANDSCAPE",

            "count":
                landscape_count,

            "message":
                "Landscape requires a dedicated terrain "
                "reconstruction/export pipeline."
        })

    if niagara_count > 0:

        risks.append({

            "severity":
                "HIGH",

            "type":
                "NIAGARA",

            "count":
                niagara_count,

            "message":
                "Niagara systems require Godot-side "
                "reconstruction or replacement."
        })

    if blueprint_count > 0:

        risks.append({

            "severity":
                "HIGH",

            "type":
                "BLUEPRINTS",

            "count":
                blueprint_count,

            "message":
                "Blueprint logic cannot be reconstructed "
                "from transforms/components alone."
        })

    if static_mesh_placements > 0:

        risks.append({

            "severity":
                "MEDIUM",

            "type":
                "STATIC_MESH",

            "count":
                static_mesh_placements,

            "message":
                "Static meshes require asset conversion "
                "plus placement reconstruction."
        })

    if decal_count > 0:

        risks.append({

            "severity":
                "MEDIUM",

            "type":
                "DECALS",

            "count":
                decal_count,

            "message":
                "Decal materials and projection behaviour "
                "must be recreated in Godot."
        })

    if light_count > 0:

        risks.append({

            "severity":
                "MEDIUM",

            "type":
                "LIGHTING",

            "count":
                light_count,

            "message":
                "UE5 and Godot lighting models are not "
                "semantically identical."
        })

    if unique_texture_count == 0 and unique_material_count > 0:

        risks.append({

            "severity":
                "INFO",

            "type":
                "TEXTURE_EXTRACTION",

            "count":
                unique_material_count,

            "message":
                "No explicit texture registry entries were "
                "resolved. This may be an API limitation or "
                "materials may not expose texture parameters."
        })

    manifest[
        "conversion_risks"
    ] = risks

    # ========================================================
    # WARNINGS
    # ========================================================

    if manifest[
        "world_partition"
    ][
        "detected"
    ]:

        manifest[
            "warnings"
        ].append({

            "severity":
                "HIGH",

            "type":
                "WORLD_PARTITION",

            "message":
                "World Partition detected. Only currently "
                "accessible/loaded content is guaranteed to be present."
        })

    if li_duplicate_encounters > 0:

        manifest[
            "warnings"
        ].append({

            "severity":
                "INFO",

            "type":
                "LEVEL_INSTANCE_DUPLICATES",

            "message":
                "Repeated encounters of the same LevelInstance "
                "actor path were detected and deduplicated."
        })

    manifest[
        "warnings"
    ].append({

        "severity":
            "INFO",

        "type":
            "TRANSFORM_CONTEXT",

        "message":
            "Transforms are preserved in their accessible UE level "
            "context and V10 also stores composed world-space transforms. "
            "World Partition still limits guarantees to accessible/loaded content."
    })

    manifest[
        "warnings"
    ].append({

        "severity":
            "INFO",

        "type":
            "ASSET_DEDUPLICATION",

        "message":
            "Assets are deduplicated but every distinct placement "
            "remains preserved."
    })

    manifest[
        "warnings"
    ].extend(
        critical_warnings
    )

    # ========================================================
    # TIMINGS
    # ========================================================

    TIMINGS[
        "total"
    ] = time.time() - total_start

    manifest[
        "timings"
    ] = dict(
        TIMINGS
    )

    # ========================================================
    # SAVE JSON
    # ========================================================

    t0 = time.time()

    try:

        output_directory = os.path.dirname(
            OUTPUT_PATH
        )

        os.makedirs(
            output_directory,
            exist_ok=True
        )

        with open(
            OUTPUT_PATH,
            "w",
            encoding="utf-8"
        ) as file:

            json.dump(

                manifest,

                file,

                indent=2,

                ensure_ascii=False
            )

    except Exception:

        print(
            "ERROR: impossible d'écrire le manifest V9."
        )

        traceback.print_exc()

        return

    TIMINGS[
        "json_save"
    ] = time.time() - t0

    # ========================================================
    # TXT SUMMARY
    # ========================================================

    t0 = time.time()

    try:

        output_directory = os.path.dirname(
            TXT_OUTPUT_PATH
        )

        os.makedirs(
            output_directory,
            exist_ok=True
        )

        with open(
            TXT_OUTPUT_PATH,
            "w",
            encoding="utf-8"
        ) as file:

            file.write(
                "============================================================\n"
            )

            file.write(
                " UE5 -> GODOT CONVERSION MANIFEST - V10\n"
            )

            file.write(
                "============================================================\n\n"
            )

            file.write(
                "WORLD\n"
            )

            file.write(
                "  Path                 : "
                + str(
                    object_path(world)
                )
                + "\n"
            )

            file.write(
                "  Resolution           : "
                + str(
                    world_resolution
                )
                + "\n"
            )

            file.write(
                "  Direct actors        : "
                + str(
                    len(direct_actors)
                )
                + "\n\n"
            )

            file.write(
                "LEVEL INSTANCE ANALYSIS\n"
            )

            file.write(
                "  Direct LIs           : "
                + str(
                    len(root_level_instances)
                )
                + "\n"
            )

            file.write(
                "  Occurrences          : "
                + str(
                    li_occurrence_count
                )
                + "\n"
            )

            file.write(
                "  Unique actor paths   : "
                + str(
                    level_instance_count
                )
                + "\n"
            )

            file.write(
                "  Canonical placements : "
                + str(
                    len(canonical_li_records)
                )
                + "\n"
            )

            file.write(
                "  Hierarchy nodes      : "
                + str(
                    hierarchy_node_count
                )
                + "\n"
            )

            file.write(
                "  Source World assets  : "
                + str(
                    unique_li_world_count
                )
                + "\n"
            )

            file.write(
                "  Duplicate encounters : "
                + str(
                    li_duplicate_encounters
                )
                + "\n\n"
            )

            file.write(
                "ACCESSIBLE CONTENT\n"
            )

            file.write(
                "  Unique actors        : "
                + str(
                    len(all_actors)
                )
                + "\n\n"
            )

            file.write(
                "GEOMETRY\n"
            )

            file.write(
                "  StaticMesh actors    : "
                + str(
                    actor_category_counter[
                        "static_mesh"
                    ]
                )
                + "\n"
            )

            file.write(
                "  StaticMesh comps     : "
                + str(
                    component_counter[
                        "static_mesh"
                    ]
                )
                + "\n"
            )

            file.write(
                "  ISM                  : "
                + str(
                    component_counter[
                        "instanced_mesh"
                    ]
                )
                + "\n"
            )

            file.write(
                "  HISM                 : "
                + str(
                    component_counter[
                        "hierarchical_instanced_mesh"
                    ]
                )
                + "\n"
            )

            file.write(
                "  Mesh placements      : "
                + str(
                    static_mesh_placements
                )
                + "\n"
            )

            file.write(
                "  Unique meshes        : "
                + str(
                    unique_mesh_count
                )
                + "\n"
            )

            file.write(
                "  Unique materials     : "
                + str(
                    unique_material_count
                )
                + "\n"
            )

            file.write(
                "  Unique textures      : "
                + str(
                    unique_texture_count
                )
                + "\n\n"
            )

            file.write(
                "ACTORS\n"
            )

            file.write(
                "  Blueprint            : "
                + str(
                    blueprint_count
                )
                + "\n"
            )

            file.write(
                "  Native               : "
                + str(
                    native_count
                )
                + "\n"
            )

            file.write(
                "  Unknown              : "
                + str(
                    unknown_count
                )
                + "\n\n"
            )

            file.write(
                "COMPONENTS / FX\n"
            )

            file.write(
                "  Niagara              : "
                + str(
                    niagara_count
                )
                + "\n"
            )

            file.write(
                "  Decals               : "
                + str(
                    decal_count
                )
                + "\n"
            )

            file.write(
                "  Lights               : "
                + str(
                    light_count
                )
                + "\n"
            )

            file.write(
                "  Audio                : "
                + str(
                    audio_count
                )
                + "\n"
            )

            file.write(
                "  Foliage              : "
                + str(
                    foliage_count
                )
                + "\n"
            )

            file.write(
                "  Landscape            : "
                + str(
                    landscape_count
                )
                + "\n\n"
            )

            file.write(
                "WORLD PARTITION\n"
            )

            file.write(
                "  Detected             : "
                + str(
                    manifest[
                        "world_partition"
                    ][
                        "detected"
                    ]
                )
                + "\n"
            )

            file.write(
                "  System actors        : "
                + str(
                    manifest[
                        "world_partition"
                    ][
                        "system_actor_count"
                    ]
                )
                + "\n\n"
            )

            file.write(
                "CONSISTENCY\n"
            )

            file.write(
                "  Status               : "
                + (
                    "PASS"
                    if consistency_ok
                    else "WARNING"
                )
                + "\n"
            )

            for key, value in (
                consistency_checks.items()
            ):

                file.write(
                    "  "
                    + str(key)
                    + " : "
                    + str(value)
                    + "\n"
                )

            file.write(
                "\n"
            )

            file.write(
                "V10 NOTES\n"
            )

            file.write(
                "  Nested transform composition : ENABLED (FTransform)\n"
            )

            file.write(
                "  Material parameters          : BEST EFFORT\n"
            )

            file.write(
                "  Texture references           : BEST EFFORT\n"
            )

            file.write(
                "  Nanite information           : BEST EFFORT\n"
            )

            file.write(
                "  Blueprint logic               : NOT EXTRACTED\n\n"
            )

            file.write(
                "TIMINGS\n"
            )

            for key, value in TIMINGS.items():

                file.write(
                    "  "
                    + str(key)
                    + " : "
                    + "{:.4f}".format(
                        float(value)
                    )
                    + " s\n"
                )

    except Exception:

        print(
            "WARNING: impossible d'écrire le résumé TXT V10."
        )

        traceback.print_exc()

    TIMINGS[
        "txt_save"
    ] = time.time() - t0

    # ========================================================
    # FINAL CONSOLE DIAGNOSTIC
    # ========================================================

    print("")
    print("============================================================")
    print(" UE5 CONVERSION DIAGNOSTIC V10")
    print("============================================================")

    print("")
    print("WORLD")

    print(
        "  World                         :",
        object_path(world)
    )

    print(
        "  Resolution                    :",
        world_resolution
    )

    print(
        "  Direct actors                 :",
        len(direct_actors)
    )

    print("")
    print("LEVEL INSTANCE ANALYSIS")

    print(
        "  Direct LevelInstances         :",
        len(root_level_instances)
    )

    print(
        "  LI occurrences found          :",
        li_occurrence_count
    )

    print(
        "  Unique LI actor paths         :",
        level_instance_count
    )

    print(
        "  Duplicate encounters ignored  :",
        li_duplicate_encounters
    )

    print(
        "  Unique source World assets    :",
        unique_li_world_count
    )

    print(
        "  Canonical placements          :",
        len(canonical_li_records)
    )

    print(
        "  Hierarchy nodes               :",
        hierarchy_node_count
    )

    print("")
    print("ACCESSIBLE CONTENT")

    print(
        "  Unique actors                 :",
        len(all_actors)
    )

    print("")
    print("GEOMETRY")

    print(
        "  StaticMesh actors             :",
        actor_category_counter[
            "static_mesh"
        ]
    )

    print(
        "  StaticMesh components         :",
        component_counter[
            "static_mesh"
        ]
    )

    print(
        "  ISM components                :",
        component_counter[
            "instanced_mesh"
        ]
    )

    print(
        "  HISM components               :",
        component_counter[
            "hierarchical_instanced_mesh"
        ]
    )

    print(
        "  Mesh placements               :",
        static_mesh_placements
    )

    print(
        "  Unique StaticMeshes           :",
        unique_mesh_count
    )

    print(
        "  Unique Materials              :",
        unique_material_count
    )

    print(
        "  Unique Textures               :",
        unique_texture_count
    )

    print("")
    print("ACTORS")

    print(
        "  Blueprint actors              :",
        blueprint_count
    )

    print(
        "  Native actors                 :",
        native_count
    )

    print(
        "  Unknown actors                :",
        unknown_count
    )

    print("")
    print("COMPONENTS / FX")

    print(
        "  Niagara components            :",
        niagara_count
    )

    print(
        "  Decals                        :",
        decal_count
    )

    print(
        "  Lights                        :",
        light_count
    )

    print(
        "  Audio                         :",
        audio_count
    )

    print(
        "  Foliage actors                :",
        foliage_count
    )

    print(
        "  Landscape actors              :",
        landscape_count
    )

    print("")
    print("WORLD PARTITION")

    print(
        "  Detected                      :",
        manifest[
            "world_partition"
        ][
            "detected"
        ]
    )

    print(
        "  System actors                 :",
        manifest[
            "world_partition"
        ][
            "system_actor_count"
        ]
    )

    print(
        "  Counted as scene content      : NO"
    )

    # ========================================================
    # LI CONSISTENCY
    # ========================================================

    print("")
    print("============================================================")
    print(" LEVEL INSTANCE CONSISTENCY V10")
    print("============================================================")

    print(
        "  Occurrences                  :",
        li_occurrence_count
    )

    print(
        "  Canonical unique actors      :",
        level_instance_count
    )

    print(
        "  Canonical placements         :",
        len(canonical_li_records)
    )

    print(
        "  Hierarchy nodes              :",
        hierarchy_node_count
    )

    print(
        "  Source Worlds                :",
        unique_li_world_count
    )

    print(
        "  Duplicate encounters         :",
        li_duplicate_encounters
    )

    if consistency_ok:

        print(
            "  STATUS                       : PASS"
        )

    else:

        print(
            "  STATUS                       : WARNING"
        )

        for warning in critical_warnings:

            print(
                "  !!",
                warning[
                    "severity"
                ],
                ":",
                warning[
                    "type"
                ]
            )

    # ========================================================
    # CUMULATIVE INVENTORY
    # ========================================================

    print("")
    print("============================================================")
    print(" CUMULATIVE INVENTORY")
    print("============================================================")

    print(
        "  V4 LevelInstance enumeration : PRESERVED"
    )

    print(
        "  V5 component inventory       : PRESERVED"
    )

    print(
        "  V5 asset inventory           : PRESERVED"
    )

    print(
        "  V5 hierarchy                 : PRESERVED"
    )

    print(
        "  V6 mesh placements           : PRESERVED"
    )

    print(
        "  V6 material mapping          : PRESERVED"
    )

    print(
        "  V6 transforms                : PRESERVED"
    )

    print(
        "  V7 Blueprint classification  : PRESERVED"
    )

    print(
        "  V7 nested LI placements     : PRESERVED"
    )

    print(
        "  V8 canonical LI registry    : PRESERVED"
    )

    print(
        "  V8 LI consistency diagnostic : PRESERVED"
    )

    print(
        "  V9 material parameters       : PRESERVED"
    )

    print(
        "  V10 final world transforms   : ADDED"
    )

    print(
        "  V10 stable placement IDs     : ADDED"
    )

    print(
        "  V9 texture registry          : ADDED"
    )

    print(
        "  V9 mesh diagnostics           : ADDED"
    )

    print(
        "  V9 Blueprint components       : ADDED"
    )

    print(
        "  V9 FX diagnostics             : ADDED"
    )

    print(
        "  V9 timing diagnostics         : ADDED"
    )

    print("")
    print("V10 RECONSTRUCTION READINESS")
    print(
        "  Final LI transforms          :",
        v10_transform_diagnostic["li_final_transform_count"]
    )
    print(
        "  Final mesh transforms        :",
        v10_transform_diagnostic["geometry_final_transform_count"]
    )
    print(
        "  Mesh transform failures      :",
        v10_transform_diagnostic["geometry_transform_failures"]
    )
    print(
        "  Missing mesh references      :",
        v10_transform_diagnostic["missing_mesh_references"],
        "(real, excludes empty mesh slots)"
    )
    print(
        "  Empty mesh slots (not a bug) :",
        v10_transform_diagnostic["empty_mesh_slot_count"]
    )
    print(
        "  Broken LI chain links        :",
        v10_transform_diagnostic["broken_li_chain_links"]
    )
    print(
        "  ISM/HISM real instances      :",
        v10_transform_diagnostic["instance_transform_total"]
    )
    print(
        "  Instance transform failures  :",
        v10_transform_diagnostic["instance_transform_failures"]
    )
    print(
        "  Unique SkeletalMeshes        :",
        len(manifest["geometry"]["unique_skeletal_meshes"])
    )
    print(
        "  Reconstruction ready (geo)   :",
        manifest["reconstruction"]["ready_for_godot_geometry"]
    )
    print(
        "  Reconstruction ready (FX)    :",
        manifest["reconstruction"]["ready_for_godot_fx"]
    )
    for _fx_name, _fx_diag in manifest["reconstruction"]["fx_transform_diagnostics"].items():
        print(
            "    " + _fx_name.ljust(10),
            "total", _fx_diag["total"],
            "| ok", _fx_diag["with_transform"],
            "| FAILED", _fx_diag["failed"]
        )

    # ========================================================
    # OUTPUT
    # ========================================================

    print("")
    print("============================================================")
    print(" OUTPUT")
    print("============================================================")

    print(
        "  Manifest V9 écrit dans :"
    )

    print(
        "  ",
        OUTPUT_PATH
    )

    print(
        "  Résumé V9 écrit dans   :"
    )

    print(
        "  ",
        TXT_OUTPUT_PATH
    )

    print("")
    print(
        "  Total time              :",
        "{:.3f}".format(
            time.time() - total_start
        ),
        "s"
    )

    print("")
    print("============================================================")
    print(" V10 COMPLETE")
    print("============================================================")



# ============================================================
# EXECUTE
# ============================================================

main()