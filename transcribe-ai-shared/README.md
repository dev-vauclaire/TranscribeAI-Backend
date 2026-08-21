# Transcribe AI Shared

Ce package fournit les composants communs aux applications du backend.

## Structure

- `database` : configuration PostgreSQL, moteur, sessions, modèles et
  repositories ;
- `queue` : contrats, modèles et adaptateur asynchrone Redis Streams ;
- `storage` : contrat de stockage audio et implémentation sur système de
  fichiers ;
- `worker` : runtime, contrats et composition communs aux processus workers.

Les éléments supportés sont réexportés depuis `transcribe_ai_shared` et depuis le
sous-package auquel ils appartiennent.

Le sous-package `storage` sépare explicitement ses responsabilités :

- `models.py` contient les objets de valeur et leurs invariants, sans accès
  I/O ;
- `protocols.py` décrit les contrats consommés par les applications ;
- `filesystem.py` porte l'adaptateur concret du volume partagé ;
- `exceptions.py` et `config.py` regroupent respectivement les erreurs publiques
  et la configuration.

Le sous-package `worker` suit le même découpage :

- `models.py` contient les snapshots et résultats détachés ;
- `protocols.py` décrit le `Transcriber` et le port PostgreSQL minimal ;
- `postgresql.py` adapte `JobRepository.claim()` à une transaction courte ;
- `runtime.py` porte le flux consume, claim et transcribe ;
- `application.py` compose et ferme les adaptateurs partagés ;
- `testing.py` contient uniquement le fake explicitement destiné au
  développement et aux tests.

## Modèles de transcription

Le package expose deux modèles SQLAlchemy :

- `TranscriptionJob` porte le cycle de vie, l'état de dispatch, les tentatives et
  le lease du job ;
- `TranscriptionResult` contient l'unique résultat JSONB associé à un job et une
  note textuelle optionnelle.

Les clés sont des UUID natifs PostgreSQL. La relation résultat utilise une clé
étrangère `RESTRICT` : un job référencé par un résultat ne peut pas être
supprimé. Les schémas Pydantic associés sont configurés pour valider directement
les instances SQLAlchemy.

## Repositories et transactions

`JobRepository` utilise une `AsyncSession` SQLAlchemy et ne réalise aucun
commit. L'application délimite l'unité de travail avec `async_transaction` :

```python
engine = create_async_db_engine(DatabaseSettings())
session_factory = create_async_session_factory(engine)

async with async_transaction(session_factory) as session:
    repository = JobRepository(session)
    await repository.add(job)
```

Le moteur synchrone reste utilisé par Alembic et par le code historique en
attente de migration.

Le repository expose également la sélection des jobs en attente de dispatch et
la confirmation d'une publication réussie. Cette confirmation reste dans la
transaction de l'appelant : le dispatcher ne doit l'exécuter qu'après le succès
de la publication Redis.

Deux index PostgreSQL partiels ciblent les files de travail actives : les jobs
`QUEUED` à dispatcher, ordonnés par création, et les jobs `PROCESSING` dont le
lease doit être surveillé, ordonnés par expiration.

## Configuration

Les configurations héritent de `pydantic-settings.BaseSettings`. Elles lisent
les variables d'environnement suivantes :

| Classe | Variable | Obligatoire | Défaut |
| --- | --- | --- | --- |
| `DatabaseSettings` | `DATABASE_URL` | oui | — |
| `RedisSettings` | `REDIS_URL` | oui | — |
| `StorageSettings` | `AUDIO_STORAGE_PATH` | non | `tmp/audios_buffers` |
| `WorkerSettings` | `WORKER_ID` | oui | — |
| `WorkerSettings` | `WORKER_CONSUMER_GROUP` | non | `transcription-workers` |
| `WorkerSettings` | `WORKER_BLOCK_MILLISECONDS` | non | `5000` |
| `WorkerSettings` | `WORKER_LEASE_SECONDS` | non | `300` |
| `WorkerSettings` | `MAX_ATTEMPTS` | non | `3` |

Le package ne charge pas implicitement de fichier `.env` : le point d'entrée de
chaque application reste responsable de fournir son environnement.

En production avec un volume Docker partagé, `AUDIO_STORAGE_PATH` doit pointer
vers `/data/transcriptions`. La racine reste injectable afin que les tests
puissent utiliser un dossier temporaire et qu'aucun chemin physique ne soit
persisté dans `TranscriptionJob.audio_uri`.

## Services

- `TranscriptionStreams` définit les primitives asynchrones communes au
  dispatcher et aux workers sans contenir leur boucle métier.
- `RedisTranscriptionStreams` publie les jobs FAST dans
  `transcription:fast` et les jobs BATCH dans `transcription:batch`. Le payload
  contient uniquement `job_uuid` et `attempt_count`; le type est porté par le
  stream. Les groupes sont créés depuis `0-0` avec `MKSTREAM`, afin de rendre
  visibles les messages publiés avant leur création.
- `ack_and_delete` utilise nativement `XACKDEL`. Le projet cible Redis 8.10.1,
  tandis que cette commande est disponible à partir de Redis 8.2. La politique
  `KEEPREF` suppose un seul consumer group métier par stream, avec autant de
  consumers concurrents que nécessaire dans ce groupe.
- `autoclaim` expose uniquement la primitive Redis et ne décide ni du retry
  métier ni de la valeur de `TranscriptionJob.attempt_count`.
- Le point de composition qui crée un `TranscriptionStreams` doit appeler
  `aclose()` lors de son arrêt afin de libérer le pool de connexions Redis.
- `WorkerRuntime` orchestre une seule itération commune à FAST et BATCH :
  consommation d'un message, claim PostgreSQL atomique, puis appel du
  `Transcriber` injecté. `run_worker` porte la boucle, la composition et la
  fermeture des ressources communes ; chaque application choisit uniquement
  son `JobType` et son moteur. Le moteur ML pourra ainsi rester chargé entre
  deux messages.
- `PostgresWorkerJobStore` committe le claim dans une transaction courte avant
  de rendre un snapshot détaché au runtime. Aucune transaction PostgreSQL ne
  reste donc ouverte pendant le traitement audio. Il vérifie aussi, avant le
  commit, que le type PostgreSQL correspond au stream attribué au worker ; une
  entrée égarée dans le mauvais stream ne peut donc pas lancer le mauvais
  moteur.
- Un claim refusé couvre aussi bien un UUID inexistant qu'un doublon ou un job
  déjà traité : le runtime acquitte et supprime alors le message sans appeler
  le transcriber. Après un claim réussi, aucun ACK n'est encore effectué, même
  lorsque le fake retourne un résultat ; la persistance du résultat et l'ACK
  final appartiennent à l'étape de finalisation suivante.
- Un payload invalide ou une exception du transcriber reste dans la PEL. Cette
  étape ne définit volontairement ni poison queue, ni retry métier, ni
  `XAUTOCLAIM` automatique.
- `FakeTranscriber` est disponible uniquement depuis le module explicite
  `transcribe_ai_shared.worker.testing`. Les applications refusent de
  l'activer sans un opt-in d'environnement de développement.
- `AudioStorage` définit le contrat synchrone consommé par l'API et les workers.
- `AudioLocation` encapsule l'URI opaque persistée dans
  `TranscriptionJob.audio_uri`.
- `FileSystemAudioStorage` sauvegarde les flux dans
  `{racine}/{job_uuid}/input.{extension}`, puis les ouvre ou les supprime sans
  exposer le chemin physique aux consommateurs. Le flux est d'abord écrit et
  fermé dans un fichier temporaire du dossier UUID, puis publié par renommage
  atomique : le chemin final ne contient donc jamais de fichier partiel et un
  temporaire abandonné reste détectable par la maintenance. Les collisions,
  fichiers absents et emplacements invalides sont signalés par les exceptions
  métier du sous-package `storage`.
- `AudioStorageMaintenance` inventorie les dossiers UUID avec leur date de
  modification UTC et un jeton de révision opaque. La suppression revalide ce
  jeton et refuse les UUID non canoniques, liens symboliques, contenus
  inattendus et dossiers modifiés depuis l'inventaire. Aucun chemin arbitraire
  n'est exposé aux applications chargées de la maintenance.

Une future implémentation `S3Storage` pourra respecter le même contrat sans
modifier l'API ni les workers.
