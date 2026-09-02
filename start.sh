#!/bin/bash
if [ -f ".venv/bin/python" ]; then
    exec .venv/bin/python -u bot.py
else
    exec python -u bot.py
fi


