import io

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

MAX_IMAGE_BASE64_BYTES = 5 * 1024 * 1024
MAX_IMAGE_WIDTH = 2000
MAX_IMAGE_HEIGHT = 2000


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
