# -*- coding: utf-8 -*-
"""Standalone pure-Python binary glTF (GLB) writer."""

import json
import struct
from typing import List, Tuple, Optional, Dict, Any


def build_grid_mesh(
    grid: List[List[Optional[float]]],
    bounds: Tuple[float, float, float, float],
    scale: float = 0.01
) -> Tuple[List[float], List[float], List[float], List[int]]:
    """Builds grid mesh (positions, normals, uvs, indices) with inverted winding for det=-1."""
    min_x, min_y, max_x, max_y = bounds
    res_y = len(grid) - 1
    res_x = len(grid[0]) - 1

    dx = (max_x - min_x) / float(res_x)
    dy = (max_y - min_y) / float(res_y)

    vertices: List[Tuple[float, float, float]] = []
    uvs: List[Tuple[float, float]] = []
    index_grid: List[List[int]] = [[-1] * (res_x + 1) for _ in range(res_y + 1)]

    for iy in range(res_y + 1):
        for ix in range(res_x + 1):
            h = grid[iy][ix]
            if h is None:
                continue
            ue_x = min_x + ix * dx
            ue_y = min_y + iy * dy
            ue_z = h

            # Godot = (ue.x, ue.z, ue.y) * 0.01
            gx = ue_x * scale
            gy = ue_z * scale
            gz = ue_y * scale

            u = ix / float(res_x)
            v = iy / float(res_y)

            idx = len(vertices)
            vertices.append((gx, gy, gz))
            uvs.append((u, v))
            index_grid[iy][ix] = idx

    indices: List[int] = []
    for iy in range(res_y):
        for ix in range(res_x):
            i0 = index_grid[iy][ix]
            i1 = index_grid[iy][ix + 1]
            i2 = index_grid[iy + 1][ix]
            i3 = index_grid[iy + 1][ix + 1]

            # Only emit quads if all 4 corners exist
            if i0 >= 0 and i1 >= 0 and i2 >= 0 and i3 >= 0:
                # Winding inverted for det=-1: (i0, i2, i1) and (i1, i2, i3)
                indices.extend([i0, i2, i1])
                indices.extend([i1, i2, i3])

    # Compute flat normals
    normals: List[List[float]] = [[0.0, 1.0, 0.0] for _ in range(len(vertices))]
    counts = [0] * len(vertices)

    for i in range(0, len(indices), 3):
        idx0, idx1, idx2 = indices[i], indices[i + 1], indices[i + 2]
        p0, p1, p2 = vertices[idx0], vertices[idx1], vertices[idx2]

        v1 = (p1[0] - p0[0], p1[1] - p0[1], p1[2] - p0[2])
        v2 = (p2[0] - p0[0], p2[1] - p0[1], p2[2] - p0[2])

        nx = v1[1] * v2[2] - v1[2] * v2[1]
        ny = v1[2] * v2[0] - v1[0] * v2[2]
        nz = v1[0] * v2[1] - v1[1] * v2[0]

        length = (nx*nx + ny*ny + nz*nz) ** 0.5
        if length > 1e-6:
            nx, ny, nz = nx / length, ny / length, nz / length
            for idx in (idx0, idx1, idx2):
                normals[idx][0] += nx
                normals[idx][1] += ny
                normals[idx][2] += nz
                counts[idx] += 1

    for i in range(len(vertices)):
        if counts[i] > 0:
            nx, ny, nz = normals[i][0], normals[i][1], normals[i][2]
            length = (nx*nx + ny*ny + nz*nz) ** 0.5
            if length > 1e-6:
                normals[i] = [nx / length, ny / length, nz / length]

    flat_pos = [c for v in vertices for c in v]
    flat_norm = [c for n in normals for c in n]
    flat_uv = [c for u in uvs for c in u]

    return flat_pos, flat_norm, flat_uv, indices


def write_glb(
    output_path: str,
    positions: List[float],
    normals: List[float],
    uvs: List[float],
    indices: List[int],
    png_bytes: Optional[bytes] = None
) -> bool:
    """Writes a GLB v2 file with positions, normals, uvs, indices, and optional embedded PNG."""
    pos_bytes = bytearray()
    min_pos = [float("inf")] * 3
    max_pos = [float("-inf")] * 3

    for i in range(0, len(positions), 3):
        x, y, z = positions[i], positions[i+1], positions[i+2]
        pos_bytes.extend(struct.pack("<fff", x, y, z))
        min_pos[0] = min(min_pos[0], x)
        min_pos[1] = min(min_pos[1], y)
        min_pos[2] = min(min_pos[2], z)
        max_pos[0] = max(max_pos[0], x)
        max_pos[1] = max(max_pos[1], y)
        max_pos[2] = max(max_pos[2], z)

    norm_bytes = bytearray()
    for i in range(0, len(normals), 3):
        norm_bytes.extend(struct.pack("<fff", normals[i], normals[i+1], normals[i+2]))

    uv_bytes = bytearray()
    for i in range(0, len(uvs), 2):
        uv_bytes.extend(struct.pack("<ff", uvs[i], uvs[i+1]))

    idx_bytes = bytearray()
    max_idx = max(indices) if indices else 0
    use_uint32 = max_idx > 65535

    for idx in indices:
        if use_uint32:
            idx_bytes.extend(struct.pack("<I", idx))
        else:
            idx_bytes.extend(struct.pack("<H", idx))

    bin_buffer = bytearray()

    def add_buffer_view(data: bytes, target: Optional[int] = None) -> Tuple[int, int, int]:
        offset = len(bin_buffer)
        length = len(data)
        bin_buffer.extend(data)
        # Pad to 4 bytes boundary
        padding = (4 - (len(bin_buffer) % 4)) % 4
        bin_buffer.extend(b"\x00" * padding)
        return offset, length, target or 0

    pos_offset, pos_len, _ = add_buffer_view(bytes(pos_bytes), 34962)
    norm_offset, norm_len, _ = add_buffer_view(bytes(norm_bytes), 34962)
    uv_offset, uv_len, _ = add_buffer_view(bytes(uv_bytes), 34962)
    idx_offset, idx_len, _ = add_buffer_view(bytes(idx_bytes), 34963)

    img_offset, img_len = 0, 0
    if png_bytes:
        img_offset, img_len, _ = add_buffer_view(png_bytes)

    buffer_views = [
        {"buffer": 0, "byteOffset": pos_offset, "byteLength": pos_len, "target": 34962},
        {"buffer": 0, "byteOffset": norm_offset, "byteLength": norm_len, "target": 34962},
        {"buffer": 0, "byteOffset": uv_offset, "byteLength": uv_len, "target": 34962},
        {"buffer": 0, "byteOffset": idx_offset, "byteLength": idx_len, "target": 34963},
    ]

    accessors = [
        {"bufferView": 0, "byteOffset": 0, "componentType": 5126, "count": len(positions) // 3, "type": "VEC3", "min": min_pos, "max": max_pos},
        {"bufferView": 1, "byteOffset": 0, "componentType": 5126, "count": len(normals) // 3, "type": "VEC3"},
        {"bufferView": 2, "byteOffset": 0, "componentType": 5126, "count": len(uvs) // 2, "type": "VEC2"},
        {"bufferView": 3, "byteOffset": 0, "componentType": 5125 if use_uint32 else 5123, "count": len(indices), "type": "SCALAR"},
    ]

    gltf: Dict[str, Any] = {
        "asset": {"version": "2.0", "generator": "ue2godot_glb_writer"},
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0}],
        "meshes": [{
            "primitives": [{
                "attributes": {"POSITION": 0, "NORMAL": 1, "TEXCOORD_0": 2},
                "indices": 3,
                "material": 0
            }]
        }],
        "materials": [{
            "name": "LandscapeMaterial",
            "pbrMetallicRoughness": {
                "roughnessFactor": 0.9,
                "metallicFactor": 0.0
            }
        }],
        "bufferViews": buffer_views,
        "accessors": accessors,
        "buffers": [{"byteLength": len(bin_buffer)}]
    }

    if png_bytes:
        buffer_views.append({"buffer": 0, "byteOffset": img_offset, "byteLength": img_len})
        gltf["images"] = [{"bufferView": 4, "mimeType": "image/png"}]
        gltf["textures"] = [{"source": 0}]
        gltf["materials"][0]["pbrMetallicRoughness"]["baseColorTexture"] = {"index": 0}

    json_str = json.dumps(gltf, separators=(",", ":")).encode("utf-8")
    json_padding = (4 - (len(json_str) % 4)) % 4
    json_str += b" " * json_padding

    header = struct.pack("<4sII", b"glTF", 2, 12 + 8 + len(json_str) + 8 + len(bin_buffer))
    json_chunk = struct.pack("<II", len(json_str), 0x4E4F534A) + json_str
    bin_chunk = struct.pack("<II", len(bin_buffer), 0x00414942) + bytes(bin_buffer)

    with open(output_path, "wb") as f:
        f.write(header)
        f.write(json_chunk)
        f.write(bin_chunk)

    return True
