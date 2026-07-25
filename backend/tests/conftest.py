"""Fixtures partagées par tous les tests du backend.

Principe d'isolation : chaque test obtient sa PROPRE base SQLite (fichier
temporaire, jamais le vrai `backend/socialnet.db`) et son PROPRE index de
recherche C vidé au démarrage. Sans ça, les tests se pollueraient les uns
les autres (ex: un `testuser` créé dans un test entrerait en collision avec
le même nom dans un autre test, ou une recherche verrait les posts d'un
test précédent).
"""
import os
import sys

# Permet `import app`, `import database`, etc. depuis les tests, comme si on
# lançait pytest depuis le dossier backend/ (voir pytest.ini : rootdir).
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from fastapi.testclient import TestClient

import database
import ratelimit


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Client de test FastAPI, avec une base SQLite fraîche pour ce test.

    - `monkeypatch.setattr(database, "DB_PATH", ...)` redirige toutes les
      connexions (`get_conn()` lit `database.DB_PATH` à chaque appel, donc ce
      patch est bien pris en compte) vers un fichier temporaire propre à ce
      test, automatiquement supprimé par pytest après le test.
    - `TestClient(app)` utilisé en context manager (`with`) déclenche les
      événements `startup`/`shutdown` de FastAPI, donc `on_startup()` dans
      app.py s'exécute : la base est créée (`init_db`) et l'index de
      recherche C est vidé puis reconstruit à partir de cette base neuve
      (donc vide) — chaque test démarre avec un index de recherche vierge.
    - L'import de `app` est fait ICI (dans la fixture, après le monkeypatch)
      et non en haut du fichier : `app` importe `database` et utilise
      `database.get_conn`, mais comme Python met les modules en cache après
      le premier import, un import unique en haut de fichier serait
      réutilisé tel quel par tous les tests. En important après le patch et
      en s'appuyant sur le fait que get_conn relit DB_PATH à chaque appel
      (pas de valeur figée à l'import), chaque test voit bien sa propre base.
    - `ratelimit.reset()` vide les compteurs de tentatives (voir
      ratelimit.py) : comme searchengine, ce module garde son état dans une
      variable globale du process, partagée par tous les tests. Sans ce
      reset, les appels à /api/register et /api/login des tests précédents
      s'accumuleraient et finiraient par déclencher des 429 dans des tests
      qui n'ont rien à voir avec le rate limiting.
    """
    db_path = tmp_path / "test_socialnet.db"
    monkeypatch.setattr(database, "DB_PATH", str(db_path))
    ratelimit.reset()

    # Import différé : évite qu'un précédent test ait déjà importé `app`
    # avec un état de module différent (le module reste le même objet d'un
    # test à l'autre de toute façon, seul `database.DB_PATH` change, mais
    # réimporter ici documente clairement la dépendance à l'ordre du patch).
    import app as app_module

    # Isole aussi les fichiers uploadés (voir test_image_upload.py) dans un
    # dossier temporaire propre à ce test, plutôt que le vrai
    # backend/uploads/ : sans ça, chaque exécution de la suite de tests
    # laisserait de vrais fichiers image sur disque, jamais nettoyés.
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    monkeypatch.setattr(app_module, "UPLOAD_DIR", str(upload_dir))

    with TestClient(app_module.app) as c:
        yield c


@pytest.fixture
def register(client):
    """Raccourci : crée un compte et renvoie (headers_auth, username).

    Évite de répéter le POST /api/register + construction du header
    Authorization dans chaque test qui a juste besoin d'un utilisateur
    connecté sans se soucier des détails de l'inscription.
    """
    def _register(username="alice", password="password123"):
        resp = client.post("/api/register", json={"username": username, "password": password})
        assert resp.status_code == 200, resp.text
        token = resp.json()["token"]
        return {"Authorization": f"Bearer {token}"}, username

    return _register
