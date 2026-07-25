"""Limitation de débit en mémoire, pour empêcher les tentatives de
connexion/inscription automatisées (brute-force sur les mots de passe,
création massive de comptes).

Implémentation volontairement simple — un dict en mémoire du process, pas
de dépendance externe comme Redis — proportionnée à la taille du projet
(un seul process `uvicorn`). Limites connues, acceptées pour ce projet :
- Ne protège pas contre un attaquant distribué sur de nombreuses IP.
- Se réinitialise à chaque redémarrage du serveur.
- Ne fonctionnerait pas correctement avec plusieurs workers/instances
  (chacun aurait son propre compteur, divisant la protection réelle par le
  nombre de workers).
Si le projet grandit au point de tourner derrière plusieurs instances,
remplacer `_attempts` par un compteur partagé (Redis `INCR` + `EXPIRE` est
le pattern classique) sans changer la forme de `rate_limit()`.
"""
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request

# clé -> horodatages (secondes, time.monotonic()) des tentatives récentes.
# `time.monotonic()` plutôt que `time.time()` : insensible aux changements
# d'horloge système (ntp, veille), ce qui compte pour une fenêtre glissante.
_attempts: dict[str, deque] = defaultdict(deque)


def rate_limit(max_attempts: int, window_seconds: int):
    """Fabrique une dépendance FastAPI limitant à `max_attempts` requêtes
    par `window_seconds` secondes et par adresse IP cliente, sur la route où
    elle est branchée (la clé inclut le chemin de la requête : la limite de
    `/api/login` est indépendante de celle de `/api/register`).

    Utilisation : `@app.post(..., dependencies=[Depends(rate_limit(5, 60))])`
    """

    def dependency(request: Request) -> None:
        client_ip = request.client.host if request.client else "unknown"
        key = f"{request.url.path}:{client_ip}"
        now = time.monotonic()
        window = _attempts[key]

        # Purge les tentatives sorties de la fenêtre glissante.
        while window and now - window[0] > window_seconds:
            window.popleft()

        if len(window) >= max_attempts:
            retry_after = max(1, int(window_seconds - (now - window[0])) + 1)
            raise HTTPException(
                status_code=429,
                detail=f"Trop de tentatives. Réessaie dans {retry_after} secondes.",
                headers={"Retry-After": str(retry_after)},
            )

        window.append(now)

    return dependency


def reset() -> None:
    """Vide tous les compteurs. Utilisé par les tests (voir conftest.py) :
    sans ça, les appels de tous les tests précédents dans le même process
    pytest s'accumuleraient dans `_attempts` et finiraient par déclencher
    des 429 dans des tests qui n'ont rien à voir avec le rate limiting."""
    _attempts.clear()
