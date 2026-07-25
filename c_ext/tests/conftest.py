"""Permet `import searchengine` depuis les tests même si le .so compilé
est resté dans c_ext/ (à côté de ce dossier tests/), et vide l'index avant
CHAQUE test : searchengine garde son état dans des variables globales C,
partagées par tous les tests du process, donc sans ce nettoyage un test
verrait les documents indexés par le test précédent."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

try:
    import searchengine
except ImportError as exc:
    raise RuntimeError(
        "Le module 'searchengine' est introuvable. Compile-le d'abord avec : "
        "cd c_ext && python3 setup.py build_ext --inplace"
    ) from exc


@pytest.fixture(autouse=True)
def clean_index():
    searchengine.clear()
    yield
    searchengine.clear()
