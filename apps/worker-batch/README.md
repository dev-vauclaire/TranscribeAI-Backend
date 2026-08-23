# Worker BATCH

Cette application consomme les nouveaux messages et récupère périodiquement les
anciens pending du stream `transcription:batch`, un par un. Chaque message passe
par la même décision PostgreSQL avant une éventuelle délégation au transcriber
injecté. La boucle conserve les connexions et le transcriber entre deux
messages. Pendant l'inférence, elle renouvelle le lease PostgreSQL selon
`WORKER_HEARTBEAT_SECONDS`. Après une inférence réussie, le résultat et l'état
`COMPLETED` sont committés ensemble avant l'ACK Redis. Elle ne contient aucune
dépendance FastAPI ou Flask et n'implémente pas encore la diarization ou le
moteur ML réel.

Une erreur retryable réarme le job dans PostgreSQL avant de supprimer l'ancien
message Redis. Une erreur permanente, ou une tentative ayant atteint
`MAX_ATTEMPTS`, termine le job en `FAILED` sans nouvelle publication.

Le premier balayage du Pending Entries List a lieu au démarrage, puis toutes les
`WORKER_AUTOCLAIM_INTERVAL_SECONDS`. Seuls les messages inactifs depuis au moins
`WORKER_AUTOCLAIM_MIN_IDLE_MILLISECONDS` sont réattribués. Ce délai Redis ne
constitue jamais une preuve de crash, même pour une transcription BATCH très
longue : un job `PROCESSING` pour la tentative courante reste pending et seul
son lease PostgreSQL gouverne sa recovery. Les jobs terminaux, les anciennes
tentatives et les UUID absents sont nettoyés sans inférence. Un UUID absent est
considéré comme obsolète, puisque le contrat de publication exige que le job
PostgreSQL soit committé avant le message Redis. Le balayage reste séquentiel et
ne concurrence pas l'inférence en cours.

## Exécution de développement

Le seul backend actuellement disponible est un fake. Son activation exige les
deux variables explicites suivantes :

```shell
WORKER_ENVIRONMENT=development
WORKER_TRANSCRIBER_BACKEND=fake
```

Sans backend configuré, ou si le fake est demandé en production, le worker
échoue clairement sans produire de faux résultat.

Depuis la racine du workspace :

```shell
uv run --package worker-batch worker-batch
```

Les autres variables sont documentées dans `.env.example`. `WORKER_ID` sert à
la fois d'identifiant de consumer Redis et de propriétaire du lease PostgreSQL ;
il doit donc être unique pour chaque instance concurrente du worker.
`WORKER_HEARTBEAT_SECONDS` doit être strictement inférieur à
`WORKER_LEASE_SECONDS` ; un renouvellement refusé interrompt la tentative sans
finaliser ni acquitter son message.
`MAX_ATTEMPTS` compte la tentative initiale : avec la valeur `3`, les tentatives
portent les indices `0`, `1` et `2`.

## Tests

```shell
uv run pytest -m unit apps/worker-batch/tests
uv run pytest -m integration transcribe-ai-shared/tests/worker/integration
```

Le second scénario exerce le runtime commun avec PostgreSQL et Redis réels via
Testcontainers, notamment face à deux messages dupliqués.
