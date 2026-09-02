#!/bin/bash
cd "$(dirname "$0")"
if [ -f ".venv/bin/python" ]; then
    exec .venv/bin/python -u bot.py
else
    exec python -u bot.py
fi


