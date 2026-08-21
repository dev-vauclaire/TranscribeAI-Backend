# Tests d'intégration des workers

La mécanique Redis Streams et le claim PostgreSQL sont communs à FAST et BATCH.
Leurs tests d'intégration vivent donc dans :

```text
transcribe-ai-shared/tests/worker/integration
```

Les futures intégrations propres au moteur FAST seront ajoutées ici lorsque ce
moteur remplacera le fake de développement.
