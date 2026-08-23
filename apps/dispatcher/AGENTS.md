# Consignes locales — Dispatcher

Lire [README.md](README.md) et les
[workflows transverses](../../docs/workflows.md) avant une modification.

## Frontières

- Le dispatcher reste une application one-shot sans serveur HTTP ni scheduler.
- Un cycle exécute recovery, réconciliation, puis dispatch.
- Il ne consomme aucun message et ne réalise ni ACK, ni retry de worker.
- PostgreSQL reste la source de vérité et de temps.

## Invariants

- Publier avec `XADD` avant de confirmer `mark_dispatched`.
- Accepter une republication si cette confirmation échoue.
- Ne pas dédupliquer Redis.
- Préserver les gardes sur l'attempt et les valeurs du snapshot observé.
- `mark_dispatched` ne doit pas exiger `QUEUED` : un worker peut déjà avoir
  claim le job.
- Recovery : incrémenter l'attempt uniquement pour un lease réellement expiré.
- Réconciliation : réarmer le dispatch sans incrémenter l'attempt.
- Une erreur individuelle ne bloque pas le reste du batch.

## Validation

```bash
uv run pytest -m unit apps/dispatcher/tests
uv run pytest -m integration apps/dispatcher/tests
```

Un changement de `MAX_ATTEMPTS` doit rester cohérent avec les workers.
