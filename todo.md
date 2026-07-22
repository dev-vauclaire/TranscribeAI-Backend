# Feuille de route — mono worker et package partagé

Priorités :

- **P0** : nécessaire avant de considérer l'itération actuelle comme stable ;
- **P1** : nécessaire avant une première mise en production ;
- **P2** : durcissement et évolutions ultérieures.

Une tâche est terminée lorsque le comportement est implémenté, testé et documenté si son contrat n'est pas évident.

## Décisions déjà prises

- [x] Injecter la factory de sessions, le service Redis et le client Whisper dans le worker.
- [x] Envoyer l'audio à Whisper sous forme de flux, sans charger le fichier entier en mémoire (`6ee6ed4`).
- [x] Stocker dans `Job.filename` un nom relatif au dossier géré par `AudioManager`, et non un chemin absolu.
- [x] Conserver provisoirement la suppression de l'audio à la fin du traitement, y compris après un échec géré.
- [x] Considérer le schéma PostgreSQL actuel comme un schéma initial encore modifiable : aucune migration de transition n'est requise avant son gel.

## P0 — Stabiliser l'itération actuelle

### Démarrage et cycle de vie du mono worker

- [x] Fermer la connexion utilisée pour vérifier PostgreSQL dans `mono_voice_worker/main.py`.
  - Utiliser un context manager autour de `engine.connect()` et exécuter idéalement `SELECT 1`.
  - Placer tout le cycle de vie qui suit la création de l'engine dans un `try/finally` afin que `engine.dispose()` soit aussi appelé si Redis ou Whisper échoue au démarrage.
- [x] Définir explicitement la politique de démarrage actuelle : échec immédiat si PostgreSQL, Redis ou Whisper est indisponible.
  - Documenter que le redémarrage/retry sera pris en charge plus tard par l'orchestrateur.
  - Ne pas utiliser le timeout de transcription de 600 secondes pour le healthcheck Whisper ; ajouter un timeout court et dédié.
- [x] Renommer les méthodes de production `test_connection` en `check_connection` ou `check_health`.
  - Leur contrat doit être clair : `-> None` et exception en cas d'échec, ou `-> bool` avec un vrai `return`.
  - Ajouter un timeout de connexion Redis et conserver l'exception d'origine avec `raise ... from error`.
- [x] Recréer `apps/mono-voice-worker/.env.example` avec les noms réellement attendus par `WorkerMonoVoiceSettings`.
  - Inclure au minimum `PG_DSN`, `REDIS_DSN`, `AUDIO_FOLDER_PATH`, `WHISPER_SERVICE_URL`, `REDIS_QUEUE_NAME_MONO_VOICE` et `WORKER_LOOP_SLEEP_TIME`.
  - Ne mettre aucun secret réel dans ce fichier.
- [x] Nettoyer le bootstrap : imports ordonnés, annotations utiles, lignes terminées et suppression des imports inutilisés (`os` dans `config.py`).
- [x] Vérifier le point d'entrée depuis la racine du workspace :
  - `uv run --package mono-voice-worker mono-voice-worker`

### Dépendances directes explicites

- [ ] Déclarer `pydantic` dans `apps/mono-voice-worker/pyproject.toml`.
  - Le worker importe directement `BaseModel` et `Field` : il ne doit pas dépendre du fait que `transcribe-ai-shared` installe Pydantic transitivement.
- [ ] Exporter `SessionFactory` depuis l'API publique `transcribe_ai_shared.database`, puis typer le worker avec cet alias.
  - Retirer les imports directs de `sqlalchemy` du worker et de son `main.py` s'ils ne servent qu'au typage.
  - Si le worker continue à importer SQLAlchemy directement, déclarer alors `sqlalchemy` dans son propre `pyproject.toml`.
- [ ] Vérifier qu'une installation isolée du worker contient toutes ses dépendances directes et que ses imports fonctionnent sans dépendance implicite.

Règle à appliquer : **si un package contient `import librairie`, cette librairie doit être déclarée dans son `pyproject.toml`, sauf si l'import est supprimé derrière une abstraction du package partagé**. Les modules de la bibliothèque standard ne sont pas concernés.

### Contrat de stockage audio et schéma initial

- [ ] Remplacer dans les tests les valeurs comme `filename="/audio/job.wav"` par un nom relatif comme `filename="job.wav"`.
- [ ] Ajouter le test aller-retour du contrat : sauvegarde par `AudioManager` → valeur retournée persistée dans `Job.filename` → réouverture par un autre `AudioManager` configuré avec le même dossier.
- [ ] Vérifier que les chemins absolus et les traversées (`../`) sont refusés pour la sauvegarde, la lecture et la suppression.
- [ ] Mettre à jour tous les consommateurs encore basés sur `Job.file_path` ou `Job.end_at`.
  - Le multi worker utilise encore `job.file_path`.
  - l'API utilise encore `job.end_at` dans les contrôleurs de transcription.
- [ ] Documenter la commande de recréation de la base locale pendant cette phase de conception destructive du schéma.
- [ ] Geler les noms et types finaux du modèle `Job` avant d'initialiser Alembic.

### Erreurs fonctionnelles du mono worker

- [ ] Introduire une exception applicative `WhisperClientError` produite par `ClientWhisper`.
  - Y traduire les erreurs HTTP/réseau et les erreurs de validation Pydantic de la réponse.
  - Le worker doit dépendre de ce contrat et non de `requests.exceptions.RequestException`.
  - Vérifier qu'une réponse Whisper invalide place bien le job en `FAILED` au lieu de le laisser en `PROCESSING`.
- [ ] Initialiser les identifiants utilisés dans les messages d'erreur avant le `try`, afin qu'une erreur de lecture ou de décodage du message Redis ne masque pas l'erreur initiale.
- [ ] Définir le comportement pour un message Redis qui n'est pas un UUID valide : log explicite, absence d'accès BDD et poursuite contrôlée du worker.
- [ ] Ajouter un timeout à `ClientWhisper.cancel_transcription` ou retirer cette méthode tant que cette fonctionnalité n'est pas utilisée.

### Tests minimaux de validation

- [ ] Remplacer les fichiers placeholders `tests/units/test.py` et `tests/integrations/test.py` par des fichiers nommés selon le comportement testé.
- [ ] Tester `WorkerMonoVoiceSettings` : héritage des variables partagées, variables propres au worker et absence des valeurs sensibles dans `repr`.
- [ ] Tester le worker avec des doubles injectés :
  - succès : `PENDING → PROCESSING → COMPLETED`, résultat persisté et audio supprimé ;
  - job inconnu ;
  - fichier absent ;
  - erreur HTTP Whisper ;
  - payload Whisper invalide ;
  - vérification du rollback d'une unité de transaction.
- [ ] Tester le bootstrap : chaque healthcheck est appelé, une erreur empêche le démarrage de la boucle et l'engine est libéré.
- [ ] Exécuter la suite unitaire complète et les tests d'intégration PostgreSQL renommés.

## P1 — Obtenir un worker et un package partagé propres

### Conception du worker

- [ ] Injecter aussi `AudioManager` dans `WorkerMonoVoice` depuis le point de composition `main.py`.
  - Le worker ne doit pas créer lui-même une dépendance d'infrastructure cachée.
- [ ] Définir des `Protocol` locaux et minimaux pour la file, le client de transcription et le stockage audio.
  - Le worker dépend ainsi des opérations qu'il utilise, pas des classes concrètes Redis/HTTP/disque.
- [ ] Séparer une itération métier de la boucle infinie.
  - `run_once()` traite un message et retourne un résultat exploitable.
  - La boucle, la temporisation et l'arrêt restent dans `main.py`.
- [ ] Remplacer les `print` par des logs structurés contenant au minimum le `job_uuid`, le statut et la durée.
- [ ] Gérer `SIGINT` et `SIGTERM` pour terminer le traitement courant puis libérer proprement les ressources.
- [ ] Ajouter des tests d'intégration avec PostgreSQL, Redis et un faux serveur Whisper local.

### API publique de `transcribe-ai-shared`

- [ ] Restaurer une API publique explicite dans `transcribe_ai_shared/database/__init__.py`.
  - Exporter les modèles, `JobRepository`, `DatabaseConfig`, `SessionFactory`, `create_db_engine`, `create_session_factory` et `transaction`.
  - Définir `__all__` dans les modules publics et ajouter un test de smoke des imports supportés.
  - Éviter d'obliger les consommateurs à connaître l'arborescence interne du package.
- [ ] Corriger l'annotation du context manager `transaction` : utiliser `Generator[Session, None, None]` au lieu de `Iterator[Session]` avec `@contextmanager`.
- [ ] Documenter dans le README du package partagé :
  - qui crée et détruit l'engine ;
  - pourquoi on injecte une `SessionFactory` ;
  - qu'une transaction possède une session courte et qu'elle commit/rollback automatiquement ;
  - le contrat relatif de `Job.filename` et `AudioManager` ;
  - les imports publics garantis.
- [ ] Préciser les types et contrats de `RedisQueueService` (`push_job`, `pop_job`, healthcheck et timeouts).
- [ ] Ajouter les tests unitaires Redis avec un client injecté ou simulé, puis un test d'intégration séparé avec une vraie instance Redis.
- [ ] Vérifier que le package partagé ne contient que des concepts réellement communs à plusieurs applications ; conserver les DTO Whisper dans le worker tant qu'ils sont spécifiques à ce service.

### Baseline Alembic après gel du modèle

- [ ] Initialiser Alembic seulement après validation du modèle initial final.
- [ ] Générer une migration initiale complète depuis les métadonnées SQLAlchemy finales.
- [ ] Faire appliquer cette migration aux nouvelles bases ; recréer les bases locales existantes au lieu d'écrire des migrations intermédiaires pour les renommages actuels.
- [ ] Réserver `Base.metadata.create_all()` aux tests, puis vérifier le démarrage sur une base initialisée par Alembic.
- [ ] Ajouter en CI un test `upgrade head` sur une base PostgreSQL vide.

### Qualité et documentation

- [ ] Ajouter Ruff et un vérificateur de types, puis corriger d'abord le périmètre worker/package partagé.
- [ ] Ajouter un README au mono worker avec configuration, commande `uv`, dépendances externes et politique de démarrage.
- [ ] Activer les hooks pre-commit dans un commit séparé une fois l'itération fonctionnelle stabilisée.
- [ ] Faire exécuter en CI les tests unitaires à chaque changement et les tests d'intégration avec services dédiés.

## P2 — Fiabilité et production

- [ ] Définir la stratégie quand PostgreSQL ou Redis devient indisponible pendant l'exécution : backoff borné, reconnexion, logs et métriques.
- [ ] Définir la récupération des jobs après un crash Redis ou worker (acknowledgement, file de traitement, retry et dead-letter queue).
- [ ] Formaliser la rétention et la suppression des fichiers audio en cohérence avec la stratégie de retry.
- [ ] Ajouter les healthchecks Docker et les politiques de redémarrage.
- [ ] Fixer précisément les versions de PostgreSQL et Redis.
- [ ] Retirer les `container_name` pour permettre le scaling.
- [ ] Segmenter les réseaux Docker selon les responsabilités.
- [ ] Mettre en place logs structurés, métriques et alertes.
- [ ] Supprimer `API_SECRET_KEY` du frontend et vérifier le modèle d'authentification attendu.
- [ ] Migrer l'API Flask vers FastAPI dans une itération dédiée, sans la coupler au nettoyage du worker.
