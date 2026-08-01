import base64
import io
import os
import re

SUPPORTED_IMAGE_MIMES = frozenset({
    "image/png", "image/jpeg", "image/gif", "image/webp",
})

_IMAGE_MAGIC = [
    ("image/png",  [(0, b"\x89PNG\r\n\x1a\n")]),
    ("image/jpeg", [(0, b"\xff\xd8\xff")]),
    ("image/gif",  [(0, b"GIF8")]),
    ("image/webp", [(0, b"RIFF"), (8, b"WEBP")]),
    ("image/bmp",  [(0, b"BM")]),
]

MAX_MEDIA_ENCODED_BYTES = 28 * 1024 * 1024
MAX_MEDIA_DECODED_BYTES = 20 * 1024 * 1024
MAX_IMAGE_BASE64_BYTES = 5 * 1024 * 1024
MAX_IMAGE_WIDTH = 2000
MAX_IMAGE_HEIGHT = 2000

SAMPLE_BYTES = 4096


def sniff_mime(data: bytes) -> str | None:
    for mime, signatures in _IMAGE_MAGIC:
        ok = True
        for offset, magic in signatures:
            if data[offset:offset + len(magic)] != magic:
                ok = False
                break
        if ok:
            return mime
    return None


def is_supported_image(mime: str) -> bool:
    return mime in SUPPORTED_IMAGE_MIMES


def make_data_url(mime: str, data: bytes) -> str:
    b64 = base64.b64encode(data).decode("ascii")
    return f"data:{mime};base64,{b64}"


def parse_data_url(url: str) -> tuple[str, str]:
    m = re.match(r"^data:([^;]+);base64,(.*)$", url)
    if not m:
        raise ValueError("Invalid data URL")
    return m.group(1).lower(), m.group(2)


def validate_media(route: str, media_type: str, data: str | bytes, supported_mimes: set) -> dict:
    mime = media_type.lower()
    if mime not in supported_mimes:
        raise ValueError(f"{route} does not support media type {media_type}")

    if isinstance(data, bytes):
        raw_b64 = base64.b64encode(data).decode("ascii")
        decoded = data
    elif data.startswith("data:"):
        parsed_mime, raw_b64 = parse_data_url(data)
        if parsed_mime and parsed_mime != mime:
            raise ValueError(f"MIME mismatch: data URL says {parsed_mime}, expected {mime}")
        decoded = base64.b64decode(raw_b64, validate=True)
    else:
        raw_b64 = data
        try:
            decoded = base64.b64decode(raw_b64, validate=True)
        except Exception:
            raise ValueError(f"{route}: invalid base64 encoding")

    if len(raw_b64) > MAX_MEDIA_ENCODED_BYTES:
        raise ValueError(f"{route}: media too large ({len(raw_b64)} encoded bytes, max {MAX_MEDIA_ENCODED_BYTES})")
    if len(decoded) > MAX_MEDIA_DECODED_BYTES:
        raise ValueError(f"{route}: media too large ({len(decoded)} decoded bytes, max {MAX_MEDIA_DECODED_BYTES})")

    return {"mime": mime, "base64": raw_b64, "data_url": make_data_url(mime, decoded), "bytes": len(decoded)}


def normalize_image(image_data: bytes, mime: str) -> bytes:
    try:
        from PIL import Image
    except ImportError:
        return image_data

    try:
        img = Image.open(io.BytesIO(image_data))
    except Exception:
        return image_data

    w, h = img.size
    needs_resize = w > MAX_IMAGE_WIDTH or h > MAX_IMAGE_HEIGHT
    if needs_resize:
        ratio = min(MAX_IMAGE_WIDTH / w, MAX_IMAGE_HEIGHT / h)
        img = img.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)

    current_size = len(image_data)
    if not needs_resize and current_size <= MAX_IMAGE_BASE64_BYTES:
        return image_data

    fmt = mime.split("/")[1].upper()
    if fmt == "JPEG":
        fmt = "JPEG"
    elif fmt == "WEBP":
        fmt = "WEBP"
    else:
        fmt = "PNG"

    for quality in [85, 80, 70, 55, 40]:
        buf = io.BytesIO()
        img.save(buf, format=fmt, quality=quality)
        if len(buf.getvalue()) <= MAX_IMAGE_BASE64_BYTES:
            return buf.getvalue()

    buf = io.BytesIO()
    img.save(buf, format=fmt, quality=40)
    return buf.getvalue()


def read_image_file(filepath: str) -> dict | None:
    with open(filepath, "rb") as f:
        sample = f.read(SAMPLE_BYTES)
    mime = sniff_mime(sample)
    if not mime or not is_supported_image(mime):
        return None

    file_size = os.path.getsize(filepath)
    if file_size > MAX_MEDIA_DECODED_BYTES:
        return None

    with open(filepath, "rb") as f:
        full_data = f.read()

    full_data = normalize_image(full_data, mime)
    data_url = make_data_url(mime, full_data)

    return {
        "type": "file",
        "mime": mime,
        "url": data_url,
        "filename": os.path.basename(filepath),
    }
