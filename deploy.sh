#!/usr/bin/env bash
set -e # halt script on error

zip -r updater.zip . --include=*.py
iron worker upload --max-concurrency $2 --zip updater.zip --name $1 astrodigital/sentinel2-metadata:latest python iron.py
