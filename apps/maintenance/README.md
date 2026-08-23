# Maintenance

Application one-shot chargée des opérations de maintenance du backend. Elle
exécute un nettoyage du stockage audio, journalise son résultat, ferme ses
ressources puis termine. La planification est volontairement laissée à
l'infrastructure (cron ou CronJob) : aucun scheduler ni processus permanent
n'est embarqué.

Le contexte transverse est décrit dans
[l'architecture](../../docs/architecture.md), les
[workflows](../../docs/workflows.md) et le
[guide d'exploitation](../../docs/operations.md).

## Repères dans le code

- `application.py` compose le repository PostgreSQL et le stockage partagé.
- `storage_cleanup.py` porte la politique et les résultats du cleanup.
- `config.py` valide la période de grâce.
- `main.py` exécute un cycle, journalise son résumé et retourne son statut.

Le package partagé fournit le scan filesystem sécurisé et le chargement des
jobs. Il ne contient pas la décision métier de suppression.

## Politique de nettoyage

`maintenance.storage_cleanup.StorageCleanupService` porte la politique propre
à cette application. Il inventorie les dossiers audio, applique la période de
grâce, puis charge par lots les jobs PostgreSQL correspondants avant la
première suppression.

- les jobs `QUEUED` et `PROCESSING` sont toujours conservés ;
- les jobs `COMPLETED` et `FAILED` ne sont supprimés qu'après la période de
  grâce calculée depuis `completed_at` et depuis la dernière activité
  filesystem du dossier ;
- les dossiers orphelins plus anciens que la période de grâce sont supprimés ;
- une lecture PostgreSQL incomplète interrompt tout le nettoyage ;
- une erreur locale ou un dossier modifié depuis l'inventaire conserve la
  cible concernée sans arrêter les autres traitements.

Cette politique reste dans l'application. Le package partagé expose seulement
les types et protocoles nécessaires pour administrer une implémentation de
stockage.

La dernière activité filesystem est le `mtime` le plus récent entre le dossier
et son contenu. Elle empêche de supprimer un upload anciennement créé mais
encore en cours d'écriture. Une date exactement égale au cutoff reste conservée.
Les dossiers trop récents sont éliminés avant toute lecture PostgreSQL.

Les UUID anciens sont chargés par un appel logique, puis découpés en requêtes de
1 000 entrées par le repository. Une entrée non canonique, un symlink, un
statut ou timestamp incohérent et toute erreur filesystem conduisent à
`KEEP`. Une cible déjà supprimée par un autre processus est traitée de manière
idempotente.

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

L'image prépare ce mountpoint avec l'UID/GID partagé `10001:10001`. Un bind
mount doit néanmoins appartenir à cet UID/GID, ou disposer d'une politique de
groupe/ACL équivalente sur l'hôte.

## Journalisation

Le cycle émet `cleanup` avec une décision par cible et un résumé contenant les
compteurs inspectés, conservés, supprimés, orphelins, trop récents et en erreur.
Une panne PostgreSQL produit un abort global sans aucune suppression.

## Tests

```bash
uv run pytest -m unit apps/maintenance/tests
uv run pytest -m integration apps/maintenance/tests
```

Les tests couvrent les statuts, les frontières de grâce, le batching, les
orphelins, les erreurs filesystem et les protections contre symlinks. Le test
d'indisponibilité PostgreSQL injecte actuellement un loader défaillant ; un
smoke de déploiement avec une URL réellement injoignable reste à ajouter.

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

Le Compose la garde hors de la stack permanente et l'expose sous le profil
`maintenance` :

```bash
docker compose --profile maintenance run --rm maintenance
```
