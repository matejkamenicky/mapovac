#!/bin/bash
# Zastaví běžící server Mapovače.
pkill -f "uvicorn backend.api.main:app" && echo "Mapovač zastaven." || echo "Mapovač neběžel."
sleep 1
