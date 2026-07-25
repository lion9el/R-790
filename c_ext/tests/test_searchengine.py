"""Tests unitaires de l'extension C `searchengine` (c_ext/searchmodule.c),
indépendants du backend : on appelle directement index_add/search/index_remove/clear.
"""
import searchengine


def test_index_and_search_basic():
    searchengine.index_add(1, "le chat dort")
    results = searchengine.search("chat")
    assert results == [(1, 1)]


def test_search_unmatched_word_returns_empty():
    searchengine.index_add(1, "le chat dort")
    assert searchengine.search("girafe") == []


def test_search_score_counts_occurrences_in_same_doc():
    searchengine.index_add(1, "chat chat chat")
    assert searchengine.search("chat") == [(1, 3)]


def test_search_is_case_insensitive():
    searchengine.index_add(1, "Chat CHAT chAt")
    assert searchengine.search("chat") == [(1, 3)]


def test_search_sorts_by_score_descending():
    searchengine.index_add(1, "chat")
    searchengine.index_add(2, "chat chat chat")
    searchengine.index_add(3, "chat chat")
    results = searchengine.search("chat")
    assert [doc_id for doc_id, _score in results] == [2, 3, 1]


def test_search_multi_word_query_sums_scores_per_doc():
    searchengine.index_add(1, "chat chien chien")  # chat:1 + chien:2 = 3
    searchengine.index_add(2, "chien")  # chien:1
    results = dict(searchengine.search("chat chien"))
    assert results[1] == 3
    assert results[2] == 1


def test_search_respects_limit():
    for doc_id in range(10):
        searchengine.index_add(doc_id, "chat")
    results = searchengine.search("chat", 3)
    assert len(results) == 3


def test_search_default_limit_is_20():
    for doc_id in range(25):
        searchengine.index_add(doc_id, "chat")
    results = searchengine.search("chat")
    assert len(results) == 20


def test_punctuation_treated_as_word_separator():
    searchengine.index_add(1, "chat, chien; souris.")
    assert searchengine.search("chat") == [(1, 1)]
    assert searchengine.search("chien") == [(1, 1)]
    assert searchengine.search("souris") == [(1, 1)]


def test_empty_query_returns_no_results():
    searchengine.index_add(1, "chat")
    assert searchengine.search("") == []


def test_indexing_same_doc_twice_accumulates_frequency():
    """Reflète l'usage réel : réindexer un post existant (ex: après une
    modification) ajoute aux fréquences plutôt que de les remplacer — c'est
    pour ça qu'app.py appelle toujours `clear()` puis réindexe tout au
    démarrage, plutôt que de compter sur index_add pour écraser l'ancien état."""
    searchengine.index_add(1, "chat")
    searchengine.index_add(1, "chat")
    assert searchengine.search("chat") == [(1, 2)]


def test_index_remove_removes_doc_from_results():
    searchengine.index_add(1, "chat")
    searchengine.index_add(2, "chat")
    searchengine.index_remove(1, "chat")
    assert searchengine.search("chat") == [(2, 1)]


def test_index_remove_only_affects_given_doc():
    searchengine.index_add(1, "chat")
    searchengine.index_remove(2, "chat")  # doc 2 n'a jamais été indexé avec "chat"
    assert searchengine.search("chat") == [(1, 1)]


def test_index_remove_nonexistent_word_does_not_crash():
    searchengine.index_add(1, "chat")
    searchengine.index_remove(1, "mot-jamais-indexe")
    assert searchengine.search("chat") == [(1, 1)]  # inchangé


def test_clear_empties_the_whole_index():
    searchengine.index_add(1, "chat")
    searchengine.index_add(2, "chien")
    searchengine.clear()
    assert searchengine.search("chat") == []
    assert searchengine.search("chien") == []


def test_numbers_are_indexed_as_words():
    searchengine.index_add(1, "modele 2026 disponible")
    assert searchengine.search("2026") == [(1, 1)]
