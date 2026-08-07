#!/usr/bin/env bash
# Compat historique : ./run.sh = ./socialnet up (tout-en-un)
# Pour l'API seule en mode reload : ./socialnet api
exec "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/socialnet" "${1:-up}"
