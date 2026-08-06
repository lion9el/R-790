"""Gestion sécurisée des uploads d'images."""
from __future__ import annotations

import io
import os
import uuid

from fastapi import HTTPException, UploadFile
from PIL import Image, UnidentifiedImageError

from config import settings

UPLOAD_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

ALLOWED_IMAGE_TYPES = {
    "image/png": (".png", "PNG"),
    "image/jpeg": (".jpg", "JPEG"),
    "image/gif": (".gif", "GIF"),
    "image/webp": (".webp", "WEBP"),
}
UPLOAD_CHUNK_SIZE = 64 * 1024


async def read_upload_with_limit(upload: UploadFile, max_size: int) -> bytes:
    chunks = []
    total = 0
    while True:
        chunk = await upload.read(UPLOAD_CHUNK_SIZE)
        if not chunk:
            break
        total += len(chunk)
        if total > max_size:
            raise HTTPException(status_code=400, detail="Image trop volumineuse (5 Mo maximum).")
        chunks.append(chunk)
    return b"".join(chunks)


async def save_uploaded_image(image: UploadFile) -> str:
    """Valide (MIME + magic bytes + dimensions) et enregistre une image.
    Retourne le nom de fichier généré (UUID).
    """
    if image.content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(
            status_code=400,
            detail="Format d'image non supporté (formats acceptés : PNG, JPEG, GIF, WEBP).",
        )
    extension, expected_format = ALLOWED_IMAGE_TYPES[image.content_type]

    data = await read_upload_with_limit(image, settings.max_image_size)
    if len(data) == 0:
        raise HTTPException(status_code=400, detail="Fichier image vide.")

    try:
        with Image.open(io.BytesIO(data)) as probe:
            probe.verify()
        with Image.open(io.BytesIO(data)) as reopened:
            actual_format = reopened.format
            width, height = reopened.size
    except (UnidentifiedImageError, OSError, ValueError):
        raise HTTPException(status_code=400, detail="Le fichier envoyé n'est pas une image valide.")

    if actual_format != expected_format:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Le contenu du fichier ({actual_format or 'format inconnu'}) ne correspond pas "
                f"au type déclaré ({expected_format})."
            ),
        )

    if width > settings.max_image_dimension or height > settings.max_image_dimension:
        raise HTTPException(
            status_code=400,
            detail=f"Image trop grande ({width}x{height}). Maximum {settings.max_image_dimension}px de côté.",
        )

    filename = uuid.uuid4().hex + extension
    filepath = os.path.join(UPLOAD_DIR, filename)
    with open(filepath, "wb") as f:
        f.write(data)
    return filename
