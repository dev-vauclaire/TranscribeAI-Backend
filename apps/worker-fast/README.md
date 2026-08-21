# Worker FAST

Cette application consomme les nouveaux messages du stream
`transcription:fast` un par un, tente leur claim PostgreSQL puis délègue le
traitement au transcriber injecté. La boucle conserve les connexions et le
transcriber entre deux messages. Après une inférence réussie, le résultat et
l'état `COMPLETED` sont committés ensemble avant l'ACK Redis. Elle ne contient
aucune logique HTTP et n'implémente pas encore le retry ou le moteur ML réel.

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
uv run --package worker-fast worker-fast
```

Les autres variables sont documentées dans `.env.example`. `WORKER_ID` sert à
la fois d'identifiant de consumer Redis et de propriétaire du lease PostgreSQL ;
il doit donc être unique pour chaque instance concurrente du worker.

## Tests

```shell
uv run pytest -m unit apps/worker-fast/tests
uv run pytest -m integration transcribe-ai-shared/tests/worker/integration
```

Le second scénario exerce le runtime commun avec PostgreSQL et Redis réels via
Testcontainers, notamment face à deux messages dupliqués.
