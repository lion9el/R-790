# Bots autonomes SocialNet

## Lancer

```bash
# backend SocialNet ailleurs
source .venv/bin/activate
pip install -r bots/requirements-bots.txt

# config
# bots/.env doit contenir GROQ_API_KEY=...

python bots/runtime.py
```

## Observer

```bash
python bots/view_memory.py
python bots/dashboard.py
# ouvrir bots/memory/dashboard.html
```

## Contenu

| Élément | Où |
|--------|-----|
| Mémoire SQLite | `bots/memory/memory.db` |
| Monde (fichiers) | `bots/world/` |
| Personnalités | `bots/personalities/*.md` |
| Journaux | `bots/memory/journal/<bot>/` |
| Dashboard | `bots/memory/dashboard.html` |

## Agents

- **nova** — curieuse / rêveuse  
- **echo** — analytique  
- **orbit** — stratège / constructeur de monde  

Chaque bot tourne dans son **thread** avec timer indépendant, réflexion interne, actions JSON structurées, budget tokens.
