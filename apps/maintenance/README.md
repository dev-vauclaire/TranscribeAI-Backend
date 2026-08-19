# Maintenance

Application one-shot chargée des opérations de maintenance du backend. Elle
exécute un nettoyage du stockage audio, journalise son résultat, ferme ses
ressources puis termine. La planification est volontairement laissée à
l'infrastructure (cron ou CronJob) : aucun scheduler ni processus permanent
n'est embarqué.

## Politique de nettoyage

`maintenance.storage_cleanup.StorageCleanupService` porte la politique propre
à cette application. Il inventorie les dossiers audio, applique la période de
grâce, puis charge par lots les jobs PostgreSQL correspondants avant la
première suppression.

- les jobs `QUEUED` et `PROCESSING` sont toujours conservés ;
- les jobs `COMPLETED` et `FAILED` ne sont supprimés qu'après la période de
  grâce calculée depuis `completed_at` ;
- les dossiers orphelins plus anciens que la période de grâce sont supprimés ;
- une lecture PostgreSQL incomplète interrompt tout le nettoyage ;
- une erreur locale ou un dossier modifié depuis l'inventaire conserve la
  cible concernée sans arrêter les autres traitements.

Cette politique reste dans l'application. Le package partagé expose seulement
les types et protocoles nécessaires pour administrer une implémentation de
stockage.

## Exécution

Depuis la racine du workspace :

```bash
uv run --package maintenance cleanup-storage
```

La commande retourne `0` lorsque tous les dossiers ont été traités sans
erreur, `1` en cas d'échec global ou partiel, et `130` si son exécution est
interrompue.

## Configuration

| Variable | Obligatoire | Défaut | Description |
| --- | --- | --- | --- |
| `DATABASE_URL` | oui | - | URL PostgreSQL utilisée pour retrouver les jobs |
| `AUDIO_STORAGE_PATH` | non | `tmp/audios_buffers` | Racine du stockage audio |
| `MAINTENANCE_CLEANUP_GRACE_PERIOD_SECONDS` | non | `3600` | Grâce |

Une valeur nulle ou négative pour la période de grâce est refusée.

Le défaut relatif convient au développement local. Dans le conteneur non-root,
`AUDIO_STORAGE_PATH` doit désigner un volume partagé inscriptible, par exemple
`/data/transcriptions`.

## Image Docker

L'image doit être construite depuis la racine du workspace :

```bash
docker build \
  --file apps/maintenance/Dockerfile \
  --tag transcribe-ai-maintenance:local \
  .
```

Exemple d'exécution ponctuelle :

```bash
docker run --rm \
  --network <database-network> \
  --env DATABASE_URL \
  --env AUDIO_STORAGE_PATH=/data/transcriptions \
  --volume <audio-volume>:/data/transcriptions \
  transcribe-ai-maintenance:local
```

Le volume doit être le même que celui de l'API et des workers, et son
propriétaire doit autoriser l'utilisateur non privilégié du conteneur à
supprimer les fichiers expirés. L'image n'expose aucun port et ne doit être
lancée qu'une fois par occurrence planifiée.
