# Utilise Python 3.12
FROM python:3.12-slim

# Définir le répertoire de travail
WORKDIR /app

# Installer les dépendances système pour psycopg2 et pip
RUN apt-get update \
    && apt-get -y install libpq-dev gcc \
    && pip install psycopg2

# Copier les fichiers nécessaires
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copier le code de l'application
COPY . .

# Exposer le port que Flask utilisera
EXPOSE 5000

# Définir la commande de lancement de l'API
ENTRYPOINT ["python", "run_api.py"]