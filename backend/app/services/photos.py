"""Report photo validation and privacy-preserving normalization.

Uploads are decoded with Pillow (JPEG/PNG/WebP, plus HEIC/HEIF from phone
galleries via pillow-heif), bounded in size and pixel
count, orientation-corrected and re-encoded as JPEG. Re-encoding drops EXIF and
other metadata, including any GPS location embedded by the phone camera.
"""

import asyncio
import io
from dataclasses import dataclass

from PIL import Image, ImageOps, UnidentifiedImageError
from pillow_heif import register_heif_opener

from app.domain.errors import DiscoveryError

register_heif_opener()

ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP", "HEIF"}
MAX_PIXELS = 40_000_000
MAX_EDGE = 2048
JPEG_QUALITY = 85


def invalid_photo(message: str) -> DiscoveryError:
    return DiscoveryError("INVALID_PHOTO", message, 422)


@dataclass(frozen=True)
class ProcessedPhoto:
    body: bytes
    content_type: str
    width: int
    height: int


def process_photo(data: bytes, max_bytes: int) -> ProcessedPhoto:
    if not data:
        raise invalid_photo("The photo is empty.")
    if len(data) > max_bytes:
        raise DiscoveryError("PHOTO_TOO_LARGE", f"Photos must be at most {max_bytes} bytes.", 413)
    try:
        with Image.open(io.BytesIO(data)) as probe:
            if probe.format not in ALLOWED_FORMATS:
                raise invalid_photo("Only JPEG, PNG, WebP and HEIC photos are accepted.")
            if probe.width * probe.height > MAX_PIXELS:
                raise invalid_photo("The photo has too many pixels.")
            probe.verify()
        with Image.open(io.BytesIO(data)) as image:
            image.load()
            image = ImageOps.exif_transpose(image)
            image = image.convert("RGB")
            image.thumbnail((MAX_EDGE, MAX_EDGE))
            output = io.BytesIO()
            # No exif= argument: metadata from the original file is not copied.
            image.save(output, format="JPEG", quality=JPEG_QUALITY, optimize=True)
            return ProcessedPhoto(output.getvalue(), "image/jpeg", image.width, image.height)
    except DiscoveryError:
        raise
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        raise invalid_photo("The photo could not be decoded.") from exc


# Bounded CPU work: decoding a large photo must not stall the event loop, and only a
# few decodes run at once per worker.
_DECODE_SLOTS = asyncio.Semaphore(2)


async def process_photo_async(data: bytes, max_bytes: int) -> ProcessedPhoto:
    async with _DECODE_SLOTS:
        return await asyncio.to_thread(process_photo, data, max_bytes)
