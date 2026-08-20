# Tests du package partagé

Les tests unitaires sont isolés des services externes. Ils peuvent utiliser des
doubles ou un dossier temporaire fourni par pytest pour valider un composant
filesystem local :

```shell
uv run pytest -m unit transcribe-ai-shared/tests
```

Les tests d'intégration utilisent plusieurs composants ou des conteneurs
PostgreSQL et Redis éphémères. Ils nécessitent un moteur Docker local accessible
par Testcontainers.

Pour lancer les tests d'intégration, placez vous à la racine du projet
et exécutez les commandes suivantes :

```shell
uv run pytest -m integration transcribe-ai-shared/tests
```

Les conteneurs sont démarrés et arrêtés par les fixtures de session. La suite
Redis vide la base du conteneur avant et après chaque test afin d'isoler les
deux streams logiques et leurs consumer groups.
