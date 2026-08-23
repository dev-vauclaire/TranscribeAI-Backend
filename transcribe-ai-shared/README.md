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
- `protocols.py` décrit le `Transcriber` et les ports consommés par le runtime ;
- `postgresql.py` adapte les opérations de claim et de renouvellement de lease
  à des transactions courtes ;
- `runtime.py` orchestre consommation, claim, inférence et confirmation Redis ;
- `completion.py` persiste le résultat et clôture le job dans une transaction
  PostgreSQL unique ;
- `failure_classification.py` traduit les erreurs du moteur en erreurs
  retryables ou permanentes sans conserver leur message ;
- `failure_code.py` borne et valide les codes d'erreur persistables ;
- `failure.py` persiste atomiquement la requeue ou l'échec terminal ;
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
| `WorkerSettings` | `WORKER_AUTOCLAIM_INTERVAL_SECONDS` | non | `60` |
| `WorkerSettings` | `WORKER_AUTOCLAIM_MIN_IDLE_MILLISECONDS` | non | `300000` |
| `WorkerSettings` | `WORKER_LEASE_SECONDS` | non | `300` |
| `WorkerSettings` | `WORKER_HEARTBEAT_SECONDS` | non | `60` |
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
  métier ni de la valeur de `TranscriptionJob.attempt_count`. Chaque worker
  balaie immédiatement puis périodiquement le PEL de son propre stream, un
  message à la fois. `WORKER_AUTOCLAIM_MIN_IDLE_MILLISECONDS` limite seulement
  la fréquence de réattribution Redis : même pour un job BATCH très long,
  l'idle Redis ne prouve jamais un crash. `COUNT 1` évite qu'un worker séquentiel
  ne précharge plusieurs longues inférences ; en contrepartie, chaque worker ne
  nettoie au maximum qu'un ancien message par intervalle configurable.
- Le point de composition qui crée un `TranscriptionStreams` doit appeler
  `aclose()` lors de son arrêt afin de libérer le pool de connexions Redis.
- `WorkerRuntime` orchestre une seule itération commune à FAST et BATCH :
  récupération éventuelle d'un ancien pending ou consommation d'un nouveau
  message, décision PostgreSQL atomique, appel du `Transcriber`, transition
  PostgreSQL terminale ou de retry, puis ACK Redis. `run_worker` porte la
  boucle, la cadence monotone, la composition et la fermeture des ressources
  communes ; chaque application choisit uniquement son `JobType` et son moteur.
  Le balayage n'est pas concurrent de l'inférence, afin que le moteur ML garde
  les ressources du worker pendant son exécution.
- `PostgresWorkerJobStore` committe le claim dans une transaction courte avant
  de rendre un snapshot détaché au runtime. Aucune transaction PostgreSQL ne
  reste donc ouverte pendant le traitement audio. Il vérifie aussi, avant le
  commit, que le type PostgreSQL correspond au stream attribué au worker ; une
  entrée égarée dans le mauvais stream ne peut donc pas lancer le mauvais
  moteur.
- Pendant l'inférence, le runtime renouvelle périodiquement le lease dans une
  transaction courte indépendante. Le renouvellement est protégé par l'UUID,
  le statut `PROCESSING`, le propriétaire, le numéro de tentative et une
  échéance encore valide. Un dernier renouvellement précède la finalisation :
  si PostgreSQL refuse l'un de ces CAS, le résultat n'est ni persisté ni
  acquitté dans Redis. `WORKER_HEARTBEAT_SECONDS` doit être strictement
  inférieur à `WORKER_LEASE_SECONDS`.
- Un claim refusé couvre aussi bien un UUID inexistant qu'un doublon ou un job
  déjà traité. Un job terminal ou une ancienne tentative est acquitté et
  supprimé sans appeler le transcriber. Lors d'un `XAUTOCLAIM`, un job
  `PROCESSING` du bon type et de la tentative courante reste au contraire dans
  le PEL : seul son lease PostgreSQL permet au dispatcher de décider
  ultérieurement si le worker a probablement crashé. Les nouveaux doublons
  conservent leur comportement antérieur d'ACK, puisque le message de la
  tentative active reste déjà dans le PEL. Un UUID absent est acquitté et
  supprimé, car le contrat de publication crée et committe toujours le job
  PostgreSQL avant son message Redis. Le claim compare également
  l'`attempt_count` porté par Redis.
- `TranscriptionCompletionService` ajoute le résultat puis confirme par
  comparaison la tentative toujours détenue par le worker. Les deux écritures
  partagent une même session et un même commit ; un refus du CAS ou une erreur
  antérieure au commit annule la transaction entière. Une erreur pendant le
  commit peut laisser son résultat inconnu de l'appelant : le service remonte
  alors l'échec et le runtime n'effectue aucun ACK. Après un commit confirmé,
  le runtime appelle seulement alors `ack_and_delete`. Une panne à cette
  dernière frontière laisse un job `COMPLETED` et un message pending ; lors de
  sa récupération, le claim est refusé et le message est supprimé sans seconde
  inférence. Le service de finalisation ne connaît pas Redis.
- Une erreur explicite du `Transcriber` est classée retryable ou permanente.
  Une exception inconnue est retryable avec le code générique non sensible
  `TRANSCRIPTION_UNEXPECTED_ERROR` ; son message n'est jamais persisté. Les
  erreurs explicites doivent fournir un code stable en majuscules, chiffres et
  `_`, limité à 128 caractères : un message brut ou un chemin est refusé.
  `TranscriptionFailureService` vérifie le propriétaire et l'`attempt_count`
  de la tentative active. Une erreur retryable avec des exécutions restantes
  replace le job en `QUEUED`, incrémente `attempt_count`, réarme
  `dispatch_required` et libère le lease dans une seule transaction. Une erreur
  permanente ou la dernière exécution autorisée place le job en `FAILED` sans
  redispatch. `MAX_ATTEMPTS` compte l'exécution initiale : avec `3`, les index
  autorisés sont `0`, `1` et `2`.
- L'ancien message Redis est acquitté et supprimé uniquement après le commit de
  cette transition. Le dispatcher publie ensuite un nouveau message pour la
  nouvelle tentative. Une erreur SQL, un commit incertain ou un CAS refusé
  laisse l'ancien message dans la PEL ; aucune stratégie métier ne repose sur
  `XNACK`.
- Un payload invalide reste dans la PEL et l'erreur de désérialisation est
  propagée, qu'il provienne de `XREADGROUP` ou de `XAUTOCLAIM`. Le worker ne peut
  pas l'acquitter aveuglément puisqu'il ne dispose pas d'une identité PostgreSQL
  fiable ; une intervention opérateur reste nécessaire tant qu'une politique
  dédiée de quarantaine n'existe pas. `WorkerRuntime.process_next_pending()`
  porte le balayage unitaire et `run_worker` sa planification automatique.
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
