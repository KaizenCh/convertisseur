# -*- coding: utf-8 -*-
"""
======================================================================
UE5.5 -> GODOT ASSET PAYLOAD EXPORTER
======================================================================

Consumes:
    C:/Export/level_manifest_v10.json

Exports the unique StaticMesh assets referenced by the manifest as GLB
using Unreal's GLTFExporter when that plugin/API is available.

Why GLB:
    - Godot recommends glTF 2.0 for 3D scene interchange.
    - GLB keeps mesh + materials + textures together.
    - The manifest remains the placement source of truth.

Outputs:
    C:/Export/GodotAssets/Meshes/*.glb
    C:/Export/GodotAssets/ue5_godot_asset_map.json
    C:/Export/GodotAssets/ue5_godot_asset_map.txt

This script does NOT modify scene actors or assets.
"""

import unreal
import json
import os
import re
import traceback
import time
import hashlib


MANIFEST_PATH = r"C:/Export/level_manifest_v10.json"
OUTPUT_ROOT = r"C:/Export/GodotAssets"
MESH_OUTPUT_DIR = os.path.join(OUTPUT_ROOT, "Meshes")
MAP_OUTPUT_PATH = os.path.join(OUTPUT_ROOT, "ue5_godot_asset_map.json")
TXT_OUTPUT_PATH = os.path.join(OUTPUT_ROOT, "ue5_godot_asset_map.txt")

# The Godot project is expected to receive this folder under:
#     res://UEAssets/
GODOT_ROOT = "res://UEAssets"
GODOT_MESH_ROOT = GODOT_ROOT + "/Meshes"



def log(message):
    print("[UE5->Godot Assets] " + str(message))



def safe_load(path):
    try:
        return unreal.load_asset(path)
    except Exception:
        try:
            return unreal.EditorAssetLibrary.load_asset(path)
        except Exception:
            return None



def sanitize_filename(value):
    value = str(value or "Asset")
    value = re.sub(r"[^A-Za-z0-9_.-]+", "_", value)
    value = value.strip("._")
    return value or "Asset"



def object_name_from_path(path):
    if not path:
        return "Asset"
    value = str(path)
    if "." in value:
        value = value.rsplit(".", 1)[1]
    if "/" in value:
        value = value.rsplit("/", 1)[1]
    return value



def unique_filename_for_path(ue_path, asset_name):
    """
    V10.1 fix: object_name_from_path() only keeps the short asset name,
    so two different assets sharing a basename in different folders
    (e.g. /Game/Env/Rocks/SM_Rock01 and /Game/Props/Misc/SM_Rock01)
    previously collided onto the exact same "SM_Rock01.glb" file. The
    second one would then be silently marked "EXISTING" (a false
    success) while actually pointing at the wrong mesh.

    Appending a short hash of the FULL Unreal object path guarantees a
    unique filename per distinct asset, while keeping the name readable.
    """
    digest = hashlib.sha1(str(ue_path).encode("utf-8")).hexdigest()[:8]
    return sanitize_filename(asset_name) + "_" + digest + ".glb"



def export_glb(asset, output_path):
    exporter_cls = getattr(unreal, "GLTFExporter", None)

    if exporter_cls is None:
        return False, "GLTFExporter Python API is unavailable. Enable the GLTFExporter plugin."

    try:
        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        options = None
        options_cls = getattr(unreal, "GLTFExportOptions", None)
        if options_cls is not None:
            try:
                options = options_cls()
            except Exception:
                options = None

        result = exporter_cls.export_to_gltf(
            asset,
            output_path,
            options,
            set()
        )

        if result is None:
            return False, "GLTFExporter returned None."

        # V10.1: previously only checked os.path.isfile(), which treats a
        # 0-byte or truncated/corrupt file as a success. Now also checks
        # a minimum size and the actual glTF-binary magic header, and
        # surfaces any error/warning messages the exporter returned
        # (when the API exposes them) instead of discarding them.
        if not os.path.isfile(output_path):
            return False, "Exporter returned without creating the GLB file."

        file_size = os.path.getsize(output_path)

        if file_size < 20:
            return False, "GLB file is suspiciously small (%d bytes) - likely corrupt or empty export." % file_size

        try:
            with open(output_path, "rb") as glb_file:
                magic = glb_file.read(4)
            if magic != b"glTF":
                return False, "Output file does not have a valid glTF-binary header (got %r)." % magic
        except Exception as read_exc:
            return False, "Could not validate exported GLB file: " + str(read_exc)

        # Best-effort: surface exporter messages if the returned object
        # exposes any (API varies by engine version, so this is defensive).
        exporter_messages = []
        for attr_name in ("get_error_messages", "get_messages", "messages"):
            getter = getattr(result, attr_name, None)
            if getter is None:
                continue
            try:
                collected = getter() if callable(getter) else getter
                if collected:
                    exporter_messages.extend(str(m) for m in collected)
            except Exception:
                pass

        if exporter_messages:
            return True, "Exported with warnings: " + " | ".join(exporter_messages)

        return True, ""

    except Exception as exc:
        return False, str(exc)



def main():
    start = time.time()

    log("============================================================")
    log("UE5.5 -> GODOT ASSET PAYLOAD EXPORT")
    log("============================================================")

    if not os.path.isfile(MANIFEST_PATH):
        log("ERROR: V10 manifest not found: " + MANIFEST_PATH)
        return

    try:
        with open(MANIFEST_PATH, "r", encoding="utf-8") as file:
            manifest = json.load(file)
    except Exception:
        traceback.print_exc()
        return

    version = str(manifest.get("manifest_version", ""))
    if version != "10.0":
        log("WARNING: expected manifest 10.0, got " + version)

    unique_meshes = (
        manifest
        .get("geometry", {})
        .get("unique_meshes", {})
    )

    if not unique_meshes:
        log("ERROR: no unique meshes found in manifest.")
        return

    os.makedirs(MESH_OUTPUT_DIR, exist_ok=True)

    mapping = {}
    failures = []
    exported = 0
    skipped_existing = 0

    for mesh_path, mesh_info in unique_meshes.items():
        if not mesh_path:
            continue

        asset = safe_load(mesh_path)
        if asset is None:
            failures.append({
                "ue_path": mesh_path,
                "reason": "Asset could not be loaded."
            })
            continue

        asset_name = object_name_from_path(mesh_path)
        filename = unique_filename_for_path(mesh_path, asset_name)
        disk_path = os.path.join(MESH_OUTPUT_DIR, filename)
        godot_path = GODOT_MESH_ROOT + "/" + filename

        if os.path.isfile(disk_path) and os.path.getsize(disk_path) > 0:
            skipped_existing += 1
            mapping[mesh_path] = {
                "ue_path": mesh_path,
                "ue_name": asset_name,
                "godot_path": godot_path,
                "disk_path": disk_path.replace("\\", "/"),
                "format": "glb",
                "status": "EXISTING"
            }
            continue

        ok, reason = export_glb(asset, disk_path)

        if ok:
            exported += 1
            mapping[mesh_path] = {
                "ue_path": mesh_path,
                "ue_name": asset_name,
                "godot_path": godot_path,
                "disk_path": disk_path.replace("\\", "/"),
                "format": "glb",
                "status": "EXPORTED"
            }
            if reason:
                # export_glb returns a non-empty reason on success only
                # when it collected exporter warnings - keep them visible
                # instead of discarding them silently.
                mapping[mesh_path]["export_warnings"] = reason
                log("OK  " + asset_name + "  (with warnings: " + reason + ")")
            else:
                log("OK  " + asset_name)
        else:
            failures.append({
                "ue_path": mesh_path,
                "ue_name": asset_name,
                "disk_path": disk_path.replace("\\", "/"),
                "reason": reason
            })
            log("FAIL " + asset_name + " :: " + reason)

    payload = {
        "exporter_version": "1.0",
        "manifest_version": version,
        "format": "glb",
        "godot_asset_root": GODOT_ROOT,
        "godot_mesh_root": GODOT_MESH_ROOT,
        "source_manifest": MANIFEST_PATH,
        "unique_mesh_count": len(unique_meshes),
        "exported_count": exported,
        "existing_count": skipped_existing,
        "failure_count": len(failures),
        "assets": mapping,
        "failures": failures,
        "notes": [
            "Copy C:/Export/GodotAssets into the Godot project as res://UEAssets/.",
            "The V10 manifest remains responsible for placement and transform data.",
            "GLB is self-contained where Unreal's GLTF exporter includes the referenced payload.",
            "Complex UE material graphs are not guaranteed to reproduce 1:1 in Godot."
        ]
    }

    try:
        with open(MAP_OUTPUT_PATH, "w", encoding="utf-8") as file:
            json.dump(payload, file, indent=2, ensure_ascii=False)
    except Exception:
        traceback.print_exc()
        return

    try:
        with open(TXT_OUTPUT_PATH, "w", encoding="utf-8") as file:
            file.write("UE5.5 -> GODOT ASSET PAYLOAD EXPORT\n")
            file.write("=================================\n\n")
            file.write("Manifest: " + MANIFEST_PATH + "\n")
            file.write("Unique meshes: " + str(len(unique_meshes)) + "\n")
            file.write("Exported: " + str(exported) + "\n")
            file.write("Already present: " + str(skipped_existing) + "\n")
            file.write("Failures: " + str(len(failures)) + "\n\n")

            if failures:
                file.write("FAILURES\n")
                for item in failures:
                    file.write("- " + str(item.get("ue_path")) + "\n")
                    file.write("  " + str(item.get("reason")) + "\n")

            file.write("\nCOPY\n")
            file.write("Copy: C:/Export/GodotAssets\n")
            file.write("Into: <GodotProject>/UEAssets\n")
    except Exception:
        traceback.print_exc()

    log("")
    log("============================================================")
    log("DONE")
    log("Unique meshes       : " + str(len(unique_meshes)))
    log("Exported            : " + str(exported))
    log("Already present     : " + str(skipped_existing))
    log("Failures            : " + str(len(failures)))
    log("Map                 : " + MAP_OUTPUT_PATH)
    log("Time                : {:.3f}s".format(time.time() - start))
    log("============================================================")


main()
