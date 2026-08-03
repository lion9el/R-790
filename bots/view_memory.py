#!/usr/bin/env python3
"""Consulter la mémoire SQLite des bots."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import memory_store as mem

def main():
    mem.init_db()
    bot = sys.argv[1] if len(sys.argv) > 1 else None
    print(f"DB: {mem.DB_PATH}\n")
    if bot:
        bots = [bot]
    else:
        bots = ["nova", "echo"]
    for b in bots:
        print(f"======== @{b} ========")
        print(mem.memory_prompt_block(b))
        print()
    print("======== MONDE ========")
    for o in mem.list_world(30):
        print(f"#{o['id']} {o['name']} @ {o['location']} — {o['description'][:120]} (by {o['created_by']})")

if __name__ == "__main__":
    main()
