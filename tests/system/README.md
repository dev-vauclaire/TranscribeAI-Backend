# Tests système

Cette suite valide le workflow transverse sans moteur ML réel :

```text
API HTTP
→ stockage filesystem et PostgreSQL
→ dispatcher
→ Redis Streams
→ worker runtime et FakeTranscriber
→ résultat PostgreSQL puis ACK Redis
→ consultation HTTP
```

Les parcours FAST et LONG_FORM_DIARIZATION utilisent les composants
applicatifs réels. Le scénario FAST ajoute aussi un message Redis dupliqué pour
vérifier qu'un seul worker obtient le claim PostgreSQL et appelle le
transcriber.

## Exécution

Depuis la racine du workspace :

```bash
uv run pytest -m system tests/system
```

Docker doit être accessible pour PostgreSQL et Redis Testcontainers. Le binaire
`ffprobe` doit également être installé afin que l'API valide le WAV réel.

Chaque workflow possède un timeout global. Les attentes éventuelles utilisent
un polling court avec une deadline monotone, sans temporisation longue fixe.
