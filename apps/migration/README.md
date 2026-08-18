# Migration

Application run-once chargée d'appliquer les migrations PostgreSQL avec Alembic.
Le processus exécute `upgrade head`, puis termine avec un code nul. Toute erreur
de configuration, de connexion ou de migration provoque un code de sortie non
nul.

## Configuration

La variable `DATABASE_URL` est obligatoire et doit utiliser le pilote synchrone
`postgresql` ou `postgresql+psycopg2`.

Exemple local :

```bash
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/postgres \
  uv run --package migration database-migrate
```

## Révisions

La révision initiale `0001_initial_schema` cible une base vide. Elle crée les
tables `transcription_jobs` et `transcription_results`, ainsi que leurs enums,
contraintes et deux index partiels : `idx_job_dispatch` pour les jobs en attente
de publication et `idx_job_expired_lease` pour les leases expirés des jobs en
cours de traitement.

## Tests de migration

Chaque révision possède un fichier `test_<revision_id>.py`. Il vérifie
uniquement l'upgrade depuis la révision précédente et le downgrade vers cette
révision précédente. Les helpers d'exécution et d'introspection communs sont
centralisés dans `tests/integration/common.py`.

Le downgrade vers `base` est destructif : il supprime les deux tables et les
types enum associés. Alembic conserve sa table technique vide
`alembic_version`.

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
