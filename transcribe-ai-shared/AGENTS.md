# Consignes locales — Package partagé

Lire [README.md](README.md),
[l'architecture](../docs/architecture.md), les
[workflows](../docs/workflows.md) et les
[contrats](../docs/transcription-contracts.md).

## Frontières

- Ajouter ici uniquement un contrat ou comportement réellement partagé.
- Ne jamais importer une application depuis ce package.
- Les repositories exécutent et flushent, mais ne committent pas.
- Les services applicatifs possèdent les transactions.
- Séparer objets de valeur, protocoles et adaptateurs concrets.
- Toute nouvelle API publique doit être réexportée intentionnellement et testée.

## Invariants

- PostgreSQL arbitre les claims, leases, retries et états terminaux.
- Redis assure une livraison at-least-once ; aucun retry métier via Redis.
- ACK/suppression uniquement après le commit durable correspondant.
- Completion : résultat et statut dans une transaction unique.
- `XAUTOCLAIM` ne suffit jamais à déclarer un crash.
- `AudioLocation` reste opaque et les chemins filesystem sont validés.
- Les logs utilisent une liste positive et excluent toute donnée sensible.
- `MAX_ATTEMPTS` et les noms de streams restent cohérents entre applications.

## Validation

```bash
uv run pytest -m unit transcribe-ai-shared/tests
uv run pytest -m integration transcribe-ai-shared/tests
```

Une modification du runtime doit également exécuter les tests unitaires des deux
applications worker et, selon le risque, les tests système.
