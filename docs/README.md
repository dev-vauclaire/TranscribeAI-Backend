# Documentation transverse

Ce dossier contient le contexte stable qui dépasse la responsabilité d'une
application.

- [Architecture](architecture.md) : composants, dépendances et propriété des
  données.
- [Workflows](workflows.md) : cycle de vie, transactions et concurrence.
- [Contrats de transcription](transcription-contracts.md) : sortie commune et
  classification des erreurs ML.
- [Développement](development.md) : installation, commandes et stratégie de
  tests.
- [Exploitation](operations.md) : processus, healthchecks, volumes et logs.

Les détails propres à une application restent dans son README. Les instructions
pour les agents de développement vivent dans les fichiers `AGENTS.md` et ne
doivent pas être dupliquées ici.

Lorsqu'une décision change un invariant transverse, le code, les tests et le
document concerné doivent être modifiés dans la même étape.
