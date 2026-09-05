from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO

from PIL import Image, ImageOps, UnidentifiedImageError


SUPPORTED_FORMATS = {"JPEG", "PNG", "WEBP"}


class InvalidImageError(ValueError):
    pass


@dataclass(frozen=True)
class PreparedImage:
    jpeg_bytes: bytes
    original_width: int
    original_height: int
    processed_width: int
    processed_height: int


def prepare_image(
    data: bytes, *, max_upload_bytes: int, max_dimension: int
) -> PreparedImage:
    if not data:
        raise InvalidImageError("The uploaded image is empty.")
    if len(data) > max_upload_bytes:
        raise InvalidImageError(
            f"Image exceeds the {max_upload_bytes // (1024 * 1024)} MB limit."
        )

    try:
        with Image.open(BytesIO(data)) as source:
            if source.format not in SUPPORTED_FORMATS:
                raise InvalidImageError("Only JPEG, PNG, and WebP images are supported.")
            source.verify()

        with Image.open(BytesIO(data)) as source:
            source = ImageOps.exif_transpose(source)
            original_width, original_height = source.size
            source = source.convert("RGB")
            source.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)
            processed_width, processed_height = source.size
            output = BytesIO()
            source.save(output, format="JPEG", quality=85, optimize=True)
    except InvalidImageError:
        raise
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise InvalidImageError("The uploaded file is not a valid image.") from exc

    return PreparedImage(
        jpeg_bytes=output.getvalue(),
        original_width=original_width,
        original_height=original_height,
        processed_width=processed_width,
        processed_height=processed_height,
    )

