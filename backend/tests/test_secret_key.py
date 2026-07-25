"""Tests pour la gestion de SOCIALNET_SECRET (backend/auth.py).

Ces tests importent `auth` dans un sous-process isolé (via `python3 -c`)
plutôt que directement, car `SECRET_KEY` est calculé une seule fois au
niveau du module, à l'import — le réimporter dans le même process pytest
(déjà chargé par une fixture `client` précédente) ne réexécuterait pas ce
calcul et donnerait un faux résultat.
"""
import os
import subprocess
import sys

AUTH_DIR = os.path.join(os.path.dirname(__file__), "..")


def _secret_key_for_env(env_value):
    """Lance un sous-process Python isolé qui importe `auth` avec
    SOCIALNET_SECRET positionné (ou absent) et renvoie SECRET_KEY."""
    env = os.environ.copy()
    env.pop("SOCIALNET_SECRET", None)
    if env_value is not None:
        env["SOCIALNET_SECRET"] = env_value

    result = subprocess.run(
        [sys.executable, "-c", "import auth; print(auth.SECRET_KEY)"],
        cwd=AUTH_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip(), result.stderr


def test_missing_secret_generates_random_strong_key():
    key, stderr = _secret_key_for_env(None)
    assert len(key) == 64  # secrets.token_hex(32) -> 64 caractères hexadécimaux
    assert "n'est pas défini" in stderr


def test_unmodified_placeholder_is_rejected_like_missing_secret():
    """Copier .env.example sans changer la valeur doit être traité comme
    si rien n'était défini, pas comme un vrai secret utilisable."""
    key, stderr = _secret_key_for_env("change-moi-avant-la-production")
    assert key != "change-moi-avant-la-production"
    assert len(key) == 64
    assert "jamais remplacée" in stderr


def test_real_secret_is_used_as_is():
    real_secret = "un-vrai-secret-suffisamment-long-pour-hmac-sha256-1234567890"
    key, stderr = _secret_key_for_env(real_secret)
    assert key == real_secret
    assert stderr == ""  # aucun avertissement quand un vrai secret est fourni


def test_two_processes_without_secret_get_different_random_keys():
    """Confirme que le secret de repli n'est PAS une valeur fixe : deux
    lancements sans SOCIALNET_SECRET ne doivent jamais produire le même
    secret (sinon on aurait juste déplacé le problème du secret-en-dur)."""
    key1, _ = _secret_key_for_env(None)
    key2, _ = _secret_key_for_env(None)
    assert key1 != key2
