# Transcribe AI Shared

Ce package fournit les composants communs aux applications du backend.

## Structure

- `database` : configuration PostgreSQL, moteur, sessions, modèles et
  repositories ;
- `queue` : configuration Redis, file de jobs et exceptions associées ;
- `storage` : configuration et stockage local des fichiers audio ;
- `worker` : configuration commune aux processus workers.

Les éléments supportés sont réexportés depuis `transcribe_ai_shared` et depuis le
sous-package auquel ils appartiennent.

## Configuration

Les configurations héritent de `pydantic-settings.BaseSettings`. Elles lisent
les variables d'environnement suivantes :

| Classe | Variable | Obligatoire | Défaut |
| --- | --- | --- | --- |
| `DatabaseSettings` | `DATABASE_URL` | oui | — |
| `RedisSettings` | `REDIS_URL` | oui | — |
| `StorageSettings` | `AUDIO_STORAGE_PATH` | non | `tmp/audios_buffers` |
| `WorkerSettings` | `WORKER_ID` | oui | — |
| `WorkerSettings` | `WORKER_LEASE_SECONDS` | non | `300` |
| `WorkerSettings` | `MAX_ATTEMPTS` | non | `3` |

Le package ne charge pas implicitement de fichier `.env` : le point d'entrée de
chaque application reste responsable de fournir son environnement.

## Services

- `RedisQueueService` publie et consomme les identifiants de jobs dans une file
  FIFO Redis.
- `AudioStorageService` sauvegarde, ouvre et supprime des fichiers audio dans un
  dossier confiné.
