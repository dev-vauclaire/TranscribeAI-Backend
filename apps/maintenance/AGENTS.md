# Consignes locales — Maintenance

Lire [README.md](README.md), les
[workflows](../../docs/workflows.md) et le
[guide d'exploitation](../../docs/operations.md).

## Frontières

- La maintenance reste one-shot, sans daemon, scheduler ou serveur HTTP.
- La politique de cleanup reste dans cette application.
- Le package partagé fournit seulement les contrats et primitives filesystem.

## Invariants

- En cas d'incertitude, conserver la cible.
- Une panne ou lecture PostgreSQL partielle interdit toute suppression.
- Inventorier les UUID, puis charger les jobs en batch ; aucun N+1.
- Conserver toujours les jobs `QUEUED` et `PROCESSING`.
- Exiger l'ancienneté filesystem et métier pour un job terminal.
- Revalider le snapshot avant suppression.
- Refuser UUID non canonique, path traversal, symlink et contenu inattendu.
- Une erreur locale ne doit pas arrêter les autres dossiers sûrs.

## Validation

```bash
uv run pytest -m unit apps/maintenance/tests
uv run pytest -m integration apps/maintenance/tests
```

Une modification Docker doit vérifier les permissions du volume partagé avec
l'API et les workers.
