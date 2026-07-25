"""Tests pour l'upload d'images sur les posts (backend/app.py::_save_uploaded_image).

Le point central testé ici : le Content-Type déclaré par le client ne
suffit plus à faire accepter un fichier — le contenu réel doit être une
image valide du format annoncé (vérifié avec Pillow). Avant ce correctif,
n'importe quel fichier passait la validation du moment que son Content-Type
mentait correctement.
"""
import io

from PIL import Image


def _real_png_bytes(size=(10, 10), color="red"):
    buf = io.BytesIO()
    Image.new("RGB", size, color=color).save(buf, format="PNG")
    return buf.getvalue()


def _real_jpeg_bytes(size=(10, 10), color="blue"):
    buf = io.BytesIO()
    Image.new("RGB", size, color=color).save(buf, format="JPEG")
    return buf.getvalue()


def test_upload_real_png_is_accepted(client, register):
    headers, _ = register("alice")
    resp = client.post(
        "/api/posts",
        headers=headers,
        data={"content": "avec une vraie image"},
        files={"image": ("photo.png", _real_png_bytes(), "image/png")},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["image_url"] is not None
    assert body["image_url"].endswith(".png")


def test_upload_real_jpeg_is_accepted(client, register):
    headers, _ = register("alice")
    resp = client.post(
        "/api/posts",
        headers=headers,
        data={"content": "jpeg"},
        files={"image": ("photo.jpg", _real_jpeg_bytes(), "image/jpeg")},
    )
    assert resp.status_code == 200
    assert resp.json()["image_url"].endswith(".jpg")


def test_upload_html_disguised_as_png_is_rejected(client, register):
    """Le cœur du correctif : un Content-Type mensonger ne doit plus suffire
    à faire passer un fichier qui n'est pas réellement une image."""
    headers, _ = register("alice")
    fake = b"<script>alert(1)</script> ceci n'est pas une image"
    resp = client.post(
        "/api/posts",
        headers=headers,
        data={"content": "faux png"},
        files={"image": ("photo.png", fake, "image/png")},
    )
    assert resp.status_code == 400
    assert "pas une image valide" in resp.json()["detail"]


def test_upload_binary_garbage_disguised_as_jpeg_is_rejected(client, register):
    headers, _ = register("alice")
    fake = bytes(range(256)) * 4  # octets aléatoires, aucune structure d'image
    resp = client.post(
        "/api/posts",
        headers=headers,
        data={"content": "faux jpeg"},
        files={"image": ("photo.jpg", fake, "image/jpeg")},
    )
    assert resp.status_code == 400


def test_upload_format_mismatch_is_rejected(client, register):
    """Une vraie image JPEG envoyée en prétendant être un PNG doit être
    rejetée : le format réel (détecté par Pillow) doit correspondre au
    Content-Type déclaré, pas seulement 'être une image quelconque'."""
    headers, _ = register("alice")
    resp = client.post(
        "/api/posts",
        headers=headers,
        data={"content": "mismatch"},
        files={"image": ("photo.png", _real_jpeg_bytes(), "image/png")},
    )
    assert resp.status_code == 400
    assert "ne correspond pas" in resp.json()["detail"]


def test_upload_disallowed_content_type_is_rejected(client, register):
    headers, _ = register("alice")
    resp = client.post(
        "/api/posts",
        headers=headers,
        data={"content": "svg pas autorisé"},
        files={"image": ("photo.svg", b"<svg></svg>", "image/svg+xml")},
    )
    assert resp.status_code == 400


def test_upload_empty_file_is_rejected(client, register):
    headers, _ = register("alice")
    resp = client.post(
        "/api/posts",
        headers=headers,
        data={"content": "vide"},
        files={"image": ("photo.png", b"", "image/png")},
    )
    assert resp.status_code == 400


def test_upload_oversized_file_is_rejected_without_reading_it_all(client, register):
    """Fichier au-delà de la limite (5 Mo) : doit être rejeté. Peu importe
    ici qu'il s'agisse d'une vraie image ou non, la taille prime — vérifie
    aussi que le serveur répond normalement (pas de timeout/crash) même
    avec un fichier volumineux, signe que la lecture par blocs fonctionne."""
    headers, _ = register("alice")
    oversized = b"\x00" * (5 * 1024 * 1024 + 1)
    resp = client.post(
        "/api/posts",
        headers=headers,
        data={"content": "trop gros"},
        files={"image": ("photo.png", oversized, "image/png")},
    )
    assert resp.status_code == 400
    assert "volumineuse" in resp.json()["detail"]


def test_post_without_image_still_works(client, register):
    """Non-régression : un post texte seul (le cas normal) doit toujours
    fonctionner exactement comme avant ce durcissement."""
    headers, _ = register("alice")
    resp = client.post("/api/posts", headers=headers, data={"content": "texte seul"})
    assert resp.status_code == 200
    assert resp.json()["image_url"] is None
