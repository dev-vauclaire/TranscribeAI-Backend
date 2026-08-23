# Dispatcher

Application one-shot chargée de récupérer les jobs abandonnés par un worker,
de réconcilier les publications Redis potentiellement perdues, puis de publier
les jobs de transcription en attente. Chaque exécution traite, dans cet ordre,
un batch de recovery des leases, un batch de réconciliation puis un batch de
dispatch, libère ses connexions et termine. La planification reste sous la
responsabilité de l'infrastructure.

## Recovery des leases expirés

Le dispatcher commence par sélectionner les jobs `PROCESSING` dont
`lease_expires_at` est antérieur à `NOW()` côté PostgreSQL. PostgreSQL reste
ainsi la source de temps même si l'horloge du conteneur dispatcher dérive. La
requête utilise l'index partiel `idx_job_expired_lease` existant sur les jobs
`PROCESSING`.

Chaque transition est isolée dans sa propre transaction et vérifie encore le
statut, `attempt_count` et la valeur du lease observés. Un heartbeat effectué
entre la sélection et l'UPDATE rend donc le snapshot obsolète sans écraser le
job vivant.

- lorsqu'une tentative reste disponible, le job repasse à `QUEUED`, son
  `attempt_count` est incrémenté et `dispatch_required` repasse à `true` ;
- lorsque `MAX_ATTEMPTS` est atteint, le job passe à `FAILED` sans redispatch.

Dans les deux cas, les champs du lease sont nettoyés. `MAX_ATTEMPTS` compte
l'exécution initiale : avec `3`, les tentatives portent les index `0`, `1` et
`2`.

## Réconciliation des publications anciennes

Après la recovery des leases et avant le dispatch, le dispatcher sélectionne
les jobs qui respectent simultanément les conditions suivantes :

- `status = QUEUED` ;
- `dispatch_required = false` ;
- `last_dispatched_at IS NOT NULL` et est antérieur au délai
  `RECONCILIATION_TIMEOUT_SECONDS` selon l'horloge PostgreSQL.

Pour chaque snapshot sélectionné, une transition atomique remet uniquement
`dispatch_required` à `true`. Le CAS protège le statut, l'`attempt_count` et la
date de publication observés : un worker ayant claim le job ou une publication
plus récente ne peuvent donc pas être écrasés. Le numéro de tentative n'est
jamais incrémenté par cette opération.

Cette réconciliation accepte volontairement les faux positifs. Le message
Redis initial peut encore exister lorsque le dispatcher republie la même
tentative ; le claim atomique PostgreSQL garantit alors qu'un seul worker lance
l'inférence. Redis ne décide ni d'un retry métier ni d'un changement de
tentative.

Le dispatcher reste one-shot : la périodicité est celle du Cron ou de
l'orchestrateur qui le lance. Le délai maximal avant republication est donc le
timeout configuré, augmenté de cette période et, si le batch est saturé, du
temps nécessaire pour résorber les jobs plus anciens. PostgreSQL fournit
l'horloge utilisée pour le seuil comme pour `last_dispatched_at`, afin d'éviter
les écarts entre plusieurs instances du dispatcher.

## Publication Redis

Le dispatcher sélectionne les `TranscriptionJob` qui respectent simultanément
les conditions suivantes :

- `status = QUEUED` ;
- `dispatch_required = true`.

Pour chaque job sélectionné, il publie `job_uuid` et `attempt_count` dans
`transcription:fast` ou `transcription:batch` selon son type. La confirmation
PostgreSQL n'est exécutée qu'après le succès de `XADD` et utilise
`attempt_count` comme garde de concurrence.

Les publications sont intentionnellement **at-least-once** : si Redis accepte
le message mais que la confirmation PostgreSQL échoue, le job reste éligible
et une exécution suivante peut republier le même message. Le dispatcher ne
cherche pas à dédupliquer Redis ; le claim atomique PostgreSQL des workers
neutralise les doublons.

Une erreur sur un job n'interrompt pas les autres jobs du batch. Une
confirmation devenue obsolète est ignorée proprement, notamment lorsqu'une
nouvelle tentative a déjà été réarmée.

## Exécution

Depuis la racine du workspace :

```bash
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/postgres \
REDIS_URL=redis://localhost:6379/0 \
uv run --package dispatcher dispatch-transcriptions
```

La commande retourne :

- `0` lorsque le batch se termine sans erreur, y compris si une confirmation
  est devenue obsolète ;
- `1` lorsqu'au moins une recovery, réconciliation, publication ou confirmation
  échoue, ou lors d'un échec global ;
- `130` lorsque le processus est interrompu.

## Configuration

| Variable | Obligatoire | Défaut | Description |
| --- | --- | --- | --- |
| `DATABASE_URL` | oui | - | URL PostgreSQL, source de vérité des jobs |
| `REDIS_URL` | oui | - | URL Redis utilisée pour publier les messages |
| `DISPATCHER_BATCH_SIZE` | non | `100` | Taille de chaque batch (1-1000) |
| `RECONCILIATION_TIMEOUT_SECONDS` | non | `300` | Délai de réconciliation |
| `MAX_ATTEMPTS` | non | `3` | Exécutions totales autorisées |

`MAX_ATTEMPTS` est une règle métier commune : le dispatcher et les deux workers
doivent recevoir la même valeur dans un déploiement.

Les URL de connexion doivent être fournies par le mécanisme de secrets de
l'orchestrateur et ne sont jamais journalisées.

## Image Docker

L'image doit être construite depuis la racine du workspace :

```bash
docker build \
  --file apps/dispatcher/Dockerfile \
  --tag transcribe-ai-dispatcher:local \
  .
```

Exemple d'exécution ponctuelle :

```bash
docker run --rm \
  --network <backend-network> \
  --env DATABASE_URL \
  --env REDIS_URL \
  --env DISPATCHER_BATCH_SIZE=100 \
  --env RECONCILIATION_TIMEOUT_SECONDS=300 \
  --env MAX_ATTEMPTS=3 \
  transcribe-ai-dispatcher:local
```

Le conteneur s'exécute avec un utilisateur non privilégié, n'expose aucun port
et doit être relancé par un cron, un CronJob ou un autre orchestrateur selon la
fréquence de dispatch souhaitée.
