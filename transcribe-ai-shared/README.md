# Transcribe AI Shared

Ce package contient les composants partagés par les applications backend :
configuration, accès SQLAlchemy, modèle `Job`, stockage audio et file Redis.

## Cycle de vie SQLAlchemy

L'application crée un seul engine au démarrage, puis injecte une
`SessionFactory` dans les services qui en ont besoin. Chaque unité de travail
ouvre une transaction courte :

```python
from transcribe_ai_shared.database import JobRepository, JobStatus, transaction

with transaction(session_factory) as session:
    repository = JobRepository(session)
    repository.update_status(job_id, JobStatus.PROCESSING)
```

Le context manager commit en cas de succès, rollback en cas d'exception et
ferme la session. L'application reste responsable de `engine.dispose()` à
l'arrêt.

## Contrat de stockage audio

`Job.filename` contient un nom relatif au dossier donné à `AudioManager`. Il ne
contient jamais le chemin absolu du fichier. `AudioManager` refuse tout accès
qui sortirait de son dossier racine.

## Schéma initial

Le modèle de données est encore en phase d'initialisation. Les changements
actuels ne nécessitent donc pas de migrations intermédiaires. Pour recréer les
services de développement locaux, cette commande supprime leurs données :

```shell
docker compose down --volumes
docker compose up -d postgres redis
```

Cette commande ne doit jamais être utilisée sur une base partagée ou de
production. Alembic sera initialisé avec une migration de référence après le
gel des modèles.
