# Instructions pour les agents de développement

Ce fichier s'applique à tout le dépôt. Les fichiers `AGENTS.md` placés dans les
sous-répertoires ajoutent des règles propres à un composant.

## Contexte à charger avant de travailler

Avant toute analyse ou modification :

1. lire le [README racine](README.md) ;
2. lire [l'architecture](docs/architecture.md) et les
   [workflows](docs/workflows.md) ;
3. lire le README et le `AGENTS.md` du composant concerné ;
4. inspecter `git status`, `git diff`, `git diff --staged` et le contexte autour
   des lignes modifiées ;
5. vérifier les modèles, protocoles, migrations et tests appelés par le diff.

Ne déduisez pas l'état courant du projet d'une ancienne conversation. Le code,
les migrations, le lockfile et la documentation versionnée sont les sources à
recouper.

## Mode de collaboration

Le mode par défaut est la revue et le mentorat : analysez et expliquez sans
modifier les fichiers. Une demande de revue, de diagnostic ou d'explication
n'autorise pas une correction.

Modifiez le dépôt seulement après une instruction explicite telle que
« implémente », « corrige » ou « applique ». Ne créez un commit et ne poussez
qu'après une validation explicite. Préservez toujours les changements locaux
qui ne font pas partie du périmètre.

Après avoir modifié une application, résumez l'impact sur son flow, les fichiers
touchés et les tests exécutés. Signalez les limites ou décisions restantes.

Les échanges et la documentation sont rédigés en français. Les identifiants de
code suivent les conventions Python et les noms de domaine existants.

## Invariants d'architecture

- PostgreSQL est la source de vérité du cycle de vie des jobs.
- Redis Streams est un transport at-least-once, pas une source d'état métier.
- L'API ne dépend jamais de Redis pour créer ou consulter un job.
- Il n'existe pas de table Outbox. `dispatch_required` représente le travail à
  publier.
- Un doublon Redis est acceptable. Le claim atomique PostgreSQL empêche une
  seconde inférence.
- `attempt_count` commence à zéro. Une republication de réconciliation ne
  l'incrémente pas ; seul un nouvel attempt métier le fait.
- `XAUTOCLAIM` réattribue un message Redis mais ne décide jamais qu'un worker a
  crashé. Cette décision appartient au lease PostgreSQL.
- Toute transition effectuée par un worker depuis `PROCESSING` vérifie au
  minimum le propriétaire du lease et l'`attempt_count` observé. La recovery du
  dispatcher compare plutôt l'attempt et l'échéance expirée observée.
- Le résultat et la transition `PROCESSING -> COMPLETED` partagent une seule
  transaction PostgreSQL.
- Un message Redis n'est ACK/supprimé qu'après le commit de toute transition
  PostgreSQL requise. Un message déjà obsolète selon un état durable peut être
  nettoyé sans nouvelle transition.
- Les applications one-shot restent one-shot. Ne leur ajoutez ni serveur HTTP,
  ni scheduler Python, ni boucle permanente.
- Le runtime worker partagé ne connaît ni FastAPI, ni HTTP, ni moteur ML
  concret.

Toute modification de ces invariants est une décision d'architecture. Elle doit
être annoncée, documentée dans `docs/` et couverte par des tests d'intégration.

## Responsabilités et dépendances

- `apps/` contient les compositions et règles propres à chaque processus.
- `transcribe-ai-shared` contient uniquement les modèles, contrats et
  implémentations réellement partagés.
- Une application peut dépendre du package partagé. L'inverse est interdit.
- Les repositories ne committent pas. Le service ou l'opération applicative
  possède la transaction.
- Les adaptateurs ML transforment uniquement un audio en
  `TranscriptionOutput`. Ils ne connaissent ni Redis, ni PostgreSQL, ni leases.
- `AudioLocation` est une URI opaque. Aucun appelant ne reconstruit lui-même un
  chemin physique.
- La politique de cleanup reste dans l'application maintenance.

Dans l'API, la direction normale des appels est :

```text
Routes -> Controllers -> Services -> Repositories / Storage / Media
```

Les routes aiguillent, les controllers traduisent HTTP, les schémas définissent
le contrat public et les services portent les use cases.

## Conventions de code

- Python 3.12 et environnement géré par `uv`.
- Typage précis ; éviter `Any` lorsqu'un contrat plus étroit est raisonnable.
- I/O PostgreSQL et Redis asynchrones dans l'API, le dispatcher, la maintenance
  et les workers. La migration Alembic utilise volontairement psycopg2 en mode
  synchrone. Une bibliothèque ML ou filesystem bloquante doit être isolée de
  l'event loop lorsqu'un heartbeat doit continuer.
- Configuration par `pydantic-settings` et variables d'environnement. Aucun
  chargement implicite de `.env`.
- Les secrets utilisent des types adaptés et ne sont jamais inclus dans les
  erreurs, logs, tests ou exemples versionnés.
- Logs applicatifs JSON via `transcribe_ai_shared.observability`.
- Ne journalisez jamais l'audio, une transcription complète, un payload, une
  URL de connexion ou un token.
- Une erreur persistée utilise un code stable et non sensible, jamais le
  message brut d'une exception.
- Les migrations Alembic sont explicites et testées en upgrade et downgrade.
- Utiliser un Conventional Commit centré sur la responsabilité modifiée.

## Tests attendus

- `unit` : pas de service externe, pas de modèle téléchargé ;
- `integration` : PostgreSQL ou Redis réel via Testcontainers ;
- `system` : workflow transverse avec `FakeTranscriber` ;
- `gpu` : inférence ML réelle, activation explicite et runner adapté.

Commandes usuelles :

```bash
uv run pytest -m unit
uv run pytest -m integration
uv run pytest -m system tests/system
uv run ruff check .
uv run ruff format --check .
uv run pre-commit run --all-files
```

Choisissez d'abord la suite la plus proche du changement, puis élargissez selon
le risque. Ne téléchargez jamais un modèle dans un test unitaire standard.

## Documentation à maintenir

Quand un changement modifie une responsabilité, un flow, une configuration ou
une commande :

- mettre à jour le README du composant ;
- mettre à jour `docs/workflows.md` pour un invariant transverse ;
- mettre à jour `docs/architecture.md` pour une frontière ou dépendance ;
- mettre à jour `docs/operations.md` pour le déploiement ou la supervision ;
- mettre à jour `.env.example` sans valeur secrète pour une nouvelle variable ;
- ajouter une migration et ses tests pour tout changement de schéma.

Évitez de recopier les mêmes tableaux de configuration dans plusieurs fichiers.
Le README de chaque application reste la référence détaillée de sa
configuration ; les documents racine expliquent seulement les relations entre
composants.

## Fin de tâche

Avant de demander une validation :

1. vérifier le diff et l'absence de fichiers inattendus ;
2. exécuter les tests proportionnés au risque ;
3. exécuter les contrôles de format et de lint pertinents ;
4. résumer le nouveau flow et les garanties de concurrence ;
5. proposer le Conventional Commit sans le créer tant qu'il n'est pas validé.
