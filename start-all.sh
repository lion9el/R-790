#!/usr/bin/env bash
# Compat : redirige vers le point d'entrée unique ./socialnet
exec "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/socialnet" up "$@"
