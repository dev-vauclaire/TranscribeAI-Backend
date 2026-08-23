# Guide d'exploitation

## Topologie des processus

Le déploiement doit distinguer trois durées de vie :

- long-lived : API, worker FAST et worker long-form ;
- one-shot planifié : dispatcher et maintenance ;
- one-shot de déploiement : migration.

Ordre recommandé :

1. démarrer PostgreSQL et Redis ;
2. exécuter une seule instance de migration jusqu'à succès ;
3. démarrer l'API et les workers ;
4. planifier les cycles du dispatcher ;
5. planifier la maintenance avec une fréquence adaptée à la période de grâce.

Le Compose racine matérialise cet ordre avec les conditions
`service_healthy` et `service_completed_successfully`. Le dispatcher appartient
au profil `operations` et maintenance au profil `maintenance` : aucun des deux
n'est redémarré en boucle par Compose.

Redis n'est pas une dépendance de l'API. Si Redis est indisponible, l'API doit
continuer à créer des jobs `QUEUED` avec `dispatch_required=true`. Le dispatcher
les publiera après rétablissement.

## Healthchecks

### API

- `/health/live` prouve seulement que le processus ASGI répond ;
- `/health/ready` exécute un `SELECT 1` PostgreSQL borné ;
- Redis n'est jamais vérifié ;
- le volume audio et `ffprobe` ne sont actuellement pas vérifiés.

Une API peut donc être ready tout en retournant `503` sur un upload si le volume
ou `ffprobe` est indisponible. Le Dockerfile déclare un `HEALTHCHECK` utilisant
la bibliothèque standard Python contre `/health/ready`, sans installer `curl`.

### Workers

Les images utilisent la commande `worker-healthcheck`. Elle vérifie PostgreSQL
et Redis sans démarrer de serveur HTTP et sans charger le modèle.

Cette sonde ne prouve pas :

- que le modèle ML est chargé ;
- que la boucle consomme encore ;
- qu'une inférence progresse ;
- que le volume audio est lisible.

Docker marque un conteneur unhealthy mais ne le redémarre pas de lui-même. Une
politique de restart ou une supervision externe reste nécessaire.

### Processus one-shot

Leur healthcheck est leur code de sortie et la présence d'exécutions récentes :

- `0` : cycle terminé sans erreur ;
- `1` : configuration, dépendance ou traitement en échec ;
- `130` : interruption explicite.

N'ajoutez pas FastAPI à ces processus. L'orchestrateur doit surveiller leurs
Jobs, leur durée et l'absence éventuelle de nouvelles exécutions.

## Planification

### Dispatcher

Un cycle traite successivement un batch de recovery, un batch de
réconciliation et un batch de dispatch. `DISPATCHER_BATCH_SIZE` s'applique à
chaque phase. Un job récupéré ou réarmé peut donc être publié pendant le même
cycle.

La fréquence du scheduler doit être nettement inférieure au délai métier
acceptable avant traitement. Sans backlog, un job devient éligible après le
timeout puis attend entre zéro et une période du scheduler. Sa borne haute
approximative est donc :

```text
RECONCILIATION_TIMEOUT_SECONDS + période du scheduler
```

Un backlog supérieur au batch ajoute plusieurs cycles. Surveillez les compteurs
`selected_count`, `published_count`, `requeued_count`, `rearmed_count` et
`error_count` présents dans les logs de résumé.

### Maintenance

`MAINTENANCE_CLEANUP_GRACE_PERIOD_SECONDS` vaut une heure par défaut. La tâche
doit être moins fréquente que nécessaire pour l'exploitation, sans devenir un
daemon. Une indisponibilité PostgreSQL doit produire zéro suppression.

## Volumes audio

Le même volume doit être :

- inscriptible par l'API ;
- lisible par les deux workers ;
- inscriptible par la maintenance pour permettre les suppressions.

Le chemin de production attendu est `/data/transcriptions`. Les permissions
des images Compose sont stabilisées avec l'UID/GID non-root `10001:10001`.
L'API et maintenance préparent le mountpoint et l'utilisent en
lecture/écriture ; les workers le montent en lecture seule. Pour un bind mount,
les permissions du répertoire hôte doivent néanmoins être préparées pour cet
UID/GID, car le `chown` de l'image ne s'applique pas au chemin hôte.

Les workers montent idéalement ce volume en lecture seule. Seules l'API et la
maintenance ont besoin d'y écrire.

## Caches des modèles

Les modèles sont trop volumineux pour être téléchargés à chaque redémarrage.
Les overrides CPU/GPU montent des volumes persistants :

- Faster-Whisper : cache Hugging Face sous `/models` dans l'image ;
- WhisperX : sous-caches `whisperx`, `alignment` et `pyannote` sous
  `WORKER_TRANSCRIBER_MODEL_DIRECTORY`.

Chaque replica charge sa propre copie du modèle en RAM ou VRAM. Le partage du
cache disque ne partage pas la mémoire du modèle. Mesurez le temps de démarrage,
l'espace disque et la mémoire avant de fixer le nombre de replicas.

Les noms de modèles distants ne garantissent pas toujours un artefact immuable.
Pour une reproductibilité stricte, une future étape devra pinner ou promouvoir
les artefacts modèles dans un registre maîtrisé.

Le Compose de base cible `runtime-fake`. Les Dockerfiles exposent séparément
`runtime-cpu` et `runtime-gpu` ; l'override choisi fixe le backend, le device et
le compute type. Le token Pyannote n'est jamais passé au build et doit être
injecté seulement au runtime.

## Smoke test Docker

`scripts/compose-smoke.sh` crée un projet Compose et des ports uniques, puis :

1. construit les images de toutes les applications ;
2. démarre PostgreSQL et Redis avec leurs healthchecks ;
3. applique Alembic sur une base vide ;
4. attend `/health/ready` ;
5. crée et termine un job FAST puis un job LONG_FORM_DIARIZATION ;
6. vérifie l'ACK/suppression Redis et les événements JSON de corrélation ;
7. exécute maintenance une fois et détruit uniquement les ressources du test.

Il utilise les backends factices et ne valide donc ni CUDA, ni la qualité ML.
Sur échec, il affiche les derniers logs avant le teardown borné au projet du
test.

## Redis

Le projet cible Redis 8.10.1 pour `XACKDEL`. Les streams sont :

- `transcription:fast` ;
- `transcription:long-form-diarization`.

Le contrat `XACKDEL KEEPREF` suppose actuellement un seul consumer group métier
par stream. N'ajoutez pas un second groupe sans revoir la politique de
suppression des entrées.

Redis peut contenir des doublons. Il ne faut pas les supprimer sur la seule base
de leur présence : PostgreSQL décide si le message est encore exploitable.

## Messages invalides dans le PEL

Un payload illisible ou incomplet reste pending et peut faire quitter le worker
avec un code non nul. Aucune dead-letter queue n'existe encore.

Procédure conservatrice :

1. relever le stream, le groupe et le `redis_message_id` ;
2. inspecter le payload sans le copier dans des logs publics ;
3. rechercher le `job_uuid` dans PostgreSQL lorsqu'il est récupérable ;
4. décider explicitement si l'entrée peut être supprimée ou doit être réparée ;
5. documenter l'intervention.

Ne transformez pas ce cas en retry métier Redis et n'utilisez pas `XNACK`.

## Arrêt et crash

Un crash avant une transition PostgreSQL durable laisse le message pending. Un
crash après le commit et avant l'ACK est résolu par la redelivery sans seconde
inférence.

Une tâche Python annulée ne peut pas interrompre immédiatement un calcul natif
déjà lancé dans `asyncio.to_thread`. Accordez aux workers un délai de terminaison
adapté à leur profil, mais comptez sur les leases et le recovery pour les arrêts
forcés.

Le comportement détaillé est décrit dans
[workflows.md](workflows.md).

## Logs structurés

Chaque ligne applicative est un objet JSON contenant au minimum :

- `timestamp` ;
- `level` ;
- `logger` ;
- `service` ;
- `event`.

Les champs de corrélation sont ajoutés lorsqu'ils sont disponibles :

- `job_uuid` ;
- `attempt_count` ;
- `worker_id` ;
- `redis_message_id`.

Les événements importants comprennent notamment `job_created`,
`dispatch_started`, `dispatched`, `dispatch_failed`, `job_claimed`,
`claim_rejected`, `transcription_started`, `heartbeat_lost`,
`retry_scheduled`, `job_completed`, `job_failed`,
`redis_message_acked`, `reconciliation` et `cleanup`.

Ne journalisez jamais :

- l'audio ou son contenu ;
- une transcription complète ;
- un payload HTTP ou Redis complet ;
- un token, mot de passe ou URL de connexion ;
- le message brut d'une exception susceptible de contenir ces valeurs.

## Secrets et exposition HTTP

Les URL PostgreSQL, URL Redis et tokens Hugging Face doivent provenir du
gestionnaire de secrets du déploiement. Les exemples versionnés utilisent
uniquement des valeurs locales factices.

L'API ne possède actuellement aucun mécanisme d'authentification ou
d'autorisation. Un UUID difficile à deviner n'est pas un contrôle d'accès. Ne
l'exposez pas sur un réseau non fiable sans ajouter cette protection et les
tests associés.
