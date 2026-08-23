# Consignes locales — API

Lire [README.md](README.md),
[l'architecture](../../docs/architecture.md) et les
[workflows](../../docs/workflows.md) avant de modifier cette application.

## Frontières

- Respecter `Routes -> Controllers -> Services -> ports/infrastructure`.
- Une route ne porte ni transaction, ni accès filesystem, ni règle métier.
- Un controller traduit HTTP et délègue ; un service possède le use case.
- Les schémas Pydantic représentent uniquement le contrat HTTP public.
- Les repositories, modèles SQLAlchemy et le stockage concret vivent dans le
  package partagé lorsqu'ils sont communs.
- L'API ne publie jamais dans Redis et ne dépend pas de sa disponibilité.

## Invariants

- Valider la taille mesurée par Starlette avant l'écriture finale, puis le
  contenu stocké avec `ffprobe`.
- Déporter le stockage et `ffprobe`, synchrones, hors de l'event loop.
- Créer le job `QUEUED`, attempt zéro et dispatch requis dans PostgreSQL.
- Supprimer l'audio après la validation ou un échec avant le flush réussi, mais
  le conserver après une erreur de commit tant que le flow reste conservateur.
- Ne jamais exposer les leases, `audio_uri`, `last_error` ou données de dispatch.
- Un job `COMPLETED` sans résultat durable est une incohérence serveur.
- Redis ne participe ni à `/health/ready`, ni au flow de création.

## Validation

```bash
uv run pytest -m unit apps/api/tests
uv run pytest -m integration apps/api/tests
```

Les intégrations exigent Docker, `ffmpeg` et `ffprobe`. Toute nouvelle variable
doit aussi apparaître dans `.env.example` et dans le README.
