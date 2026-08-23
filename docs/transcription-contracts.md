# Contrats de transcription

## Port commun

Les deux moteurs implémentent le même port asynchrone :

```python
async def transcribe(
    audio_location: AudioLocation,
) -> TranscriptionOutput: ...
```

`AudioLocation` est une URI opaque fournie par `AudioStorage`.
`TranscriptionOutput` contient :

- `result: dict[str, JsonValue]`, persisté dans le JSONB public ;
- `speaker_count: int | None`, persisté dans une colonne interne.

Le runtime ne connaît pas la forme détaillée du dictionnaire. La dataclass
valide uniquement que `speaker_count` est positif ou nul. Les contraintes de
segments restent donc sous la responsabilité de chaque adaptateur.

## Convention JSON actuelle

Le socle commun produit :

```json
{
  "text": "Bonjour.",
  "language": "fr",
  "segments": [
    {
      "start": 0.0,
      "end": 1.2,
      "text": "Bonjour."
    }
  ]
}
```

Le worker long-form ajoute `speaker_count` à la racine et `speaker` dans chaque
segment :

```json
{
  "text": "Bonjour. Comment allez-vous ?",
  "language": "fr",
  "speaker_count": 2,
  "segments": [
    {
      "start": 0.0,
      "end": 1.2,
      "text": "Bonjour.",
      "speaker": "SPEAKER_00"
    },
    {
      "start": 1.3,
      "end": 2.5,
      "text": "Comment allez-vous ?",
      "speaker": "SPEAKER_01"
    }
  ]
}
```

Cette forme est une convention interne actuelle, pas encore un schéma public
Pydantic détaillé. L'API expose le JSONB comme `dict[str, JsonValue]`. Un
changement incompatible du contenu peut donc casser un client même si le schéma
OpenAPI ne le détecte pas. Avant de faire évoluer ces champs, choisissez
explicitement entre :

- stabiliser un schéma public versionné ;
- conserver un résultat opaque et versionner son contenu autrement.

Les colonnes internes `speaker_count`, `model_name` et `model_version` ne sont
pas exposées séparément par `GET /api/transcriptions/{job_uuid}`. Les deux
dernières ne sont actuellement pas renseignées par la finalisation.

## Règles FAST

Faster-Whisper utilise toujours :

- la langue `fr` ;
- `vad_filter=true` ;
- le modèle `large-v3-turbo` par défaut.

Chaque segment est mappé vers `start`, `end` et `text`. Le texte global est la
concaténation des textes bruts des segments, puis un `strip`. L'adaptateur
convertit les timestamps en `float`, mais ne vérifie actuellement ni leur
finitude, ni leur ordre, ni `end >= start`.

Une sortie vide reste valide avec un texte vide et une liste de segments vide.
`speaker_count` vaut `None` et aucun champ `speaker` n'est produit.

## Règles LONG_FORM_DIARIZATION

WhisperX utilise toujours la langue `fr`, puis exécute :

```text
décodage -> ASR -> alignement -> diarisation -> attribution -> normalisation
```

La normalisation :

- exige des timestamps numériques finis ;
- refuse `start < 0`, `end < start` et un ordre de début décroissant ;
- retire les segments dont le texte devient vide ;
- normalise un speaker vide vers `null` ;
- fusionne deux segments adjacents du même speaker connu ;
- ne fusionne jamais à travers un segment dont le speaker est absent ;
- calcule `speaker_count` à partir des labels non nuls distincts.

Le début du premier segment et la fin maximale des segments fusionnés sont
conservés. Les textes sont joints avec un espace. Une sortie ASR ou alignée sans
segment retourne une sortie vide sans exécuter les étapes suivantes inutiles.

## Exécution bloquante

Les bibliothèques ML et le stockage filesystem sont synchrones. Chaque adapter
déporte l'ensemble du pipeline dans `asyncio.to_thread`, y compris l'itération
lazy des segments Faster-Whisper.

Cette isolation laisse l'event loop disponible pour le heartbeat. Elle ne
réserve toutefois pas automatiquement du CPU ou de la VRAM et ne peut pas tuer
immédiatement un calcul natif après annulation. Le dimensionnement appartient au
déploiement.

## Classification des erreurs

Les erreurs explicites portent un code stable. Une exception inconnue est
classée retryable avec `TRANSCRIPTION_UNEXPECTED_ERROR`. Son message brut n'est
jamais persisté.

Faster-Whisper produit actuellement :

- `FASTER_WHISPER_INFERENCE_FAILED` : retryable pour l'appel ou l'itération du
  moteur.

WhisperX produit :

- `WHISPERX_AUDIO_DECODE_FAILED` : permanente ;
- `WHISPERX_INFERENCE_FAILED` : retryable ;
- `WHISPERX_ALIGNMENT_FAILED` : retryable ;
- `WHISPERX_DIARIZATION_FAILED` : retryable ;
- `WHISPERX_SPEAKER_ASSIGNMENT_FAILED` : permanente ;
- `WHISPERX_INVALID_OUTPUT` : permanente.

Les erreurs de stockage ou de mapping non explicitement converties passent par
la classification générique retryable. Toute nouvelle classification doit être
testée sans persister de chemin local, payload ou message sensible.
