#!/bin/bash
# Lance la web app SoundCloud Downloader

set -e

cd "$(dirname "$0")"

echo "🎵 SoundCloud Downloader"
echo "========================"

# Vérifie Python
if ! command -v python3 &>/dev/null; then
  echo "❌ Python3 non trouvé. Installe-le depuis https://python.org"
  exit 1
fi

# Installe les dépendances si besoin
if ! python3 -c "import flask" &>/dev/null; then
  echo "📦 Installation de Flask..."
  pip3 install flask flask-cors --quiet
fi

if ! command -v scdl &>/dev/null && ! python3 -c "import scdl" &>/dev/null; then
  echo ""
  echo "⚠️  scdl n'est pas installé."
  echo "   Lance cette commande pour l'installer :"
  echo "   pip3 install scdl"
  echo ""
  echo "   L'app va quand même démarrer, mais les téléchargements ne fonctionneront pas."
  echo ""
fi

# Ouvre le navigateur automatiquement (après 1 sec)
sleep 1 && open "http://localhost:5005" &

# Lance le serveur
python3 app.py
