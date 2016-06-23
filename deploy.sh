#!/usr/bin/env bash
set -e # halt script on error

zip -r updater.zip . --include=*.py
iron worker upload --max-concurrency 1 --zip updater.zip --name updater astrodigital/sentinel2-metadata:latest python iron.py
