# Guide de contribution — SocialNet

Ce document explique comment travailler à plusieurs sur ce projet sans
s'écraser mutuellement le travail.

## Règle d'or

**La branche `main` doit toujours contenir une version qui fonctionne.**
On n'y pousse jamais de code non testé directement. Toute modification passe
par une branche séparée puis une pull request (voir plus bas).

## Installation (à faire une fois)

Voir `README.md` pour l'installation complète (WSL, compilation de
l'extension C, etc.). Une fois l'installation faite :

```bash
git clone <url-du-dépôt>
cd socialnet
cp .env.example .env      # puis éditer .env avec une vraie clé secrète
./run.sh
```

## Workflow quotidien

### 1. Toujours partir d'une base à jour

```bash
git checkout main
git pull
```

### 2. Créer une branche pour son travail

Une branche par fonctionnalité ou correction, jamais de travail direct sur
`main`.

```bash
git checkout -b feature/nom-court-de-la-fonctionnalite
# ou
git checkout -b fix/nom-court-du-bug
```

Exemples : `feature/notifications`, `fix/crash-unlike-post-inexistant`.

### 3. Travailler, tester, committer régulièrement

Committer souvent, par petits changements cohérents plutôt qu'un seul énorme
commit en fin de journée.

```bash
git add chemin/du/fichier
git commit -m "type: description courte au présent"
```

**Format des messages de commit** (préfixe obligatoire) :

| Préfixe | Utilisation |
|---|---|
| `feat:` | nouvelle fonctionnalité |
| `fix:` | correction de bug |
| `docs:` | documentation uniquement |
| `refactor:` | réorganisation du code sans changement de comportement |
| `test:` | ajout/modification de tests |
| `chore:` | tâche technique (dépendances, config...) |

Exemples :
```
feat: ajoute la commande /notifications
fix: corrige le crash quand on like un post supprimé
docs: documente les fonctions de database.py
```

### 4. Avant de proposer ses changements : vérifier que tout marche encore

```bash
# Recompiler l'extension C si on l'a modifiée
cd c_ext && python3 setup.py build_ext --inplace && cd ..

# Relancer le serveur et tester manuellement les commandes touchées
./run.sh

# Lancer les tests automatisés (voir README.md > Lancer les tests)
cd backend && python3 -m pytest
cd ../c_ext && python3 -m pytest
```

Ne jamais proposer une modification qu'on n'a pas testée soi-même. Si ta
modification touche une route API ou le moteur de recherche C, ajoute ou
mets à jour les tests correspondants dans `backend/tests/` ou
`c_ext/tests/` — ne compte pas uniquement sur les tests existants pour
couvrir du nouveau comportement.

### 5. Pousser la branche et ouvrir une pull request

```bash
git push -u origin feature/nom-court-de-la-fonctionnalite
```

Puis sur GitHub/GitLab : ouvrir une "Pull Request" (ou "Merge Request") vers
`main`, avec une description de ce qui a été changé et pourquoi. Au moins une
autre personne de l'équipe relit avant fusion.

### 6. Après fusion dans `main`

Si l'état de `main` est stable et testé, marquer une nouvelle version :

```bash
git checkout main
git pull
git tag -a v1.1 -m "Description courte de ce qui a changé depuis v1.0"
git push --tags
```

## Résoudre un conflit

Si Git signale un conflit lors d'un `pull` ou d'une fusion, il marque les
zones en désaccord directement dans les fichiers concernés avec des balises
`<<<<<<<`, `=======`, `>>>>>>>`. Il faut ouvrir le fichier, choisir/fusionner
manuellement la bonne version, supprimer les balises, puis :

```bash
git add fichier-corrigé
git commit
```

En cas de doute sur un conflit, en parler à la personne qui a écrit l'autre
version plutôt que de trancher seul.

## Style de code attendu

- **Python** : docstrings sur chaque fonction publique (voir l'existant dans
  `backend/` comme référence). Noms de variables explicites, en anglais ou en
  français mais cohérent dans un même fichier.
- **C** : commentaire au-dessus de chaque fonction expliquant son rôle,
  entrées et sortie.
- **JS (frontend)** : chaque commande du terminal doit avoir un usage clair
  affiché en cas d'erreur (voir le pattern existant dans `COMMANDS`).

## Suivi des tâches

Utiliser les "Issues" de GitHub/GitLab pour toute tâche (bug ou nouvelle
fonctionnalité) : une issue = une tâche assignable, avec une étiquette
(`bug`, `feature`, `documentation`...). Référencer l'issue dans les commits
et la pull request associée, ex : `fix: corrige le crash au like (#12)`.
