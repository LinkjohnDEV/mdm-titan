#!/bin/bash

echo "🚀 Iniciando MDM Titan..."

# Inicia o worker em background
python /app/worker.py &
WORKER_PID=$!
echo "✅ Worker iniciado (PID: $WORKER_PID)"

# Inicia o Flask (foreground)
echo "✅ Servidor Flask iniciando na porta 3063..."
python /app/app.py

# Se o Flask morrer, mata o worker também
kill $WORKER_PID 2>/dev/null
