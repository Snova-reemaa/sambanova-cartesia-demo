#!/bin/bash
# Start the demo. Sorts out the virtualenv and dependencies if they are missing.
#
#   ./run.sh
#
# The point of this script is that `python server.py` does not work and never
# will: this machine has no `python` on PATH, and the system `python3` has none
# of the dependencies. They live in ./venv.

set -e
cd "$(dirname "$0")"

PORT="${PORT:-8080}"

if [ ! -x venv/bin/python ]; then
  echo "  creating venv…"
  python3 -m venv venv
  venv/bin/pip install -q --upgrade pip
fi

if ! venv/bin/python -c "import aiohttp, openai, cartesia, dotenv" 2>/dev/null; then
  echo "  installing dependencies…"
  venv/bin/pip install -q -r requirements.txt
fi

if [ ! -f .env ]; then
  echo
  echo "  No .env file. Copy the template and add your keys:"
  echo "      cp .env.example .env"
  echo
  exit 1
fi

for key in SAMBANOVA_API_KEY CARTESIA_API_KEY; do
  if ! grep -qE "^${key}=.+" .env; then
    echo
    echo "  ${key} is empty in .env — the demo cannot start without it."
    echo
    exit 1
  fi
done

# A server left running from last time holds the port and the new one dies.
if lsof -ti:"$PORT" >/dev/null 2>&1; then
  echo "  port $PORT was busy — stopping the old server"
  lsof -ti:"$PORT" | xargs kill 2>/dev/null || true
  sleep 1
fi

echo "  opening http://localhost:$PORT in your browser"
( sleep 2; open "http://localhost:$PORT" >/dev/null 2>&1 || true ) &

exec venv/bin/python -u server.py
