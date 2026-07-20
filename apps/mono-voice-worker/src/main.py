from mono_voice_worker.config import WorkerMonoVoiceConfig
from mono_voice_worker import WorkerMonoVoice

def main():
    worker = WorkerMonoVoice(WorkerMonoVoiceConfig())
    print("🚀 Worker démarré avec le contexte de l'application Flask.")
    print(f"📂 Dossier Audio configuré : {WorkerMonoVoiceConfig.AUDIO_FOLDER_PATH}")

    # TODO gérer proprement l'arrêt du worker avec un signal (SIGINT, SIGTERM)
    while True:
        worker.run_once()

if __name__ == "__main__":
    main()