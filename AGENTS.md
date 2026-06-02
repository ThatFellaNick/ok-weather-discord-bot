# Repository Instructions

This repository contains an Oklahoma weather Discord bot that runs on Unraid in Docker.

## Operating Context

- The bot uses NWS and SPC weather data.
- The bot posts Oklahoma weather briefings and alerts to Discord.
- Runtime data is stored under the Docker `/data` mount.
- Docker Compose is the expected deployment path on an Unraid server.

## Security Rules

- Never commit secrets.
- Never commit Discord webhook URLs.
- Never commit `.env` files.
- Never commit state files, runtime data, or logs.
- Use `config.example.env` for documented configuration examples only.

## Development Guidelines

- Keep changes small and focused.
- Preserve existing functionality unless explicitly asked to change it.
- Prefer readability over cleverness.
- Add comments only when they improve maintainability.
- Document any new environment variables in `config.example.env`.
- Keep weather alerting logic conservative and easy to audit.

## Runtime Files

Do not treat these as source files:

- `data/`
- `state.json`
- `weather.log`
- `.env`
- `config.local.env`
