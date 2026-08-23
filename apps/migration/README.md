# Migration

Application run-once chargée d'appliquer les migrations PostgreSQL avec Alembic.
Le processus exécute `upgrade head`, puis termine avec un code nul. Toute erreur
de configuration, de connexion ou de migration provoque un code de sortie non
nul.

Le contexte transverse est décrit dans
[l'architecture](../../docs/architecture.md), le
[guide de développement](../../docs/development.md) et le
[guide d'exploitation](../../docs/operations.md).

## Repères dans le code

- `main.py` crée le moteur, ouvre la transaction et lance `upgrade head`.
- `alembic.ini` et `migrations/env.py` composent Alembic avec la connexion déjà
  ouverte.
- `migrations/versions/` contient les révisions ordonnées.
- `tests/integration/` valide chaque upgrade et downgrade réel sur PostgreSQL.

Alembic reçoit la transaction du point d'entrée. Sa configuration de logs est
désactivée afin de conserver le formatter JSON commun. Le moteur est toujours
disposé, que la migration réussisse ou non.

## Configuration

La variable `DATABASE_URL` est obligatoire et doit utiliser le pilote synchrone
`postgresql` ou `postgresql+psycopg2`.

Exemple local :

```bash
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/postgres \
  uv run --package migration database-migrate
```

La commande retourne `0` après `upgrade head`, `1` pour une erreur et `130`
pour une interruption. Elle émet l'événement structuré `migration` avec les
actions `started`, `completed`, `failed` ou `interrupted`.

Une seule instance doit migrer une base pendant un déploiement. Aucun downgrade
automatique n'est exécuté après un échec applicatif.

## Révisions

La révision initiale `0001_initial_schema` cible une base vide. Elle crée les
tables `transcription_jobs` et `transcription_results`, ainsi que leurs enums,
contraintes et deux index partiels : `idx_job_dispatch` pour les jobs en attente
de publication et `idx_job_expired_lease` pour les leases expirés des jobs en
cours de traitement.

La révision `0002_add_stale_dispatch_index` ajoute l'index partiel
`idx_job_stale_dispatch(last_dispatched_at, job_uuid)` pour parcourir les
anciennes publications `QUEUED` confirmées par PostgreSQL et potentiellement
perdues dans Redis.

## Tests de migration

Chaque révision possède un fichier `test_<revision_id>.py`. Il vérifie
uniquement l'upgrade depuis la révision précédente et le downgrade vers cette
révision précédente. Les helpers d'exécution et d'introspection communs sont
centralisés dans `tests/integration/common.py`.

Le downgrade vers `base` est destructif : il supprime les deux tables et les
types enum associés. Alembic conserve sa table technique vide
`alembic_version`.

Tests disponibles :

```bash
uv run pytest -m unit apps/migration/tests
uv run pytest -m integration apps/migration/tests
```

Les intégrations ciblent PostgreSQL 16 via Testcontainers. Le smoke Compose
construit aussi l'image et exécute son entrypoint sur une base vide.

## Image Docker

L'image doit être construite depuis la racine du workspace :

```bash
docker build \
  --file apps/migration/Dockerfile \
  --tag transcribe-ai-migration:local \
  .
```

Le conteneur ne doit être lancé qu'une fois par déploiement, avant les
applications :

```bash
export DATABASE_URL=postgresql://postgres:postgres@postgres:5432/postgres
docker run --rm \
  --network <database-network> \
  --env DATABASE_URL \
  transcribe-ai-migration:local
```

En production, `DATABASE_URL` doit provenir du mécanisme de secrets de
l'orchestrateur et ne doit pas être inscrite directement dans la commande.

Cette image est destinée à un Job ou à un init container. Elle n'expose aucun
port et ne lance pas de boucle permanente.

Dans le Compose racine, `migration` dépend de PostgreSQL healthy. L'API et les
workers dépendent ensuite de son état `service_completed_successfully`, ce qui
empêche une readiness trompeuse sur une base joignable mais non migrée.
