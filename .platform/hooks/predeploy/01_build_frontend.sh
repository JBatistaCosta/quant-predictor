#!/bin/bash
# Roda ANTES do app subir, depois do "npm install" que o Elastic Beanstalk já
# faz sozinho. Builda o front-end (dist/) que server/app.js serve como
# arquivo estático — mesmo papel que "npm run build" tem no Vercel e
# "npm run build:firebase" tem no deploy do Firebase Hosting.
set -e
cd /var/app/staging
npm run build:aws
