# Consignes locales — Migration

Lire [README.md](README.md),
[l'architecture](../../docs/architecture.md) et le modèle SQLAlchemy concerné.

## Frontières

- L'application exécute `upgrade head` une fois puis termine.
- Ne lui ajoutez ni serveur, ni boucle, ni migration automatique au démarrage
  des autres applications.
- Une seule instance de migration doit être exécutée par déploiement.

## Invariants

- Toute évolution du modèle persistant possède une révision Alembic explicite.
- Tester l'upgrade depuis la révision précédente et le downgrade vers cette
  révision précédente.
- Ne pas utiliser `metadata.create_all()` comme substitut dans ces tests.
- Préserver les index partiels nécessaires aux requêtes de travail.
- Ne jamais lancer un downgrade destructif automatiquement.
- Garder la configuration de logs JSON du point d'entrée.

## Validation

```bash
uv run pytest -m unit apps/migration/tests
uv run pytest -m integration apps/migration/tests
```

Vérifier que les révisions, `alembic.ini` et le template sont bien inclus dans
le package ou l'image lors d'un changement de build.
