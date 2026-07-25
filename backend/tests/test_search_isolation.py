"""L'extension C (`searchengine`) garde son index en mémoire dans des
variables globales du process — pas dans la base SQLite. Sans précaution,
un post indexé par un test resterait visible par recherche dans le test
suivant, même si ce post n'existe pas dans SA base à lui.

Ces deux tests vérifient que `on_startup()` (déclenché à l'ouverture du
`TestClient`, voir conftest.py) vide bien l'index à chaque test. L'ordre
d'exécution n'a pas d'importance : peu importe lequel tourne en premier, si
l'isolation est cassée, l'un des deux verra un résultat "fantôme" venant de
l'autre.
"""


def test_first_test_indexes_a_unique_word(client, register):
    headers, _ = register("alice")
    client.post("/api/posts", headers=headers, data={"content": "mot-clé-isolation-test-A"})
    body = client.get("/api/search?q=isolation", headers=headers).json()
    assert len(body["results"]) == 1


def test_second_test_does_not_see_first_tests_index(client, register):
    """Si l'index C n'était pas vidé entre les tests, une recherche sur le
    mot indexé par le test précédent renverrait un résultat ici alors que
    CE test n'a jamais créé un tel post dans SA base."""
    headers, _ = register("alice")
    body = client.get("/api/search?q=isolation", headers=headers).json()
    assert body["results"] == [], (
        "l'index de recherche C n'est pas réinitialisé entre les tests : "
        "fuite d'état entre test_first_test_indexes_a_unique_word et celui-ci"
    )
