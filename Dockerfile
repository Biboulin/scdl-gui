FROM python:3.11-slim

WORKDIR /app

# Dépendances système (ffmpeg pour scdl + mutagen)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Dépendances Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Code source
COPY app.py .
COPY index.html .

# Dossier temporaire pour les téléchargements
RUN mkdir -p /tmp/scdl

EXPOSE 5005

CMD ["python", "app.py"]
