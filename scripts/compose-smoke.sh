#!/usr/bin/env bash

set -Eeuo pipefail

repository_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
smoke_directory="$(mktemp -d /tmp/transcribe-ai-compose-smoke.XXXXXX)"
project_name="transcribe-ai-smoke-${$}"
compose=(
  docker compose
  --project-directory "${repository_root}"
  --project-name "${project_name}"
  --file "${repository_root}/docker-compose.yml"
)

cleanup() {
  local exit_code=$?
  if ((exit_code != 0)); then
    echo "Le smoke test a échoué ; derniers logs Compose :" >&2
    "${compose[@]}" logs --no-color --tail=200 >&2 || true
  fi
  "${compose[@]}" down --volumes --remove-orphans --rmi local \
    >/dev/null 2>&1 || true
  if [[ "${smoke_directory}" == /tmp/transcribe-ai-compose-smoke.* ]]; then
    rm -rf -- "${smoke_directory}"
  fi
  return "${exit_code}"
}
trap cleanup EXIT

fail() {
  echo "ERREUR: $*" >&2
  return 1
}

wait_for_migration() {
  local container_id state deadline
  container_id="$("${compose[@]}" ps --all --quiet migration)"
  [[ -n "${container_id}" ]] || fail "le conteneur migration est absent"
  deadline=$((SECONDS + 45))

  while ((SECONDS < deadline)); do
    state="$(docker inspect --format '{{.State.Status}} {{.State.ExitCode}}' "${container_id}")"
    case "${state}" in
      "exited 0") return 0 ;;
      exited\ *) fail "la migration a terminé avec l'état ${state}" ;;
    esac
    sleep 0.2
  done
  fail "la migration n'a pas terminé dans le délai imparti"
}

wait_for_api() {
  local deadline=$((SECONDS + 45))
  while ((SECONDS < deadline)); do
    if curl --fail --silent --show-error \
      "http://127.0.0.1:${API_PUBLISHED_PORT}/health/ready" \
      >"${smoke_directory}/ready.json" 2>/dev/null; then
      python3 - "${smoke_directory}/ready.json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as response:
    payload = json.load(response)
if payload != {"status": "ready"}:
    raise SystemExit(1)
PY
      return 0
    fi
    sleep 0.2
  done
  fail "l'API n'est pas devenue ready dans le délai imparti"
}

post_job() {
  local job_type=$1 response_file header_file job_uuid
  response_file="${smoke_directory}/${job_type}.json"
  header_file="${smoke_directory}/${job_type}.headers"

  curl --fail-with-body --silent --show-error \
    --dump-header "${header_file}" \
    --output "${response_file}" \
    --request POST "http://127.0.0.1:${API_PUBLISHED_PORT}/api/transcriptions" \
    --form "audio_file=@${smoke_directory}/input.wav;type=audio/wav;filename=input.wav" \
    --form "type=${job_type}"

  job_uuid="$(python3 - "${response_file}" <<'PY'
import json
import sys
import uuid

with open(sys.argv[1], encoding="utf-8") as response:
    payload = json.load(response)
if payload.get("status") != "QUEUED":
    raise SystemExit(f"réponse de création inattendue: {payload!r}")
print(uuid.UUID(payload["job_uuid"]))
PY
)"

  grep -Fqi "location: /api/transcriptions/${job_uuid}" "${header_file}" \
    || fail "l'en-tête Location est invalide pour ${job_type}"
  printf '%s\n' "${job_uuid}"
}

wait_for_completion() {
  local job_uuid=$1 response_file status deadline
  response_file="${smoke_directory}/${job_uuid}.completed.json"
  deadline=$((SECONDS + 30))

  while ((SECONDS < deadline)); do
    if curl --fail --silent --show-error \
      "http://127.0.0.1:${API_PUBLISHED_PORT}/api/transcriptions/${job_uuid}" \
      >"${response_file}" 2>/dev/null; then
      status="$(python3 - "${response_file}" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as response:
    print(json.load(response).get("status", ""))
PY
)"
      if [[ "${status}" == "COMPLETED" ]]; then
        python3 - "${response_file}" "${job_uuid}" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as response:
    payload = json.load(response)
expected = {
    "job_uuid": sys.argv[2],
    "status": "COMPLETED",
    "result": {"text": "fake transcription"},
}
if payload != expected:
    raise SystemExit(f"réponse finale inattendue: {payload!r}")
PY
        return 0
      fi
    fi
    sleep 0.2
  done
  fail "le job ${job_uuid} n'est pas COMPLETED dans le délai imparti"
}

assert_log_event() {
  local log_file=$1 event=$2
  grep -Fq "\"event\":\"${event}\"" "${log_file}" \
    || fail "l'événement ${event} est absent de ${log_file}"
}

command -v docker >/dev/null || fail "docker est absent"
command -v curl >/dev/null || fail "curl est absent"
command -v python3 >/dev/null || fail "python3 est absent"
docker info >/dev/null

readarray -t published_ports < <(python3 <<'PY'
import socket

sockets = []
try:
    for _ in range(3):
        current = socket.socket()
        current.bind(("127.0.0.1", 0))
        sockets.append(current)
    for current in sockets:
        print(current.getsockname()[1])
finally:
    for current in sockets:
        current.close()
PY
)

export API_PUBLISHED_PORT="${published_ports[0]}"
export POSTGRES_PUBLISHED_PORT="${published_ports[1]}"
export REDIS_PUBLISHED_PORT="${published_ports[2]}"
export POSTGRES_DB="transcribe_ai_smoke"
export POSTGRES_USER="transcribe_ai_smoke"
export POSTGRES_PASSWORD
POSTGRES_PASSWORD="$(python3 -c 'import secrets; print(secrets.token_hex(16))')"
export DATABASE_URL="postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@postgres:5432/${POSTGRES_DB}"
export REDIS_URL="redis://redis:6379/0"
export WORKER_BLOCK_MILLISECONDS=250
export WORKER_HEARTBEAT_SECONDS=5
export WORKER_LEASE_SECONDS=30

echo "Validation de la configuration Compose"
"${compose[@]}" config --quiet

echo "Build des images applicatives"
"${compose[@]}" build \
  migration \
  api \
  dispatcher \
  worker-fast \
  worker-long-form-diarization \
  maintenance

for application_service in \
  migration api dispatcher worker-fast worker-long-form-diarization maintenance; do
  application_image="${project_name}-${application_service}"
  configured_user="$(docker image inspect --format '{{.Config.User}}' \
    "${application_image}")"
  case "${configured_user}" in
    "" | root | 0 | 0:0) fail "l'image ${application_service} s'exécute en root" ;;
  esac
done

echo "Démarrage de PostgreSQL et Redis"
"${compose[@]}" up --detach --wait postgres redis

echo "Migration d'une base PostgreSQL vide"
"${compose[@]}" up --detach --no-deps migration
wait_for_migration
"${compose[@]}" logs --no-color migration >"${smoke_directory}/migration.log"
assert_log_event "${smoke_directory}/migration.log" migration
grep -Fq '"action":"completed"' "${smoke_directory}/migration.log" \
  || fail "la migration n'a pas journalisé son succès"

echo "Démarrage de l'API et des workers avec les transcripteurs factices"
"${compose[@]}" up --detach --no-deps \
  api worker-fast worker-long-form-diarization
wait_for_api

for worker_service in worker-fast worker-long-form-diarization; do
  [[ -n "$("${compose[@]}" ps --status running --quiet "${worker_service}")" ]] \
    || fail "${worker_service} n'est pas en cours d'exécution"
  "${compose[@]}" exec -T "${worker_service}" worker-healthcheck
done

"${compose[@]}" exec -T worker-fast python -c \
  'import importlib.util; names=("faster_whisper", "nvidia", "onnxruntime"); raise SystemExit(1 if any(importlib.util.find_spec(name) for name in names) else 0)' \
  || fail "l'image fake FAST contient encore le moteur ML"
"${compose[@]}" exec -T worker-long-form-diarization python -c \
  'import importlib.util; names=("torch", "whisperx", "pyannote"); raise SystemExit(1 if any(importlib.util.find_spec(name) for name in names) else 0)' \
  || fail "l'image fake long-form contient encore la stack ML"

python3 - "${smoke_directory}/input.wav" <<'PY'
import sys
import wave

with wave.open(sys.argv[1], "wb") as audio:
    audio.setnchannels(1)
    audio.setsampwidth(2)
    audio.setframerate(16_000)
    audio.writeframes(b"\x00\x00" * 1_600)
PY

echo "Création des jobs FAST et LONG_FORM_DIARIZATION"
fast_job_uuid="$(post_job FAST)"
long_job_uuid="$(post_job LONG_FORM_DIARIZATION)"

for job_uuid in "${fast_job_uuid}" "${long_job_uuid}"; do
  "${compose[@]}" exec -T api python -c \
    'from pathlib import Path; import sys; matches=list(Path("/data/transcriptions", sys.argv[1]).glob("input.*")); raise SystemExit(0 if len(matches) == 1 else 1)' \
    "${job_uuid}" \
    || fail "le fichier audio partagé du job ${job_uuid} est absent"
done

echo "Exécution du cycle one-shot du dispatcher"
"${compose[@]}" run --rm --no-deps dispatcher 2>&1 \
  | tee "${smoke_directory}/dispatcher.log"
assert_log_event "${smoke_directory}/dispatcher.log" dispatched
grep -Fq '"confirmed_count":2' "${smoke_directory}/dispatcher.log" \
  || fail "le dispatcher n'a pas confirmé les deux publications"

wait_for_completion "${fast_job_uuid}"
wait_for_completion "${long_job_uuid}"

for stream in transcription:fast transcription:long-form-diarization; do
  stream_length="$("${compose[@]}" exec -T redis redis-cli --raw XLEN "${stream}" | tr -d '\r')"
  [[ "${stream_length}" == "0" ]] \
    || fail "le stream ${stream} contient encore ${stream_length} message(s)"
  pending_count="$("${compose[@]}" exec -T redis redis-cli --raw \
    XPENDING "${stream}" "${WORKER_CONSUMER_GROUP:-transcription-workers}" \
    | sed -n '1p' | tr -d '\r')"
  [[ "${pending_count}" == "0" ]] \
    || fail "le PEL de ${stream} contient encore ${pending_count} message(s)"
done

"${compose[@]}" logs --no-color \
  api worker-fast worker-long-form-diarization \
  >"${smoke_directory}/applications.log"
assert_log_event "${smoke_directory}/applications.log" job_created
assert_log_event "${smoke_directory}/applications.log" job_claimed
assert_log_event "${smoke_directory}/applications.log" transcription_started
assert_log_event "${smoke_directory}/applications.log" job_completed
assert_log_event "${smoke_directory}/applications.log" redis_message_acked
grep -Fq "${fast_job_uuid}" "${smoke_directory}/applications.log" \
  || fail "les logs ne corrèlent pas le job FAST"
grep -Fq "${long_job_uuid}" "${smoke_directory}/applications.log" \
  || fail "les logs ne corrèlent pas le job LONG_FORM_DIARIZATION"
if grep -Fq '"level":"ERROR"' \
  "${smoke_directory}/applications.log" "${smoke_directory}/dispatcher.log"; then
  fail "un événement ERROR est présent dans les logs applicatifs"
fi
if grep -Fq "${POSTGRES_PASSWORD}" \
  "${smoke_directory}/applications.log" "${smoke_directory}/dispatcher.log"; then
  fail "un secret PostgreSQL est présent dans les logs"
fi

echo "Exécution ponctuelle de maintenance (les fichiers récents doivent être gardés)"
"${compose[@]}" run --rm --no-deps maintenance 2>&1 \
  | tee "${smoke_directory}/maintenance.log"
assert_log_event "${smoke_directory}/maintenance.log" cleanup

echo "Smoke test Compose réussi : FAST=${fast_job_uuid}, LONG_FORM_DIARIZATION=${long_job_uuid}"
