"""
AUTO TERRAIN MATERIAL - Unreal Engine 5.5  (v9)
Companion of auto_terrain_generator_ue55_v3_1.py

v9 (manual retouch layers on top of the validated v8 - rendering untouched)
------------------------------------------------------------------------
6 paintable landscape layers are inserted AFTER the procedural chain and
BEFORE the AO / macro / brightness pass, so a painted area gets exactly the
same final treatment as a procedural one (no seam in wide shots).
    Paint_RockySand  Paint_RockyMoss  Paint_DirtRough01
    Paint_DirtRough02  Paint_MudyPath  Paint_RockSlate
Rules that keep the material black-proof:
  * the weight is read as a SCALAR mask: LandscapeLayerWeight with Base = 0
    and Layer = 1, both pins CONNECTED. Whatever the node does internally
    (lerp(Base, Layer, w) or Base + Layer * w) the result is exactly w, and
    when the layer does not exist on the landscape it falls back to Base = 0
    -> the procedural result passes through untouched.
  * nothing is ever fed to MP_NORMAL that can be (0,0,0): the normal is
    always a Lerp between two real normals. A zero-length normal is the
    usual reason a landscape turns completely black.
  * no new texture sample: the 6 sets are already sampled by the procedural
    chain, get_sample() returns the cached ones (sampler count unchanged).
  * live kill switch PaintStrength (group '8 Paint'): 0 = exact v8 render,
    without rebuilding anything.
  * debug_flat_normal=True forces MP_NORMAL to (0,0,1) -> tells in 10 s
    whether a black terrain comes from the normal chain or not.

v8 (improvements on top of the validated v7.1 - distribution untouched)
------------------------------------------------------------------------
Every addition is isolated behind a live parameter; setting it to 0 gives v7.1 back.
- Rock look (group '7 Rock'): biplanar world projection on steep faces (no
  vertical stretching), warmer/darker tint matching the ground palette, large
  tone variation, normal detail flattened where it would stretch.
  -> RockBiplanar, RockSteepNormalFlatten, RockTint, RockToneVariation
- Hill flanks: below the piedmont only true cliffs (> 46 deg) get rock, so the
  white strips on hill sides become hill dirt.  -> LowRockStrictness
- Mud fill (group '6 Mud'): mudy_path fills the LOW parts of the plateau ground
  texture (between stones). Amount varies very slowly, no threshold, so it cannot
  form spots.  -> MudAmount, MudLevel, MudSoftness, MudVariation

v7.1 (validated render)
-----------------------
- Plateau accents removed (mudy_path, dirt_fine_02): as thresholded noise islands
  they always read as dark spots. Validated live by setting their thresholds to 2;
  removing them from LOW_STACK gives the exact same result (same threshold for
  rocky_moss_floor), so re-running no longer brings the spots back.

v7 (from the v6 render)
-----------------------
- Hierarchy instead of equal shares, per zone:
      LOW PLATEAU : rocky_sand main, rocky_moss_floor secondary,
                    mudy_path + dirt_fine_02 as small decorative accents
      HILLS       : dirt_rough_01 main, dirt_rough_02 secondary (subtle variation),
                    rocky_moss_floor as small accents
      ROCK        : rock_slate on steep slopes and mountain faces
- Accents use their own live zone size (AccentZoneSize), much smaller than the
  main regions (LowZoneSize / HillZoneSize).
- Distance tiling: the main texture of each zone is re-sampled at a larger scale
  far from the camera -> no visible repeat pattern in wide shots (+2 samples).
- Altitude rock needs some slope (MountainSlopeGate): rounded hill tops keep hill
  dirt instead of white "snow" caps; steep faces stay rock. Slate brightness 0.7.

Kept: exact asset references, 0..1 noise, live zone sizes, height blending,
measured low level, MI_AutoTerrain for live tuning.

Usage (Output Log, Cmd mode):
    py "C:\\...\\auto_terrain_material_ue55_v8.py"
Diagnostics only (builds nothing):
    py -c "exec(open(r'C:\\...\\auto_terrain_material_ue55_v8.py').read()); list_sets()"
    py -c "exec(open(r'C:\\...\\auto_terrain_material_ue55_v8.py').read()); preview_levels()"
"""

import math
from dataclasses import dataclass
from statistics import NormalDist
from typing import Optional

import unreal

LOG = "[AutoTerrainMat]"
MEL = unreal.MaterialEditingLibrary
EAL = unreal.EditorAssetLibrary


def log(msg):
    unreal.log(f"{LOG} {msg}")


def warn(msg):
    unreal.log_warning(f"{LOG} {msg}")


# =============================================================================
# TEXTURE SETS  (exact references)
# =============================================================================

@dataclass(frozen=True)
class SetDef:
    mi: str = ""                  # glTF Material Instance -> textures read from its parameters
    base: str = ""                # or plain Texture2D references
    normal: str = ""
    mask: str = ""                # Necropolis packed mask: roughness in G (MF_Dirt)
    height: str = ""              # height map (R)
    tiling_m: float = 4.0
    use_orm: bool = True
    roughness: float = 0.85       # used when there is no ORM / mask
    brightness: float = 1.0       # live parameter Brightness_<key>
    normal_flatness: float = 0.0  # FlattenNormal amount; negative = stronger relief
    height_scale: float = 1.0     # live parameter HeightScale_<key>


TT = "/Game/Necropolis/Landscape/TerrainTextures"
NL = "/Game/Necropolis/Landscape/Textures"

SETS = {
    # Necropolis pack (artist recipe from MF_Dirt)
    "mudy_path": SetDef(base=f"{NL}/T_mudy_path_D", normal=f"{NL}/T_mudy_path_N",
                        mask=f"{NL}/T_mudy_path_M", height=f"{NL}/T_mudy_path_H", normal_flatness=-0.5),
    "rocky_moss_floor": SetDef(base=f"{NL}/T_rocky_moss_floor_D", normal=f"{NL}/T_rocky_moss_floor_N",
                               mask=f"{NL}/T_rocky_moss_floor_M", height=f"{NL}/T_rocky_moss_floor_H",
                               normal_flatness=-0.5),
    # Quixel glTF
    "rocky_sand": SetDef(mi=f"{TT}/Rocky_Sand-a913d25a/MI_vd4pbdt", tiling_m=3.5),
    "dirt_fine_02": SetDef(mi=f"{TT}/Military_Trenches_Ground_Dirt_Fine_02-5d55a266/MI_yd0keak"),
    "dirt_rough_01": SetDef(mi=f"{TT}/Military_Trenches_Ground_Dirt_Rough_01-d588d839/MI_yd0lfcqcc"),
    "dirt_rough_02": SetDef(mi=f"{TT}/Military_Trenches_Ground_Dirt_Rough_02-b0736626/MI_yd0lacrs"),
    "grassy_soil": SetDef(mi=f"{TT}/grassy_soil_xbreair_4k_u_extracted/MI_xbreair"),   # unused (grass)
    "forest_path": SetDef(mi=f"{TT}/forest_path/MI_ugsnfawlw"),                        # unused
    # rock
    "rock_slate": SetDef(base="/Game/StarterContent/Textures/T_Rock_Slate_D",
                         normal="/Game/StarterContent/Textures/T_Rock_Slate_N",
                         tiling_m=6.0, brightness=0.55),
}


# =============================================================================
# ZONE STACKS  (what goes where - edit here)
# =============================================================================

@dataclass(frozen=True)
class Layer:
    key: str
    tier: str       # main | secondary | accent
    share: float    # approximate visible share inside the zone


LOW_STACK = (
    Layer("rocky_sand", "main", 0.55),
    Layer("rocky_moss_floor", "secondary", 0.25),
    # Accents removed after the v7 render: as noise islands they read as dark spots.
    # Layer("mudy_path", "accent", 0.12),
    # Layer("dirt_fine_02", "accent", 0.08),
)

HILL_STACK = (
    Layer("dirt_rough_01", "main", 0.65),
    Layer("dirt_rough_02", "secondary", 0.20),
    Layer("rocky_moss_floor", "accent", 0.15),
)

ROCK_KEY = "rock_slate"
MUD_KEY = "mudy_path"      # crevice fill on the plateau (None disables)
TIERS = ("main", "secondary", "accent")


# =============================================================================
# PAINT LAYERS  (manual retouch - edit here)
# =============================================================================
# (landscape layer name, texture set key)
# The names must match the Target Layers of the Landscape (Paint mode), each
# with a Layer Info assigned - preferably NON weight-blended, so every layer is
# an independent mask over the procedural result.
# Order = priority: the last one painted on a pixel wins.

PAINT_LAYERS = (
    ("Paint_RockySand", "rocky_sand"),
    ("Paint_RockyMoss", "rocky_moss_floor"),
    ("Paint_DirtRough01", "dirt_rough_01"),
    ("Paint_DirtRough02", "dirt_rough_02"),
    ("Paint_MudyPath", "mudy_path"),
    ("Paint_RockSlate", "rock_slate"),
)


# =============================================================================
# CONFIG
# =============================================================================

@dataclass
class MaterialConfig:
    material_path: str = "/Game/AutoTerrain/M_AutoTerrain"
    instance_path: str = "/Game/AutoTerrain/MI_AutoTerrain"
    use_instance: bool = True
    reset_instance_overrides: bool = True   # False = keep the values you tuned in MI_AutoTerrain

    # --- zones (all three sizes are live parameters) --------------------------
    low_zone_size_m: float = 35.0
    hill_zone_size_m: float = 35.0
    accent_zone_size_m: float = 10.0
    zone_levels: int = 1
    zone_softness: float = 0.12
    zone_edge_warp: float = 0.12
    noise_sigma: float = 0.18

    # --- height blending ------------------------------------------------------
    height_blend_depth: float = 0.2
    height_blend_range: float = 1.5

    # --- distance tiling ------------------------------------------------------
    far_tiling_multiplier: float = 3.7      # <= 1 disables it
    far_tiling_start_cm: float = 2500.0
    far_tiling_range_cm: float = 6000.0

    # --- slopes ---------------------------------------------------------------
    hill_slope_start_deg: float = 12.0
    hill_slope_full_deg: float = 26.0
    hill_slope_strength: float = 0.6
    rock_slope_start_deg: float = 34.0
    rock_slope_full_deg: float = 48.0
    mountain_slope_start_deg: float = 10.0  # altitude rock needs at least this slope...
    mountain_slope_full_deg: float = 25.0
    mountain_slope_gate: float = 0.75       # ...0 = altitude alone makes rock, 1 = slope fully required

    low_rock_slope_start_deg: float = 46.0  # below the piedmont, rock only on slopes steeper than this
    low_rock_slope_full_deg: float = 60.0
    low_rock_strictness: float = 1.0        # 0 = v7.1 behaviour, 1 = only true cliffs below the piedmont

    # --- rock look ------------------------------------------------------------
    rock_biplanar: float = 1.0              # 0 = off (v7.1), 1 = world projection on steep faces
    rock_biplanar_sharpness: float = 4.0
    rock_side_start_deg: float = 40.0       # where the side projection starts replacing the top one
    rock_side_full_deg: float = 65.0
    rock_steep_normal_flatten: float = 0.5
    rock_tint: tuple = (1.0, 0.92, 0.82)    # warm grey-brown, matches the ground palette
    rock_tone_variation: float = 0.15

    # --- mud fill -------------------------------------------------------------
    mud_amount: float = 0.6                 # 0 = off (v7.1)
    mud_level: float = 0.30                 # fills ground heights below this
    mud_softness: float = 0.10
    mud_variation: float = 0.25             # how much the fill level drifts across the plateau
    mud_variation_size_m: float = 80.0

    # --- height levels (None = measured automatically) -------------------------
    low_level_top_z: Optional[float] = None
    piedmont_band_cm: Optional[float] = None
    rock_band_cm: Optional[float] = None
    piedmont_fraction: float = 0.08
    rock_band_fraction: float = 0.12
    min_piedmont_cm: float = 400.0
    min_rock_band_cm: float = 800.0
    height_breakup_cm: float = 500.0
    breakup_size_m: float = 18.0
    fallback_low_level_top_z: float = 582.0

    trace_grid: int = 48
    trace_max_retries: int = 10
    level_bin_cm: float = 100.0
    level_search_fraction: float = 0.5
    level_dropoff: float = 0.15
    low_level_margin_cm: float = 150.0

    # --- look -----------------------------------------------------------------
    macro_strength: float = 0.12
    occlusion_strength: float = 0.5
    roughness_multiplier: float = 1.0
    brightness: float = 1.0
    gltf_normal_flip: str = "auto"          # auto | always | never

    # --- manual paint layers --------------------------------------------------
    paint_enabled: bool = True              # False = exact v8 material, no layer node at all
    paint_strength: float = 1.0             # live PaintStrength: 0 = procedural only
    paint_rock_uses_rock_look: bool = True  # painted slate keeps the biplanar rock look
    debug_flat_normal: bool = False         # True = MP_NORMAL forced to (0,0,1) (black terrain test)

    landscape_label: str = ""
    save_all: bool = False


# =============================================================================
# SET LOADING
# =============================================================================

@dataclass
class PBR:
    key: str
    sdef: SetDef
    base: object
    normal: object = None
    orm: object = None
    ao_in_orm: bool = False
    flip_green: bool = False
    mask: object = None
    height: object = None

    def sample_count(self):
        return (1 + (self.normal is not None) + (self.orm is not None)
                + (self.mask is not None) + (self.height is not None))


def _param_name(entry):
    try:
        return str(entry.parameter_info.name)
    except Exception:
        pass
    try:
        return str(entry.get_editor_property("parameter_info").name)
    except Exception:
        return ""


def _load_texture(path, label):
    if not path:
        return None
    if not EAL.does_asset_exist(path):
        warn(f"{label}: texture not found: {path}")
        return None
    tex = unreal.load_asset(path)
    if not isinstance(tex, unreal.Texture2D):
        warn(f"{label}: not a Texture2D: {path}")
        return None
    return tex


def _is_normal_map(tex):
    try:
        return tex.get_editor_property("compression_settings") == unreal.TextureCompressionSettings.TC_NORMALMAP
    except Exception:
        return False


def _needs_green_flip(sdef, normal_tex, cfg):
    """glTF normal maps are OpenGL (+Y), Unreal expects DirectX (-Y)."""
    if normal_tex is None or not sdef.mi:
        return False
    if cfg.gltf_normal_flip == "always":
        return True
    if cfg.gltf_normal_flip == "never":
        return False
    try:
        return not bool(normal_tex.get_editor_property("flip_green_channel"))
    except Exception:
        return False


def load_set(key, sdef, cfg):
    if sdef.mi:
        if not EAL.does_asset_exist(sdef.mi):
            warn(f"{key}: Material Instance not found: {sdef.mi}")
            return None
        mi = unreal.load_asset(sdef.mi)
        if not isinstance(mi, unreal.MaterialInstance):
            warn(f"{key}: not a Material Instance: {sdef.mi}")
            return None
        base = normal = mr = ao = None
        seen = []
        try:
            entries = mi.get_editor_property("texture_parameter_values")
        except Exception:
            entries = []
        for entry in entries:
            name = _param_name(entry)
            low = name.lower().replace(" ", "").replace("_", "")
            try:
                tex = entry.get_editor_property("parameter_value")
            except Exception:
                tex = None
            if not isinstance(tex, unreal.Texture2D):
                continue
            seen.append(name)
            if "basecolor" in low:
                base = base or tex
            elif "normal" in low:
                normal = normal or tex
            elif "metallicroughness" in low or ("metallic" in low and "roughness" in low):
                mr = mr or tex
            elif "occlusion" in low:
                ao = ao or tex
        if base is None:
            warn(f"{key}: no BaseColor texture in {sdef.mi} (texture parameters: {seen})")
            return None
        ao_in_orm = mr is not None and ao is not None and ao.get_path_name() == mr.get_path_name()
        orm = mr if sdef.use_orm else None
        pbr = PBR(key, sdef, base, normal, orm, ao_in_orm and orm is not None,
                  _needs_green_flip(sdef, normal, cfg))
    else:
        base = _load_texture(sdef.base, key)
        if base is None:
            return None
        pbr = PBR(key, sdef, base, _load_texture(sdef.normal, key),
                  mask=_load_texture(sdef.mask, key), height=_load_texture(sdef.height, key))

    if pbr.normal is not None and not _is_normal_map(pbr.normal):
        warn(f"{key}: '{pbr.normal.get_name()}' is not compressed as NormalMap -> normal ignored")
        pbr.normal = None
        pbr.flip_green = False
    return pbr


def _describe(pbr):
    def n(t):
        return t.get_name() if t is not None else "-"
    if pbr.orm is not None:
        rough = f"ORM.G ({n(pbr.orm)})"
    elif pbr.mask is not None:
        rough = f"M.G ({n(pbr.mask)})"
    else:
        rough = f"constant {pbr.sdef.roughness}"
    height = n(pbr.height) if pbr.height is not None else "pseudo (luminance)"
    return (f"{pbr.key:16s} base={n(pbr.base)} normal={n(pbr.normal)}"
            f"{' (green flipped)' if pbr.flip_green else ''} rough={rough} height={height} "
            f"samples={pbr.sample_count()}")


def _validate_stacks():
    for name, stack in (("LOW_STACK", LOW_STACK), ("HILL_STACK", HILL_STACK)):
        seen = set()
        for layer in stack:
            if layer.key not in SETS:
                raise ValueError(f"{name}: '{layer.key}' is not in SETS")
            if layer.tier not in TIERS:
                raise ValueError(f"{name}: '{layer.key}' has unknown tier '{layer.tier}'")
            if layer.share <= 0:
                raise ValueError(f"{name}: '{layer.key}' share must be > 0")
            if layer.key in seen:
                raise ValueError(f"{name}: '{layer.key}' listed twice")
            seen.add(layer.key)
    for layer_name, key in PAINT_LAYERS:
        if key not in SETS:
            raise ValueError(f"PAINT_LAYERS: '{layer_name}' points at '{key}' which is not in SETS")
    names = [n for n, _ in PAINT_LAYERS]
    if len(set(names)) != len(names):
        raise ValueError("PAINT_LAYERS: duplicated landscape layer name")
    if ROCK_KEY and ROCK_KEY not in SETS:
        raise ValueError(f"ROCK_KEY '{ROCK_KEY}' is not in SETS")
    if MUD_KEY and MUD_KEY not in SETS:
        raise ValueError(f"MUD_KEY '{MUD_KEY}' is not in SETS")


def load_needed_sets(cfg):
    """Loads only the sets referenced by the stacks and the rock, once each."""
    keys = []
    for layer in LOW_STACK + HILL_STACK:
        if layer.key not in keys:
            keys.append(layer.key)
    if ROCK_KEY and ROCK_KEY not in keys:
        keys.append(ROCK_KEY)
    if MUD_KEY and MUD_KEY not in keys:
        keys.append(MUD_KEY)
    if cfg.paint_enabled:
        for _, key in PAINT_LAYERS:
            if key not in keys:
                keys.append(key)
    pbrs = {}
    for key in keys:
        pbr = load_set(key, SETS[key], cfg)
        if pbr is not None:
            pbrs[key] = pbr
            log(_describe(pbr))
    return pbrs


# =============================================================================
# SAMPLER / GRAPH HELPERS
# =============================================================================

def sampler_for(tex):
    T = unreal.TextureCompressionSettings
    S = unreal.MaterialSamplerType
    cs = tex.get_editor_property("compression_settings")
    srgb = bool(tex.get_editor_property("srgb"))
    vt = False
    try:
        vt = bool(tex.get_editor_property("virtual_texture_streaming"))
    except Exception:
        pass
    if cs == T.TC_NORMALMAP:
        kind = "NORMAL"
    elif cs == T.TC_MASKS:
        kind = "MASKS"
    elif cs == T.TC_GRAYSCALE:
        kind = "GRAYSCALE" if srgb else "LINEAR_GRAYSCALE"
    elif cs == T.TC_ALPHA:
        kind = "ALPHA"
    else:
        kind = "COLOR" if srgb else "LINEAR_COLOR"
    return getattr(S, f"SAMPLERTYPE_{'VIRTUAL_' if vt else ''}{kind}")


class Graph:
    def __init__(self, material):
        self.mat = material
        self.samples = 0
        self.noises = 0
        self._cache = {}

    def node(self, cls, x, y, **props):
        e = MEL.create_material_expression(self.mat, cls, x, y)
        if e is None:
            raise RuntimeError(f"Could not create {cls}")
        for k, v in props.items():
            e.set_editor_property(k, v)
        return e

    def link(self, src, src_out, dst, dst_in):
        if not MEL.connect_material_expressions(src, src_out, dst, dst_in):
            raise RuntimeError(f"Connection failed: {src.get_name()}.{src_out or '<0>'} -> "
                               f"{dst.get_name()}.{dst_in or '<0>'}")

    def try_link(self, src, src_out, dst, dst_in):
        try:
            return bool(MEL.connect_material_expressions(src, src_out, dst, dst_in))
        except Exception:
            return False

    def output(self, src, src_out, prop):
        if not MEL.connect_material_property(src, src_out, prop):
            raise RuntimeError(f"Connection to {prop} failed")

    def op(self, cls, x, y, a=None, b=None, **props):
        """Binary op helper: a/b are nodes (linked to A/B) or None (const_a/const_b used)."""
        e = self.node(cls, x, y, **props)
        if a is not None:
            self.link(a, "", e, "A")
        if b is not None:
            self.link(b, "", e, "B")
        return e

    def unary(self, cls, x, y, src):
        e = self.node(cls, x, y)
        self.link(src, "", e, "")
        return e

    def mask(self, src, x, y, r=False, g=False, b=False, a=False):
        e = self.node(unreal.MaterialExpressionComponentMask, x, y, r=r, g=g, b=b, a=a)
        self.link(src, "", e, "")
        return e

    def texture(self, tex, x, y, uv):
        e = self.node(unreal.MaterialExpressionTextureSample, x, y)
        e.set_editor_property("texture", tex)
        e.set_editor_property("sampler_type", sampler_for(tex))
        try:
            e.set_editor_property("sampler_source", unreal.SamplerSourceMode.SSM_WRAP_WORLD_GROUP_SHARED)
        except Exception:
            pass
        self.link(uv, "", e, "UVs")
        self.samples += 1
        return e

    def const(self, value, x, y):
        key = ("const", round(float(value), 6))
        if key not in self._cache:
            self._cache[key] = self.node(unreal.MaterialExpressionConstant, x, y, r=float(value))
        return self._cache[key]

    def vec3(self, key, rgb, x, y):
        k = ("vec3", key)
        if k not in self._cache:
            self._cache[k] = self.node(unreal.MaterialExpressionConstant3Vector, x, y,
                                       constant=unreal.LinearColor(rgb[0], rgb[1], rgb[2], 0.0))
        return self._cache[k]

    def flat_normal(self, x, y):
        return self.vec3("flat_normal", (0.0, 0.0, 1.0), x, y)

    def green_flip_vector(self, x, y):
        return self.vec3("green_flip", (1.0, -1.0, 1.0), x, y)

    def coords(self, tiling_m, quad_cm, x, y):
        scale = max(tiling_m * 100.0 / quad_cm, 0.01)
        key = ("coords", round(scale, 4))
        if key not in self._cache:
            self._cache[key] = self.node(unreal.MaterialExpressionLandscapeLayerCoords, x, y,
                                         mapping_scale=scale)
        return self._cache[key]

    def scalar(self, name, value, group, x, y):
        e = self.node(unreal.MaterialExpressionScalarParameter, x, y,
                      parameter_name=name, default_value=float(value))
        try:
            e.set_editor_property("group", group)
        except Exception:
            pass
        return e

    def noise(self, world_pos, size_m, levels, offset, x, y, size_cm_node=None):
        """0..1 noise, feature size ~size_m, decorrelated by a position offset.
        size_cm_node: optional material node giving the feature size in cm (live
        parameter). The position is divided by it and the Noise scale stays 1."""
        baked_scale = 1.0 / max(size_m * 100.0, 1.0)
        n = self.node(unreal.MaterialExpressionNoise, x, y,
                      scale=1.0 if size_cm_node is not None else baked_scale,
                      quality=1, levels=max(1, int(levels)),
                      output_min=0.0, output_max=1.0,
                      turbulence=False, tiling=False)
        pos = self.op(unreal.MaterialExpressionAdd, x - 440, y, a=world_pos, const_b=float(offset))
        if size_cm_node is not None:
            pos = self.op(unreal.MaterialExpressionDivide, x - 220, y, a=pos, b=size_cm_node)
        if not self.try_link(pos, "", n, "Position"):
            if size_cm_node is not None:
                # without the Position input the division is lost: fall back to the baked scale
                n.set_editor_property("scale", baked_scale)
                warn("Noise: Position input not connectable -> zone size baked, not live")
            else:
                warn("Noise: Position input not connectable; noise layers may be correlated")
        self.noises += 1
        return n


CHANNELS = ("color", "normal", "rough", "ao", "height")
LUMA = (0.299, 0.587, 0.114)


def coverage_to_threshold(coverage, sigma):
    c = min(max(coverage, 0.01), 0.99)
    return 0.5 + sigma * NormalDist().inv_cdf(1.0 - c)


def threshold_mask(g, noise, threshold_param, softness_clamped, x, y):
    """saturate((noise - threshold) / softness + 0.5)"""
    sub = g.op(unreal.MaterialExpressionSubtract, x, y, a=noise, b=threshold_param)
    div = g.op(unreal.MaterialExpressionDivide, x + 180, y, a=sub, b=softness_clamped)
    add = g.op(unreal.MaterialExpressionAdd, x + 360, y, a=div, const_b=0.5)
    return g.unary(unreal.MaterialExpressionSaturate, x + 540, y, add)


def slope_mask(g, normal_z, start_deg, full_deg, x, y):
    """0 below start_deg, 1 above full_deg (from VertexNormalWS.Z)."""
    c0 = math.cos(math.radians(start_deg))
    c1 = math.cos(math.radians(full_deg))
    sub = g.op(unreal.MaterialExpressionSubtract, x, y, b=normal_z, const_a=c0)
    mul = g.op(unreal.MaterialExpressionMultiply, x + 180, y, a=sub, const_b=1.0 / max(c0 - c1, 1e-3))
    return g.unary(unreal.MaterialExpressionSaturate, x + 360, y, mul)


def luminance(g, color_pair, x, y):
    dot = g.node(unreal.MaterialExpressionDotProduct, x + 200, y)
    g.link(color_pair[0], color_pair[1], dot, "A")
    g.link(g.vec3("luma", LUMA, x, y + 80), "", dot, "B")
    return dot


def sample_set(g, pbr, quad_cm, x, y):
    """Returns {channel: (node, pin)} for one set."""
    Mul = unreal.MaterialExpressionMultiply
    uv = g.coords(pbr.sdef.tiling_m, quad_cm, x - 300, y)
    group = "5 Sets"

    # colour
    base = g.texture(pbr.base, x, y, uv)
    color = g.node(Mul, x + 300, y)
    bright = g.scalar(f"Brightness_{pbr.key}", pbr.sdef.brightness, group, x + 60, y + 140)
    g.link(base, "RGB", color, "A")
    g.link(bright, "", color, "B")

    # normal (optional green flip, optional FlattenNormal)
    if pbr.normal is not None:
        nrm = g.texture(pbr.normal, x, y + 260, uv)
        normal = (nrm, "RGB")
        if pbr.flip_green:
            flipped = g.node(Mul, x + 300, y + 260)
            g.link(nrm, "RGB", flipped, "A")
            g.link(g.green_flip_vector(x + 60, y + 400), "", flipped, "B")
            normal = (flipped, "")
        if pbr.sdef.normal_flatness != 0.0:
            # FlattenNormal: lerp(N, (0,0,1), flatness). Negative flatness = stronger relief.
            flat = g.node(unreal.MaterialExpressionLinearInterpolate, x + 500, y + 260)
            g.link(normal[0], normal[1], flat, "A")
            g.link(g.flat_normal(x + 300, y + 360), "", flat, "B")
            g.link(g.scalar(f"NormalFlatness_{pbr.key}", pbr.sdef.normal_flatness, group,
                            x + 300, y + 440), "", flat, "Alpha")
            normal = (flat, "")
    else:
        normal = (g.flat_normal(x + 300, y + 260), "")

    # roughness / AO
    if pbr.orm is not None:
        orm = g.texture(pbr.orm, x, y + 520, uv)
        rough = (orm, "G")
        ao = (orm, "R") if pbr.ao_in_orm else (g.const(1.0, x + 300, y + 620), "")
    elif pbr.mask is not None:
        mask = g.texture(pbr.mask, x, y + 520, uv)
        rough = (mask, "G")
        ao = (g.const(1.0, x + 300, y + 620), "")
    else:
        rough = (g.const(pbr.sdef.roughness, x + 300, y + 520), "")
        ao = (g.const(1.0, x + 300, y + 620), "")

    # height: real map, or luminance as pseudo-height
    if pbr.height is not None:
        h_tex = g.texture(pbr.height, x, y + 780, uv)
        h_src = (h_tex, "R")
    else:
        lum = luminance(g, (base, "RGB"), x + 300, y + 780)
        shifted = g.op(unreal.MaterialExpressionSubtract, x + 500, y + 780, a=lum, const_b=0.08)
        h_src = (g.unary(unreal.MaterialExpressionSaturate, x + 800, y + 780,
                         g.op(Mul, x + 650, y + 780, a=shifted, const_b=4.0)), "")
    height = g.node(Mul, x + 950, y + 780)
    g.link(h_src[0], h_src[1], height, "A")
    g.link(g.scalar(f"HeightScale_{pbr.key}", pbr.sdef.height_scale, group, x + 700, y + 900), "", height, "B")

    return {"color": (color, ""), "normal": normal, "rough": rough, "ao": ao, "height": (height, ""),
            "_bright": bright}


def height_blend_alpha(g, mask, h_under, h_over, depth_clamped, blend_range, x, y):
    """Height-aware transition:
         alpha = saturate((h_over - h_under + (2*mask - 1) * range) / depth + 0.5)
       mask 0 -> 0, mask 1 -> 1 (for heights in 0..1 and range >= 1 + depth/2);
       in between, the higher surface wins (stones poke through mud)."""
    Sub = unreal.MaterialExpressionSubtract
    Mul = unreal.MaterialExpressionMultiply
    Add = unreal.MaterialExpressionAdd
    diff = g.node(Sub, x, y)
    g.link(h_over[0], h_over[1], diff, "A")
    g.link(h_under[0], h_under[1], diff, "B")
    bias = g.op(Sub, x + 300, y + 120,
                a=g.op(Mul, x + 150, y + 120, a=mask, const_b=2.0 * blend_range), const_b=blend_range)
    s = g.op(Add, x + 450, y, a=diff, b=bias)
    d = g.op(unreal.MaterialExpressionDivide, x + 600, y, a=s, b=depth_clamped)
    return g.unary(unreal.MaterialExpressionSaturate, x + 900, y,
                   g.op(Add, x + 750, y, a=d, const_b=0.5))


def lerp_pbr(g, a, b, alpha, x, y):
    out = {}
    for i, ch in enumerate(CHANNELS):
        node = g.node(unreal.MaterialExpressionLinearInterpolate, x, y + i * 120)
        g.link(a[ch][0], a[ch][1], node, "A")
        g.link(b[ch][0], b[ch][1], node, "B")
        g.link(alpha, "", node, "Alpha")
        out[ch] = (node, "")
    return out


def far_tiling(g, pbr, channels, quad_cm, multiplier, dist_alpha, x, y):
    """Main texture re-sampled at a larger scale far from the camera (hides repetition)."""
    if dist_alpha is None or multiplier <= 1.0:
        return channels
    uv = g.coords(pbr.sdef.tiling_m * multiplier, quad_cm, x - 300, y)
    far = g.texture(pbr.base, x, y, uv)
    far_color = g.node(unreal.MaterialExpressionMultiply, x + 300, y)
    g.link(far, "RGB", far_color, "A")
    g.link(channels["_bright"], "", far_color, "B")
    blended = g.node(unreal.MaterialExpressionLinearInterpolate, x + 500, y)
    g.link(channels["color"][0], channels["color"][1], blended, "A")
    g.link(far_color, "", blended, "B")
    g.link(dist_alpha, "", blended, "Alpha")
    out = dict(channels)
    out["color"] = (blended, "")
    return out


# =============================================================================
# MATERIAL ASSETS
# =============================================================================

def get_or_create_material(path):
    pkg, name = path.rsplit("/", 1)
    if EAL.does_asset_exist(path):
        mat = EAL.load_asset(path)
        if not isinstance(mat, unreal.Material):
            raise RuntimeError(f"'{path}' exists but is not a Material")
        MEL.delete_all_material_expressions(mat)
        log(f"Rebuilding existing material {path}")
        return mat
    EAL.make_directory(pkg)
    mat = unreal.AssetToolsHelpers.get_asset_tools().create_asset(
        name, pkg, unreal.Material, unreal.MaterialFactoryNew())
    if not mat:
        raise RuntimeError(f"Could not create material {path}")
    log(f"Created material {path}")
    return mat


def get_or_create_instance(path, parent, reset):
    try:
        pkg, name = path.rsplit("/", 1)
        if EAL.does_asset_exist(path):
            mi = EAL.load_asset(path)
            if not isinstance(mi, unreal.MaterialInstanceConstant):
                warn(f"'{path}' exists but is not a MaterialInstanceConstant -> using the parent material")
                return None
        else:
            EAL.make_directory(pkg)
            mi = unreal.AssetToolsHelpers.get_asset_tools().create_asset(
                name, pkg, unreal.MaterialInstanceConstant, unreal.MaterialInstanceConstantFactoryNew())
            if not mi:
                warn(f"Could not create {path} -> using the parent material")
                return None
        MEL.set_material_instance_parent(mi, parent)
        if reset:
            MEL.clear_all_material_instance_parameters(mi)
            log("MI_AutoTerrain overrides reset (reset_instance_overrides=False keeps your tweaks)")
        MEL.update_material_instance(mi)
        EAL.save_loaded_asset(mi)
        return mi
    except Exception as exc:
        warn(f"Material Instance step failed ({exc}) -> using the parent material")
        return None


# =============================================================================
# HEIGHT LEVELS
# =============================================================================

@dataclass
class Levels:
    low_top: float
    piedmont: float
    rock_band: float
    source: str
    low_fraction: Optional[float] = None


def _parse_hit(hit):
    fields = None
    for getter in (lambda h: unreal.GameplayStatics.break_hit_result(h), lambda h: h.to_tuple()):
        try:
            fields = getter(hit)
            if fields:
                break
        except Exception:
            fields = None
    if not fields:
        return None, None
    z = None
    actor = None
    for f in fields:
        if z is None and not isinstance(f, (bool, int, float, str)) and hasattr(f, "z"):
            try:
                z = float(f.z)
            except Exception:
                pass
        if actor is None and isinstance(f, unreal.Actor):
            actor = f
    return actor, z


def sample_landscape_heights(cfg, actors):
    world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
    bmin = [math.inf, math.inf, math.inf]
    bmax = [-math.inf, -math.inf, -math.inf]
    for actor in actors:
        try:
            origin, extent = actor.get_actor_bounds(False)
        except Exception:
            continue
        if extent.x <= 1.0 and extent.y <= 1.0:
            continue
        for i, (o, e) in enumerate(((origin.x, extent.x), (origin.y, extent.y), (origin.z, extent.z))):
            bmin[i] = min(bmin[i], o - e)
            bmax[i] = max(bmax[i], o + e)
    if bmin[0] == math.inf:
        warn("Landscape bounds unavailable")
        return []

    grid = max(8, int(cfg.trace_grid))
    top, bottom = bmax[2] + 10000.0, bmin[2] - 10000.0
    channel = unreal.TraceTypeQuery.TRACE_TYPE_QUERY1
    ignore, ignored = [], set()
    zs = []
    for i in range(grid):
        for j in range(grid):
            x = bmin[0] + (i + 0.5) * (bmax[0] - bmin[0]) / grid
            y = bmin[1] + (j + 0.5) * (bmax[1] - bmin[1]) / grid
            start, end = unreal.Vector(x, y, top), unreal.Vector(x, y, bottom)
            for _ in range(max(1, cfg.trace_max_retries)):
                hit = unreal.SystemLibrary.line_trace_single(
                    world, start, end, channel, True, ignore, unreal.DrawDebugTrace.NONE, True)
                if isinstance(hit, tuple):
                    hit = hit[-1] if (len(hit) > 1 and hit[0]) else None
                if hit is None:
                    break
                actor, z = _parse_hit(hit)
                if z is None:
                    break
                if actor is None or isinstance(actor, unreal.LandscapeProxy):
                    zs.append(z)
                    break
                path = actor.get_path_name()
                if path in ignored:
                    break
                ignored.add(path)
                ignore.append(actor)
    log(f"Height sampling: {len(zs)} landscape hits over {grid * grid} cells "
        f"({len(ignore)} occluding actors skipped)")
    return zs


def levels_from_heights(zs, cfg):
    """Lowest *surface* (not lowest point): the most populated height band in the
    lower part of the terrain, extended up until the population drops off."""
    zs = sorted(zs)
    n = len(zs)
    lo = zs[int(0.01 * (n - 1))]
    hi = zs[int(0.99 * (n - 1))]
    bin_cm = max(cfg.level_bin_cm, 1.0)
    nb = max(1, int((hi - lo) // bin_cm) + 1)
    counts = [0] * nb
    for z in zs:
        if lo <= z <= hi:
            counts[min(int((z - lo) // bin_cm), nb - 1)] += 1
    smooth = [(counts[max(i - 1, 0)] + counts[i] + counts[min(i + 1, nb - 1)]) / 3.0 for i in range(nb)]

    search_top = max(1, min(nb, int(math.ceil(nb * cfg.level_search_fraction))))
    mode_i = max(range(search_top), key=lambda i: smooth[i])
    mode_c = max(smooth[mode_i], 1e-6)
    top_i = mode_i
    while top_i + 1 < search_top and smooth[top_i + 1] >= mode_c * cfg.level_dropoff:
        top_i += 1

    plateau_z = lo + (mode_i + 0.5) * bin_cm
    low_top = lo + (top_i + 1) * bin_cm + cfg.low_level_margin_cm
    relief = max(hi - low_top, 1.0)
    low_fraction = sum(1 for z in zs if z <= low_top) / n
    return plateau_z, low_top, relief, lo, hi, low_fraction


def estimate_levels(cfg, landscape, proxies):
    relief = None
    low_fraction = None
    if cfg.low_level_top_z is not None:
        low_top, source = float(cfg.low_level_top_z), "config"
    else:
        zs = sample_landscape_heights(cfg, [landscape] + list(proxies))
        if len(zs) < 50:
            warn(f"Only {len(zs)} height samples -> fallback low level Z {cfg.fallback_low_level_top_z}")
            low_top, source = float(cfg.fallback_low_level_top_z), "fallback"
        else:
            plateau_z, low_top, relief, lo, hi, low_fraction = levels_from_heights(zs, cfg)
            source = "measured"
            log(f"Terrain Z range {lo:.0f} .. {hi:.0f} cm, dominant low surface at {plateau_z:.0f} cm, "
                f"low level top {low_top:.0f} cm ({low_fraction * 100:.0f}% of samples)")

    def band(explicit, fraction, minimum):
        if explicit is not None:
            return float(explicit)
        if relief is None:
            return float(minimum)
        return max(float(minimum), relief * fraction)

    levels = Levels(low_top,
                    band(cfg.piedmont_band_cm, cfg.piedmont_fraction, cfg.min_piedmont_cm),
                    band(cfg.rock_band_cm, cfg.rock_band_fraction, cfg.min_rock_band_cm),
                    source, low_fraction)
    log(f"Levels ({levels.source}): LowLevelTopZ={levels.low_top:.0f} PiedmontBand={levels.piedmont:.0f} "
        f"RockBand={levels.rock_band:.0f}")
    return levels


# =============================================================================
# BUILD MATERIAL
# =============================================================================

def rock_look(g, channels, pbr, cfg, world_pos, vnormal, s_side, breakup):
    """Rock appearance, all live: biplanar colour on steep faces, flattened normal
    where the top projection stretches, tint and large tone variation."""
    Mul = unreal.MaterialExpressionMultiply
    Sub = unreal.MaterialExpressionSubtract
    Add = unreal.MaterialExpressionAdd
    Div = unreal.MaterialExpressionDivide
    Lerp = unreal.MaterialExpressionLinearInterpolate
    Sat = unreal.MaterialExpressionSaturate
    Abs = unreal.MaterialExpressionAbs
    group = "7 Rock"
    x, y = -2800, 20000
    out = dict(channels)

    # biplanar: YZ plane for faces looking along X, XZ plane for faces looking along Y
    tiling_cm = max(pbr.sdef.tiling_m * 100.0, 1.0)
    uv_yz = g.op(Div, x + 200, y, a=g.mask(world_pos, x, y, g=True, b=True), const_b=tiling_cm)
    uv_xz = g.op(Div, x + 200, y + 250, a=g.mask(world_pos, x, y + 250, r=True, b=True), const_b=tiling_cm)
    s_yz = g.texture(pbr.base, x + 400, y, uv_yz)
    s_xz = g.texture(pbr.base, x + 400, y + 250, uv_xz)
    nx = g.unary(Abs, x + 200, y + 500, g.mask(vnormal, x, y + 500, r=True))
    ny = g.unary(Abs, x + 200, y + 600, g.mask(vnormal, x, y + 600, g=True))
    sharp = g.scalar("RockBiplanarSharpness", cfg.rock_biplanar_sharpness, group, x + 200, y + 700)
    w = g.unary(Sat, x + 800, y + 500,
                g.op(Add, x + 650, y + 500, a=g.op(Mul, x + 500, y + 500,
                                                 a=g.op(Sub, x + 350, y + 500, a=nx, b=ny), b=sharp),
                     const_b=0.5))
    side = g.node(Lerp, x + 800, y)
    g.link(s_xz, "RGB", side, "A")
    g.link(s_yz, "RGB", side, "B")
    g.link(w, "", side, "Alpha")
    side_col = g.op(Mul, x + 1000, y, a=side, b=channels["_bright"])
    side_alpha = g.op(Mul, x + 1000, y + 150, a=s_side,
                      b=g.scalar("RockBiplanar", cfg.rock_biplanar, group, x + 800, y + 250))
    color = g.node(Lerp, x + 1200, y)
    g.link(channels["color"][0], channels["color"][1], color, "A")
    g.link(side_col, "", color, "B")
    g.link(side_alpha, "", color, "Alpha")

    # normal: top-projected detail stretches on steep faces -> flatten it there
    flat = g.node(Lerp, x + 1200, y + 400)
    g.link(channels["normal"][0], channels["normal"][1], flat, "A")
    g.link(g.flat_normal(x + 1000, y + 450), "", flat, "B")
    g.link(g.op(Mul, x + 1000, y + 550, a=s_side,
                b=g.scalar("RockSteepNormalFlatten", cfg.rock_steep_normal_flatten, group, x + 800, y + 550)),
           "", flat, "Alpha")

    # tint + large tone variation
    tint = g.node(unreal.MaterialExpressionVectorParameter, x + 1200, y + 700,
                  parameter_name="RockTint", default_value=unreal.LinearColor(*cfg.rock_tint, 1.0))
    try:
        tint.set_editor_property("group", group)
    except Exception:
        pass
    color = g.op(Mul, x + 1400, y, a=color, b=tint)
    p_tone = g.scalar("RockToneVariation", cfg.rock_tone_variation, group, x + 1200, y + 850)
    tone = g.node(Lerp, x + 1400, y + 850)
    g.link(g.unary(unreal.MaterialExpressionOneMinus, x + 1300, y + 800, p_tone), "", tone, "A")
    g.link(g.op(Add, x + 1300, y + 900, a=p_tone, const_b=1.0), "", tone, "B")
    g.link(breakup, "", tone, "Alpha")
    color = g.op(Mul, x + 1600, y, a=color, b=tone)

    out["color"] = (color, "")
    out["normal"] = (flat, "")
    return out


def build_stack(g, name, stack, pbrs, get_sample, cfg, ctx, stack_index, y0):
    """Builds one zone (plateau or hills): main base, then secondary / accents as
    height-blended noise layers. Returns channel dict or None if nothing is loaded."""
    Mul = unreal.MaterialExpressionMultiply
    Add = unreal.MaterialExpressionAdd
    layers = [layer for layer in stack if layer.key in pbrs]
    for layer in stack:
        if layer.key not in pbrs:
            warn(f"{name}: '{layer.key}' not loaded -> skipped")
    if not layers:
        return None
    if layers[0].tier != "main":
        warn(f"{name}: main set missing -> '{layers[0].key}' used as base")

    group = f"{2 + stack_index} {name} zones"
    base = far_tiling(g, pbrs[layers[0].key], get_sample(layers[0].key), ctx["quad_cm"],
                      cfg.far_tiling_multiplier, ctx["dist_alpha"], -1200, y0)
    acc = base

    shares = [layer.share for layer in layers]
    total = sum(shares)
    cum = shares[0]
    log(f"  {name} '{layers[0].key}' ({layers[0].tier}): base, visible share ~{shares[0] / total * 100:.0f}%")
    for k in range(1, len(layers)):
        layer = layers[k]
        cum += shares[k]
        coverage = shares[k] / cum
        threshold = coverage_to_threshold(coverage, cfg.noise_sigma)
        y = y0 + k * 500
        accent = layer.tier == "accent"
        size_node = ctx["accent_cm"] if accent else ctx["zone_cm"][name]
        base_size_m = cfg.accent_zone_size_m if accent else (
            cfg.low_zone_size_m if name == "Low" else cfg.hill_zone_size_m)
        factor = 1.0 + 0.12 * k
        layer_cm = g.op(Mul, -2300, y, a=size_node, const_b=factor)
        n = g.noise(ctx["world_pos"], base_size_m * factor, cfg.zone_levels,
                    100003.0 * (stack_index + 1) + 17011.0 * k + 5003.0, -2000, y, size_cm_node=layer_cm)
        warped = g.op(Add, -1900, y + 60, a=n, b=ctx["warp"])
        tp = g.scalar(f"{name}Threshold_{layer.key}", threshold, group, -1800, y + 140)
        m = threshold_mask(g, warped, tp, ctx["zone_soft"], -1600, y)
        alpha = height_blend_alpha(g, m, acc["height"], get_sample(layer.key)["height"],
                                   ctx["hb_depth"], ctx["blend_range"], -900, y)
        acc = lerp_pbr(g, acc, get_sample(layer.key), alpha, -600 + k * 250, y0)
        log(f"  {name} '{layer.key}' ({layer.tier}): coverage {coverage:.2f} -> threshold {threshold:.3f}, "
            f"visible share ~{shares[k] / total * 100:.0f}%")
    return acc


def layer_weight_mask(g, layer_name, x, y):
    """Scalar 0..1 weight of one landscape paint layer.

    Base = 0 and Layer = 1, BOTH PINS CONNECTED. This is the only wiring that
    does not depend on what LandscapeLayerWeight does internally:
        lerp(0, 1, w) = w        and        0 + 1 * w = w
    An unconnected pin is what breaks this node: Base unconnected falls back to
    ConstBase (0,0,0), Layer unconnected makes the expression fail to compile -
    both end up as a black landscape while the Python side reports success.
    When the layer does not exist on the landscape the node returns Base = 0,
    so the procedural result passes through unchanged."""
    node = g.node(unreal.MaterialExpressionLandscapeLayerWeight, x, y,
                  parameter_name=layer_name)
    try:
        node.set_editor_property("preview_weight", 0.0)   # material preview = procedural
    except Exception:
        pass
    try:
        node.set_editor_property("const_base", unreal.Vector(0.0, 0.0, 0.0))
    except Exception:
        pass
    g.link(g.const(0.0, x - 250, y), "", node, "Base")
    g.link(g.const(1.0, x - 250, y + 110), "", node, "Layer")
    return node


def apply_paint_layers(g, acc, pbrs, get_sample, cfg, overrides=None, x0=1700, y0=14000):
    """Manual retouch on top of the procedural result.

    One Lerp per channel per layer, alpha = layer weight * PaintStrength.
    Alpha 0 everywhere by default -> as long as nothing is painted the render is
    bit-for-bit the v8 one."""
    if not cfg.paint_enabled:
        log("Paint layers disabled (paint_enabled=False)")
        return acc
    overrides = overrides or {}
    strength = g.scalar("PaintStrength", cfg.paint_strength, "8 Paint", x0 - 700, y0 - 250)
    used = []
    for i, (layer_name, key) in enumerate(PAINT_LAYERS):
        if key not in pbrs:
            warn(f"Paint '{layer_name}': set '{key}' not loaded -> layer skipped")
            continue
        y = y0 + i * 800
        weight = layer_weight_mask(g, layer_name, x0, y)
        alpha = g.op(unreal.MaterialExpressionMultiply, x0 + 300, y, a=weight, b=strength)
        target = overrides.get(key) or get_sample(key)
        acc = lerp_pbr(g, acc, target, alpha, x0 + 550, y)
        used.append(layer_name)
    if used:
        log(f"Paint layers ({len(used)}): " + ", ".join(used))
        log("  -> create one Layer Info per name in Landscape > Paint > Layers "
            "(Non Weight-Blended recommended), then paint. Unpainted = procedural.")
    else:
        warn("No paint layer could be built (no set loaded)")
    return acc


def _log_statistics(mat):
    """recompile_material() reports nothing: a shader error is invisible from Python
    and shows up as a black landscape. The sampler count is the usual culprit."""
    try:
        st = MEL.get_statistics(mat)
    except Exception:
        warn("Material statistics unavailable -> check the Stats panel of the Material Editor")
        return
    values = {}
    for name in ("num_pixel_shader_instructions", "num_samplers", "num_texture_samples",
                 "num_virtual_texture_samples", "num_interpolator_scalars",
                 "num_user_texture_coordinates"):
        try:
            values[name] = st.get_editor_property(name)
        except Exception:
            pass
    if values:
        log("Shader stats: " + ", ".join(f"{k.replace('num_', '')}={v}" for k, v in values.items()))
        n = values.get("num_samplers")
        if isinstance(n, int) and n > 13:
            warn(f"{n} samplers used: the landscape weightmaps need 2 to 4 more. "
                 f"Over 16 the shader fails and the terrain renders black.")
    else:
        log(f"Shader stats: {st}")


def build_material(cfg, quad_cm, levels, pbrs):
    Sub = unreal.MaterialExpressionSubtract
    Mul = unreal.MaterialExpressionMultiply
    Div = unreal.MaterialExpressionDivide
    Add = unreal.MaterialExpressionAdd
    Max = unreal.MaterialExpressionMax
    Sat = unreal.MaterialExpressionSaturate
    Lerp = unreal.MaterialExpressionLinearInterpolate

    mat = get_or_create_material(cfg.material_path)
    g = Graph(mat)
    world_pos = g.node(unreal.MaterialExpressionWorldPosition, -4200, 0)

    # one sample per set, shared by every zone that uses it
    cache = {}
    def get_sample(key):
        if key not in cache:
            cache[key] = sample_set(g, pbrs[key], quad_cm, -3200, len(cache) * 1100)
        return cache[key]

    # shared nodes --------------------------------------------------------------
    breakup = g.noise(world_pos, cfg.breakup_size_m, 2, 131071.0, -2400, 8100)
    centered = g.op(Sub, -2000, 8100, a=breakup, const_b=0.5)

    def size_cm(param_name, value_m, group, y):
        p = g.scalar(param_name, value_m, group, -2800, y)
        return g.op(Max, -2400, y, a=g.op(Mul, -2600, y, a=p, const_b=100.0), const_b=100.0)

    depth = g.node(unreal.MaterialExpressionPixelDepth, -2800, -2600)
    far_range = g.op(Max, -2400, -2500,
                     a=g.scalar("FarTilingRange", cfg.far_tiling_range_cm, "5 Look", -2600, -2500), const_b=1.0)
    dist_alpha = g.unary(Sat, -2000, -2600, g.op(Div, -2200, -2600,
                                                 a=g.op(Sub, -2600, -2600, a=depth,
                                                        b=g.scalar("FarTilingStart", cfg.far_tiling_start_cm,
                                                                   "5 Look", -2800, -2700)),
                                                 b=far_range))

    ctx = {
        "quad_cm": quad_cm,
        "world_pos": world_pos,
        "dist_alpha": dist_alpha if cfg.far_tiling_multiplier > 1.0 else None,
        "zone_cm": {"Low": size_cm("LowZoneSize", cfg.low_zone_size_m, "2 Low zones", -1800),
                    "Hill": size_cm("HillZoneSize", cfg.hill_zone_size_m, "3 Hill zones", -1700)},
        "accent_cm": size_cm("AccentZoneSize", cfg.accent_zone_size_m, "4 Zones common", -1600),
        "zone_soft": g.op(Max, -2400, -1200, a=g.scalar("ZoneSoftness", cfg.zone_softness,
                                                          "4 Zones common", -2600, -1200), const_b=0.001),
        "warp": g.op(Mul, -2200, -1400, a=centered,
                     b=g.scalar("ZoneEdgeWarp", cfg.zone_edge_warp, "4 Zones common", -2400, -1400)),
        "hb_depth": g.op(Max, -2400, -1500, a=g.scalar("HeightBlendDepth", cfg.height_blend_depth,
                                                         "4 Zones common", -2600, -1500), const_b=0.01),
        "blend_range": max(cfg.height_blend_range, 1.0 + cfg.height_blend_depth / 2.0),
    }

    # --- 1. zones ----------------------------------------------------------------------
    low = build_stack(g, "Low", LOW_STACK, pbrs, get_sample, cfg, ctx, 0, 12000)
    hill = build_stack(g, "Hill", HILL_STACK, pbrs, get_sample, cfg, ctx, 1, 16000)
    if low is None and hill is None:
        raise RuntimeError("Neither the plateau nor the hill stack has a loaded set.")
    acc = low if low is not None else hill

    # --- 2. slope + height masks ---------------------------------------------------
    vnormal = g.node(unreal.MaterialExpressionVertexNormalWS, -2600, 7400)
    nz = g.mask(vnormal, -2400, 7400, b=True)
    s_hill = slope_mask(g, nz, cfg.hill_slope_start_deg, cfg.hill_slope_full_deg, -2200, 7400)
    s_rock = slope_mask(g, nz, cfg.rock_slope_start_deg, cfg.rock_slope_full_deg, -2200, 7600)
    s_mount = slope_mask(g, nz, cfg.mountain_slope_start_deg, cfg.mountain_slope_full_deg, -2200, 7800)
    s_steep = slope_mask(g, nz, cfg.low_rock_slope_start_deg, cfg.low_rock_slope_full_deg, -2200, 8000)
    s_side = slope_mask(g, nz, cfg.rock_side_start_deg, cfg.rock_side_full_deg, -2200, 8200)

    z = g.mask(world_pos, -2400, 7900, b=True)
    p_low = g.scalar("LowLevelTopZ", levels.low_top, "1 Levels", -2200, 8300)
    p_pied = g.scalar("PiedmontBand", levels.piedmont, "1 Levels", -2200, 8420)
    p_rband = g.scalar("RockBand", levels.rock_band, "1 Levels", -2200, 8540)
    p_jitter = g.scalar("HeightBreakup", cfg.height_breakup_cm, "1 Levels", -2200, 8660)
    p_hill_slope = g.scalar("HillSlopeStrength", cfg.hill_slope_strength, "1 Levels", -2200, 8780)
    p_gate = g.scalar("MountainSlopeGate", cfg.mountain_slope_gate, "1 Levels", -2200, 8900)

    jitter = g.op(Mul, -1800, 8100, a=centered, b=p_jitter)
    hz = g.op(Add, -1600, 7900, a=z, b=jitter)
    rel = g.op(Sub, -1400, 7900, a=hz, b=p_low)
    pied_w = g.op(Max, -1400, 8420, a=p_pied, const_b=1.0)
    h_pied = g.unary(Sat, -1000, 7900, g.op(Div, -1200, 7900, a=rel, b=pied_w))
    rel_rock = g.op(Sub, -1400, 8100, a=rel, b=p_pied)
    rock_w = g.op(Max, -1400, 8540, a=p_rband, const_b=1.0)
    h_rock = g.unary(Sat, -1000, 8100, g.op(Div, -1200, 8100, a=rel_rock, b=rock_w))

    # altitude rock needs slope: gate = lerp(1, slope_mountain, MountainSlopeGate)
    gate = g.node(Lerp, -900, 8200, const_a=1.0)
    g.link(s_mount, "", gate, "B")
    g.link(p_gate, "", gate, "Alpha")
    h_rock_gated = g.op(Mul, -700, 8150, a=h_rock, b=gate)

    hill_mask = g.op(Max, -800, 7500, a=g.op(Mul, -1000, 7400, a=s_hill, b=p_hill_slope), b=h_pied)
    # below the piedmont, only true cliffs get rock (hill flanks keep hill dirt)
    p_strict = g.scalar("LowRockStrictness", cfg.low_rock_strictness, "1 Levels", -2200, 9020)
    low_rock = g.node(Lerp, -700, 7600)
    g.link(s_rock, "", low_rock, "A")
    g.link(s_steep, "", low_rock, "B")
    g.link(p_strict, "", low_rock, "Alpha")
    s_rock_eff = g.node(Lerp, -600, 7650)
    g.link(low_rock, "", s_rock_eff, "A")
    g.link(s_rock, "", s_rock_eff, "B")
    g.link(h_pied, "", s_rock_eff, "Alpha")
    rock_mask = g.op(Max, -500, 7700, a=s_rock_eff, b=h_rock_gated)

    # --- 3a. mud fills the low parts of the plateau ground (no threshold, no shapes) --
    if low is not None and MUD_KEY and MUD_KEY in pbrs:
        mud_s = get_sample(MUD_KEY)
        group = "6 Mud"
        slow = g.noise(world_pos, cfg.mud_variation_size_m, 1, 222221.0, -2400, 9600)
        drift = g.op(Mul, -2000, 9600, a=g.op(Sub, -2200, 9600, a=slow, const_b=0.5),
                     b=g.scalar("MudVariation", cfg.mud_variation, group, -2200, 9720))
        level = g.op(Add, -1800, 9600, a=g.scalar("MudLevel", cfg.mud_level, group, -2000, 9840), b=drift)
        below = g.node(Sub, -1600, 9600)
        g.link(level, "", below, "A")
        g.link(acc["height"][0], acc["height"][1], below, "B")
        soft = g.op(Max, -1600, 9760, a=g.scalar("MudSoftness", cfg.mud_softness, group, -1800, 9760),
                    const_b=0.001)
        fill = g.unary(Sat, -1200, 9600, g.op(Div, -1400, 9600, a=below, b=soft))
        alpha = g.op(Mul, -1000, 9600, a=fill, b=g.scalar("MudAmount", cfg.mud_amount, group, -1200, 9760))
        acc = lerp_pbr(g, acc, mud_s, alpha, 1100, 12000)
    elif MUD_KEY and MUD_KEY not in pbrs:
        warn(f"Mud fill: '{MUD_KEY}' not loaded -> skipped")

    # --- 3. plateau -> hills -> rock (height-blended) -------------------------------
    if low is not None and hill is not None:
        alpha = height_blend_alpha(g, hill_mask, acc["height"], hill["height"],
                                   ctx["hb_depth"], ctx["blend_range"], -600, 9000)
        acc = lerp_pbr(g, acc, hill, alpha, 1300, 12000)
    elif hill is None:
        warn("Hill stack empty -> hills keep the plateau ground")

    rock = pbrs.get(ROCK_KEY) if ROCK_KEY else None
    rock_s = None
    if rock is not None:
        rock_s = rock_look(g, get_sample(ROCK_KEY), rock, cfg, world_pos, vnormal, s_side, breakup)
        alpha = height_blend_alpha(g, rock_mask, acc["height"], rock_s["height"],
                                   ctx["hb_depth"], ctx["blend_range"], -600, 10200)
        acc = lerp_pbr(g, acc, rock_s, alpha, 1550, 12000)
    else:
        warn("Rock set not loaded -> slopes and mountains keep ground")

    # --- 3c. manual retouch layers (before AO / macro / brightness) ------------------
    overrides = {}
    if rock_s is not None and cfg.paint_rock_uses_rock_look:
        overrides[ROCK_KEY] = rock_s
    acc = apply_paint_layers(g, acc, pbrs, get_sample, cfg, overrides)

    # --- 4. outputs -----------------------------------------------------------------------
    p_ao = g.scalar("OcclusionStrength", cfg.occlusion_strength, "5 Look", 1800, -600)
    ao_factor = g.node(Lerp, 2000, -600, const_a=1.0)
    g.link(acc["ao"][0], acc["ao"][1], ao_factor, "B")
    g.link(p_ao, "", ao_factor, "Alpha")
    color = g.node(Mul, 2200, -1200)
    g.link(acc["color"][0], acc["color"][1], color, "A")
    g.link(ao_factor, "", color, "B")

    p_macro = g.scalar("MacroStrength", cfg.macro_strength, "5 Look", 1800, -400)
    macro = g.node(Lerp, 2200, -400)
    g.link(g.unary(unreal.MaterialExpressionOneMinus, 2000, -400, p_macro), "", macro, "A")
    g.link(g.op(Add, 2000, -300, a=p_macro, const_b=1.0), "", macro, "B")
    g.link(breakup, "", macro, "Alpha")
    color = g.op(Mul, 2400, -1200, a=color, b=macro)
    color = g.op(Mul, 2600, -1200, a=color, b=g.scalar("Brightness", cfg.brightness, "5 Look", 2400, -200))
    g.output(color, "", unreal.MaterialProperty.MP_BASE_COLOR)

    if cfg.debug_flat_normal:
        warn("debug_flat_normal=True -> MP_NORMAL forced to (0,0,1). "
             "If the terrain is no longer black, the problem is in the normal chain.")
        g.output(g.flat_normal(2600, -1000), "", unreal.MaterialProperty.MP_NORMAL)
    else:
        g.output(acc["normal"][0], acc["normal"][1], unreal.MaterialProperty.MP_NORMAL)

    rough = g.node(Mul, 2400, -800)
    g.link(acc["rough"][0], acc["rough"][1], rough, "A")
    g.link(g.scalar("RoughnessMultiplier", cfg.roughness_multiplier, "5 Look", 2200, -800), "", rough, "B")
    g.output(g.unary(Sat, 2600, -800, rough), "", unreal.MaterialProperty.MP_ROUGHNESS)

    try:
        MEL.layout_material_expressions(mat)
    except Exception:
        pass
    MEL.recompile_material(mat)
    _log_statistics(mat)
    EAL.save_loaded_asset(mat)
    return mat, g


# =============================================================================
# LANDSCAPE
# =============================================================================

def find_landscape(cfg):
    actors = unreal.get_editor_subsystem(unreal.EditorActorSubsystem).get_all_level_actors()
    landscapes = [a for a in actors if isinstance(a, unreal.Landscape)]
    if cfg.landscape_label:
        landscapes = [a for a in landscapes if a.get_actor_label() == cfg.landscape_label]
    if not landscapes:
        raise RuntimeError("No Landscape actor in the level (import the heightmap first).")
    if len(landscapes) > 1:
        warn(f"{len(landscapes)} Landscapes found, using '{landscapes[0].get_actor_label()}' "
             f"(set landscape_label to choose)")
    proxies = [a for a in actors if isinstance(a, unreal.LandscapeStreamingProxy)]
    return landscapes[0], proxies


def _set_material(actor, mat):
    mode = getattr(unreal, "PropertyAccessChangeNotifyMode", None)
    if mode is not None:
        try:
            actor.set_editor_property("landscape_material", mat, notify_mode=mode.ALWAYS)
            return
        except TypeError:
            pass
    actor.set_editor_property("landscape_material", mat)


def assign_material(landscape, proxies, mat):
    _set_material(landscape, mat)
    log(f"Landscape Material set on '{landscape.get_actor_label()}' -> {mat.get_name()}")
    fixed = failed = 0
    for p in proxies:
        try:
            if p.get_editor_property("landscape_material") != mat:
                _set_material(p, mat)
                fixed += 1
        except Exception:
            failed += 1
    if proxies:
        log(f"Streaming proxies loaded: {len(proxies)} (updated: {fixed}, refused: {failed})")


# =============================================================================
# ENTRY POINTS
# =============================================================================

def _validate(cfg):
    _validate_stacks()
    for a, b in (("hill_slope_start_deg", "hill_slope_full_deg"),
                 ("rock_slope_start_deg", "rock_slope_full_deg"),
                 ("mountain_slope_start_deg", "mountain_slope_full_deg"),
                 ("low_rock_slope_start_deg", "low_rock_slope_full_deg"),
                 ("rock_side_start_deg", "rock_side_full_deg")):
        if getattr(cfg, b) <= getattr(cfg, a):
            raise ValueError(f"{b} must be greater than {a}")
    if cfg.height_blend_depth <= 0.0:
        raise ValueError("height_blend_depth must be > 0")
    if cfg.gltf_normal_flip not in ("auto", "always", "never"):
        raise ValueError("gltf_normal_flip must be auto, always or never")


def run(cfg: MaterialConfig = None):
    cfg = cfg or MaterialConfig()
    _validate(cfg)

    landscape, proxies = find_landscape(cfg)
    quad_cm = float(landscape.get_actor_scale3d().x) or 100.0
    log(f"Landscape '{landscape.get_actor_label()}', quad size {quad_cm:.0f} cm")

    pbrs = load_needed_sets(cfg)
    levels = estimate_levels(cfg, landscape, proxies)
    mat, g = build_material(cfg, quad_cm, levels, pbrs)
    log(f"Material built: {g.samples} texture samples, {g.noises} noise nodes")

    target = mat
    if cfg.use_instance:
        mi = get_or_create_instance(cfg.instance_path, mat, cfg.reset_instance_overrides)
        if mi is not None:
            target = mi
    assign_material(landscape, proxies, target)

    log("Done. Shaders compile for a few seconds. Tune live in MI_AutoTerrain "
        "(1 Levels, 2 Low zones, 3 Hill zones, 4 Zones common, 5 Look, 5 Sets, 6 Mud, 7 Rock, 8 Paint).")
    if cfg.paint_enabled:
        log("Paint: Landscape mode > Paint > Layers. Create a Layer Info for each "
            "Paint_* name (Non Weight-Blended), never use Fill Layer. "
            "If anything looks wrong, set PaintStrength to 0 in MI_AutoTerrain: "
            "that restores the v8 render without rebuilding.")
    if cfg.save_all:
        unreal.EditorLoadingAndSavingUtils.save_dirty_packages(True, True)
        log("Dirty packages saved.")
    else:
        log("Save all (Ctrl+Shift+S) so the World Partition proxies keep the material.")
    return target


def run_custom(**kw):
    return run(MaterialConfig(**kw))


def list_sets(**kw):
    cfg = MaterialConfig(**kw)
    _validate(cfg)
    pbrs = load_needed_sets(cfg)
    total = sum(p.sample_count() for p in pbrs.values())
    for stack in (LOW_STACK, HILL_STACK):
        if stack and stack[0].key in pbrs and cfg.far_tiling_multiplier > 1.0:
            total += 1
    if ROCK_KEY in pbrs:
        total += 2   # biplanar side projections
    for pbr in pbrs.values():
        for tex, label in ((pbr.orm, "ORM"), (pbr.mask, "mask")):
            if tex is None:
                continue
            try:
                if bool(tex.get_editor_property("srgb")):
                    warn(f"{pbr.key}: {label} texture has sRGB on -> roughness values are gamma-shifted")
            except Exception:
                pass
    for name, stack in (("Low", LOW_STACK), ("Hill", HILL_STACK)):
        log(f"{name}: " + ", ".join(f"{l.key} ({l.tier} {l.share:.0%})"
                                     + ("" if l.key in pbrs else " MISSING") for l in stack))
    log(f"Rock: {ROCK_KEY} {'ok' if ROCK_KEY in pbrs else 'MISSING'} | "
        f"Mud: {MUD_KEY} {'ok' if MUD_KEY in pbrs else 'MISSING'} | estimated texture samples: {total}")


def preview_levels(**kw):
    cfg = MaterialConfig(**kw)
    landscape, proxies = find_landscape(cfg)
    return estimate_levels(cfg, landscape, proxies)


if __name__ == "__main__":
    run()