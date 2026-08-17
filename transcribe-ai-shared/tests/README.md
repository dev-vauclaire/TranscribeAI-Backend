# Tests du package partagé

Les tests unitaires utilisent des doubles et n'accèdent à aucun service ou
fichier réel :

```shell
uv run pytest -m unit transcribe-ai-shared/tests
```

Les tests d'intégration utilisent un dossier temporaire réel ainsi que des
conteneurs PostgreSQL et Redis éphémères. Ils nécessitent un moteur Docker
local accessible par Testcontainers.

Pour lancer les tests d'intégration, placez vous à la racine du projet
et exécutez les commandes suivantes :

```shell
uv run pytest -m integration transcribe-ai-shared/tests
```

Les conteneurs sont démarrés et arrêtés par les fixtures de session. La suite
Redis crée en plus une file unique par test et la supprime après utilisation.
