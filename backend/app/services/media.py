"""Safe image normalisation and tenant-scoped media storage."""

from __future__ import annotations

import hashlib
import io
import re
from pathlib import Path
from typing import BinaryIO

from PIL import Image, ImageOps, UnidentifiedImageError

from ..config import DATA_DIR

MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_IMAGE_DIMENSION = 4096
ALLOWED_IMAGE_FORMATS = {
    "JPEG": ("image/jpeg", "jpg"),
    "PNG": ("image/png", "png"),
    "WEBP": ("image/webp", "webp"),
}
ALLOWED_IMAGE_MIMES = {mime for mime, _extension in ALLOWED_IMAGE_FORMATS.values()}


class MediaValidationError(ValueError):
    """Raised when an uploaded image violates the media contract."""


def read_limited(stream: BinaryIO, limit: int = MAX_IMAGE_BYTES) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = stream.read(min(1024 * 1024, limit - total + 1))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
        if total > limit:
            raise MediaValidationError("图片不能超过 10MB")
    return b"".join(chunks)


def normalise_image(data: bytes) -> tuple[bytes, str, str, int, int]:
    """Decode and re-encode a supported image, dropping all EXIF metadata."""

    if not data:
        raise MediaValidationError("图片内容为空")
    if len(data) > MAX_IMAGE_BYTES:
        raise MediaValidationError("图片不能超过 10MB")
    try:
        with Image.open(io.BytesIO(data)) as opened:
            image_format = str(opened.format or "").upper()
            if image_format not in ALLOWED_IMAGE_FORMATS:
                raise MediaValidationError("只支持 JPEG、PNG 或 WebP 图片")
            width, height = opened.size
            if (
                width < 1
                or height < 1
                or max(width, height) > MAX_IMAGE_DIMENSION
                or width * height > MAX_IMAGE_DIMENSION * MAX_IMAGE_DIMENSION
            ):
                raise MediaValidationError("图片宽高不能超过 4096 像素")
            if int(getattr(opened, "n_frames", 1) or 1) != 1:
                raise MediaValidationError("人物图片不能是动画")
            # Reject dangerous dimensions before decoding compressed pixels.
            opened.load()
            image = ImageOps.exif_transpose(opened)
            image.load()
            output = io.BytesIO()
            if image_format == "JPEG":
                if image.mode not in {"RGB", "L"}:
                    image = image.convert("RGB")
                image.save(output, format="JPEG", quality=90, optimize=True, exif=b"")
            elif image_format == "PNG":
                image.save(output, format="PNG", optimize=True)
            else:
                if image.mode not in {"RGB", "RGBA", "L"}:
                    image = image.convert("RGBA")
                image.save(output, format="WEBP", quality=90, method=6, exif=b"")
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise MediaValidationError("图片文件损坏或无法解码") from exc
    encoded = output.getvalue()
    if len(encoded) > MAX_IMAGE_BYTES:
        raise MediaValidationError("重新编码后的图片不能超过 10MB")
    mime_type, extension = ALLOWED_IMAGE_FORMATS[image_format]
    return encoded, mime_type, extension, width, height


def validate_declared_mime(declared_mime: str | None, detected_mime: str) -> None:
    """Reject forged or ambiguous upload metadata after content sniffing.

    Decoding remains the source of truth for the actual format, while the
    multipart declaration must independently name the same supported image
    type.  This prevents valid image bytes disguised as another media type
    (and the inverse) from bypassing upload policy.
    """

    declared = (declared_mime or "").split(";", 1)[0].strip().lower()
    if declared not in ALLOWED_IMAGE_MIMES:
        raise MediaValidationError("上传 MIME 必须是 image/jpeg、image/png 或 image/webp")
    if declared != detected_mime:
        raise MediaValidationError("图片内容与声明的 MIME 类型不一致")


def safe_original_name(name: str | None) -> str:
    value = Path(name or "upload").name
    value = re.sub(r"[\x00-\x1f\x7f]", "", value).strip() or "upload"
    return value[:255]


def storage_key(owner_id: str, project_id: str, asset_id: str, extension: str) -> str:
    # IDs are generated UUIDs, but reject any future caller-provided path-like
    # value before it reaches the filesystem.
    for value in (owner_id, project_id, asset_id):
        if Path(value).name != value or value in {"", ".", ".."}:
            raise MediaValidationError("媒体标识不合法")
    return f"uploads/{owner_id}/{project_id}/assets/{asset_id}.{extension}"


def absolute_path(key: str) -> Path:
    root = DATA_DIR.resolve()
    candidate = (DATA_DIR / key).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise MediaValidationError("媒体存储路径不合法") from exc
    return candidate


def write_asset(key: str, data: bytes) -> Path:
    path = absolute_path(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def checksum(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
