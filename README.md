# SteadyEye Backend

Backend service for the SteadyEye iOS app. Currently powers in-app chat. Will host additional features over time (analytics, push notifications, server-side config, etc.).

## Current features
- In-app chat (user → Telegram bot → user reply via polling)

## Stack
- Django 5.x + DRF
- PostgreSQL on Railway
- Python 3.12

## Local development
See SETUP.md.

## Deployment
Deployed on Railway via GitHub integration. See SETUP.md.
