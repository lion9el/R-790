# Mission commune — Projet AGENT-ZERO

## Objectif
Collaborer pour concevoir et documenter un **agent IA autonome** qui :
- n'a **pas besoin d'être ré-entraîné** (pas de fine-tuning obligatoire) ;
- reste **très capable** grâce à : mémoire structurée, outils, réflexion, monde externe, dépôt de travail ;
- peut s'améliorer par **écriture de règles, de procédures et de souvenirs**, pas en modifiant des poids.

## Principe central
La puissance vient de l'**architecture** (mémoire + outils + boucle de réflexion + environnement),
pas seulement du modèle de langage. Un petit modèle bien outillé peut battre un gros modèle nu.

## Livrables attendus (dans socialnet-world)
1. `notes/agent-zero/ARCHITECTURE.md` — schéma de l'agent
2. `notes/agent-zero/MEMORY.md` — design de la mémoire
3. `notes/agent-zero/TOOLS.md` — outils nécessaires
4. `notes/agent-zero/LOOP.md` — boucle perception → réflexion → action
5. `notes/agent-zero/SAFETY.md` — garde-fous
6. `objects/agent-zero-spec` — spécification consolidée
7. Discussions et désaccords productifs entre Nova, Echo et Orbit

## Rôles suggérés
- **Nova** : vision, cas d'usage, créativité, questions ouvertes
- **Echo** : critique, cohérence, tests mentaux, détection de failles
- **Orbit** : architecture, structure des fichiers, intégration système

## Contraintes
- Pas de promesse magique : rester technique et honnête
- Tout progrès important doit être **écrit dans le dépôt** (write_repo)
- Travailler ensemble : se répondre, se contredire, consolider
