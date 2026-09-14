# -*- coding: utf-8 -*-
"""Minimal pure-Python PNG reader/writer (stdlib only: zlib, struct).

Scope: 8-bit non-interlaced PNG, colour types 0 (grey), 2 (RGB), 4 (grey+alpha)
and 6 (RGBA).
"""

import struct
import zlib

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

CHANNELS_FOR_COLOR_TYPE = {
    0: 1,   # greyscale
    2: 3,   # truecolour
    4: 2,   # greyscale + alpha
    6: 4,   # truecolour + alpha
}


def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa = abs(p - a)
    pb = abs(p - b)
    pc = abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def read_png(path: str):
    """Returns (width, height, channels, bytearray of raw samples)."""
    with open(path, "rb") as handle:
        data = handle.read()

    if data[:8] != PNG_SIGNATURE:
        raise ValueError("not a PNG file: %s" % path)

    pos = 8
    width = height = None
    bit_depth = color_type = None
    idat = bytearray()

    while pos + 8 <= len(data):
        length = struct.unpack(">I", data[pos:pos + 4])[0]
        ctype = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + length]
        pos += 12 + length

        if ctype == b"IHDR":
            (width, height, bit_depth, color_type,
             compression, filter_method, interlace) = struct.unpack(">IIBBBBB", body)

            if bit_depth != 8:
                raise ValueError("unsupported bit depth %d (only 8)" % bit_depth)
            if interlace != 0:
                raise ValueError("interlaced PNG is not supported")
            if color_type not in CHANNELS_FOR_COLOR_TYPE:
                raise ValueError("unsupported colour type %d" % color_type)

        elif ctype == b"IDAT":
            idat.extend(body)

        elif ctype == b"IEND":
            break

    if width is None:
        raise ValueError("PNG has no IHDR: %s" % path)

    channels = CHANNELS_FOR_COLOR_TYPE[color_type]
    raw = zlib.decompress(bytes(idat))

    stride = width * channels
    out = bytearray(height * stride)
    previous = bytearray(stride)
    pos = 0

    for row in range(height):
        filter_type = raw[pos]
        pos += 1
        line = bytearray(raw[pos:pos + stride])
        pos += stride

        if filter_type == 0:
            pass
        elif filter_type == 1:
            for i in range(channels, stride):
                line[i] = (line[i] + line[i - channels]) & 0xFF
        elif filter_type == 2:
            for i in range(stride):
                line[i] = (line[i] + previous[i]) & 0xFF
        elif filter_type == 3:
            for i in range(stride):
                left = line[i - channels] if i >= channels else 0
                line[i] = (line[i] + ((left + previous[i]) >> 1)) & 0xFF
        elif filter_type == 4:
            for i in range(stride):
                left = line[i - channels] if i >= channels else 0
                upper_left = previous[i - channels] if i >= channels else 0
                line[i] = (line[i] + _paeth(left, previous[i], upper_left)) & 0xFF
        else:
            raise ValueError("unknown PNG filter type %d" % filter_type)

        out[row * stride:(row + 1) * stride] = line
        previous = line

    return width, height, channels, out


def _chunk(ctype: bytes, body: bytes) -> bytes:
    return (struct.pack(">I", len(body))
            + ctype
            + body
            + struct.pack(">I", zlib.crc32(ctype + body) & 0xFFFFFFFF))


def write_rgba_png(path: str, width: int, height: int, pixels: bytes, compress_level: int = 6) -> bool:
    """Writes an 8-bit RGBA PNG."""
    expected = width * height * 4
    if len(pixels) != expected:
        raise ValueError("expected %d samples, got %d" % (expected, len(pixels)))

    stride = width * 4
    raw = bytearray()

    for row in range(height):
        raw.append(0)
        raw.extend(pixels[row * stride:(row + 1) * stride])

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)

    with open(path, "wb") as handle:
        handle.write(PNG_SIGNATURE)
        handle.write(_chunk(b"IHDR", ihdr))
        handle.write(_chunk(b"IDAT", zlib.compress(bytes(raw), compress_level)))
        handle.write(_chunk(b"IEND", b""))

    return True


def luminance_at(channels: int, samples: bytearray, index: int) -> int:
    base = index * channels
    if channels in (1, 2):
        return samples[base]
    r, g, b = samples[base], samples[base + 1], samples[base + 2]
    return int(0.299 * r + 0.587 * g + 0.114 * b)


def compose_tinted_rgba(mask_path: str, output_path: str, tint_rgb: list,
                        mask_from_alpha: bool = False, invert: bool = False):
    width, height, channels, samples = read_png(mask_path)

    r = max(0, min(255, int(round(tint_rgb[0] * 255.0))))
    g = max(0, min(255, int(round(tint_rgb[1] * 255.0))))
    b = max(0, min(255, int(round(tint_rgb[2] * 255.0))))

    count = width * height
    out = bytearray(count * 4)

    use_alpha = mask_from_alpha and channels in (2, 4)
    alpha_offset = 1 if channels == 2 else 3

    total = 0
    lowest = 255
    highest = 0

    for i in range(count):
        if use_alpha:
            coverage = samples[i * channels + alpha_offset]
        else:
            coverage = luminance_at(channels, samples, i)

        if invert:
            coverage = 255 - coverage

        j = i * 4
        out[j] = r
        out[j + 1] = g
        out[j + 2] = b
        out[j + 3] = coverage

        total += coverage
        if coverage < lowest:
            lowest = coverage
        if coverage > highest:
            highest = coverage

    write_rgba_png(output_path, width, height, out)
    return width, height, total / float(count), lowest, highest
