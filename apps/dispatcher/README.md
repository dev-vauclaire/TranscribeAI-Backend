# Dispatcher

Application one-shot chargée de publier dans Redis Streams les jobs de
transcription en attente dans PostgreSQL. Chaque exécution traite au plus un
batch, libère ses connexions puis termine. La planification reste sous la
responsabilité de l'infrastructure.

## Fonctionnement

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
- `1` lorsqu'au moins une publication ou confirmation échoue, ou lors d'un
  échec global ;
- `130` lorsque le processus est interrompu.

## Configuration

| Variable | Obligatoire | Défaut | Description |
| --- | --- | --- | --- |
| `DATABASE_URL` | oui | - | URL PostgreSQL, source de vérité des jobs |
| `REDIS_URL` | oui | - | URL Redis utilisée pour publier les messages |
| `DISPATCHER_BATCH_SIZE` | non | `100` | Jobs par exécution (1 à 1000) |

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
  transcribe-ai-dispatcher:local
```

Le conteneur s'exécute avec un utilisateur non privilégié, n'expose aucun port
et doit être relancé par un cron, un CronJob ou un autre orchestrateur selon la
fréquence de dispatch souhaitée.
