"""Tests pour GET /api/search (moteur C + pagination offset/limit)."""


def create_post(client, headers, text):
    resp = client.post("/api/posts", headers=headers, data={"content": text})
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_search_finds_matching_post(client, register):
    headers, _ = register("alice")
    create_post(client, headers, "J'adore les chats")
    create_post(client, headers, "J'adore les chiens")
    body = client.get("/api/search?q=chats", headers=headers).json()
    assert len(body["results"]) == 1
    assert "chats" in body["results"][0]["content"]


def test_search_no_match_returns_empty(client, register):
    headers, _ = register("alice")
    create_post(client, headers, "un post quelconque")
    body = client.get("/api/search?q=motintrouvable", headers=headers).json()
    assert body == {"results": [], "has_more": False, "next_offset": None}


def test_search_ranks_by_occurrence_count(client, register):
    headers, _ = register("alice")
    create_post(client, headers, "chat chat chat")  # score 3
    create_post(client, headers, "chat")  # score 1
    body = client.get("/api/search?q=chat", headers=headers).json()
    scores = [r["score"] for r in body["results"]]
    assert scores == sorted(scores, reverse=True)
    assert scores[0] == 3


def test_search_is_case_insensitive(client, register):
    headers, _ = register("alice")
    create_post(client, headers, "CHAT en majuscules")
    body = client.get("/api/search?q=chat", headers=headers).json()
    assert len(body["results"]) == 1


def test_search_reflects_newly_created_posts(client, register):
    """Un post fraîchement créé doit être trouvable immédiatement (l'index
    C est mis à jour dans la même requête que l'insertion en base)."""
    headers, _ = register("alice")
    body_before = client.get("/api/search?q=licorne", headers=headers).json()
    assert body_before["results"] == []
    create_post(client, headers, "j'ai vu une licorne")
    body_after = client.get("/api/search?q=licorne", headers=headers).json()
    assert len(body_after["results"]) == 1


# ---------- Pagination ----------

def test_search_pagination_covers_all_matches_without_duplicates(client, register):
    headers, _ = register("alice")
    created_ids = [create_post(client, headers, f"chat numero {i}")["id"] for i in range(12)]

    seen_ids = []
    offset = 0
    for _ in range(10):
        body = client.get(f"/api/search?q=chat&limit=4&offset={offset}", headers=headers).json()
        seen_ids.extend(r["id"] for r in body["results"])
        if not body["has_more"]:
            assert body["next_offset"] is None
            break
        offset = body["next_offset"]
    else:
        raise AssertionError("pagination de la recherche : trop de pages")

    assert sorted(seen_ids) == sorted(created_ids)
    assert len(seen_ids) == len(set(seen_ids))


def test_search_limit_clamped_to_50(client, register):
    headers, _ = register("alice")
    create_post(client, headers, "un seul post avec chat")
    resp = client.get("/api/search?q=chat&limit=99999", headers=headers)
    assert resp.status_code == 200  # ne plante pas


def test_search_negative_offset_treated_as_zero(client, register):
    headers, _ = register("alice")
    create_post(client, headers, "un post avec chat")
    resp = client.get("/api/search?q=chat&offset=-50", headers=headers)
    assert resp.status_code == 200
    assert len(resp.json()["results"]) == 1


def test_search_offset_past_all_results_is_empty(client, register):
    headers, _ = register("alice")
    create_post(client, headers, "un post avec chat")
    body = client.get("/api/search?q=chat&offset=1000", headers=headers).json()
    assert body["results"] == []
    assert body["has_more"] is False
