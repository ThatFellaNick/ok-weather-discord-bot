# US Weather Discord Bot

Python Docker service that posts NWS/SPC weather alerts to Discord and sends scheduled weather briefings for a configurable US state or radius around one or more GPS points. The default profile remains Oklahoma.

## Important

Regenerate Discord webhooks before using this if you pasted them into ChatGPT or anywhere else. A webhook URL can post to that channel.

Never commit `.env`, Discord webhook URLs, state files, runtime data, or logs.

## What It Does

- Polls NWS active alerts by state, plus exact NWS point alerts when a radius is configured.
- Filters alert polygons to `TARGET_RADIUS_MILES` around `TARGET_POINTS` when radius mode is enabled.
- Posts selected warnings, watches, special weather statements, and SPC RSS items to Discord.
- Sends daily and optional afternoon severe weather briefings.
- Includes NWS point forecasts, Area Forecast Discussion notes, SPC Day 1/Day 2 outlooks, SPC GIS risk summaries, and optional radar/SPC images.
- Stores dedupe and schedule state in Docker `/data`.

## Example Posts

| Tornado warning with radar | SPC mesoscale discussion |
| --- | --- |
| ![Tornado warning Discord example](https://raw.githubusercontent.com/ThatFellaNick/ok-weather-discord-bot/main/docs/images/tornado-warning-example.png) | ![SPC mesoscale discussion Discord example](https://raw.githubusercontent.com/ThatFellaNick/ok-weather-discord-bot/main/docs/images/spc-md-example.png) |

![Weather brief Discord example](https://raw.githubusercontent.com/ThatFellaNick/ok-weather-discord-bot/main/docs/images/weather-brief-example.png)

## Quick Start on Any Docker Host

Use the published Docker image when you just want to run the bot:

```bash
mkdir -p ok-weather-discord-bot/data
cd ok-weather-discord-bot
wget https://raw.githubusercontent.com/ThatFellaNick/ok-weather-discord-bot/main/docker-compose.yml
wget https://raw.githubusercontent.com/ThatFellaNick/ok-weather-discord-bot/main/config.example.env
cp config.example.env .env
```

Edit `.env`, at minimum:

- `BRIEF_WEBHOOK_URL` or `TEAMS_BRIEF_WEBHOOK_URL`
- `ALERT_WEBHOOK_URL` or `TEAMS_ALERT_WEBHOOK_URL`
- `NWS_USER_AGENT`, including your contact email
- `TARGET_NAME`
- `TARGET_MODE`
- `TARGET_STATES`
- `TARGET_POINTS` if you want forecast points or use radius mode

Start it:

```bash
docker compose pull
docker compose up -d
```

View logs:

```bash
docker logs -f ok-weather-bot
```

Stop it:

```bash
docker compose down
```

## Updating After a Release

If you run the published Docker image, keep your `.env` and `data/` folder in place and pull the new image:

```bash
docker compose pull
docker compose up -d
```

If you cloned the repo because you want local source changes, keep your `.env` and `data/` folder in place and rebuild:

```bash
git pull
docker compose up -d --build
```

To pin a specific published version later, set `BOT_IMAGE` in `.env`, for example:

```env
BOT_IMAGE=thatfellanick/ok-weather-discord-bot:v2.5.4
```

## Build Locally From Git

Use this path if you want to edit the source or test unreleased changes:

```bash
git clone https://github.com/ThatFellaNick/ok-weather-discord-bot.git
cd ok-weather-discord-bot
cp config.example.env .env
mkdir -p data
docker compose up -d --build
```

## Target Setup

Whole-state Oklahoma, the default:

```env
TARGET_NAME=Oklahoma
TARGET_MODE=state
TARGET_STATES=OK
TARGET_POINTS=OKC:35.4676,-97.5164;Tulsa:36.1540,-95.9928;Lawton:34.6036,-98.3959
TARGET_RADIUS_MILES=0
```

Whole-state Kansas:

```env
TARGET_NAME=Kansas
TARGET_MODE=state
TARGET_STATES=KS
TARGET_POINTS=Wichita:37.6872,-97.3301;Topeka:39.0473,-95.6752
TARGET_RADIUS_MILES=0
```

Fifty miles around one town or custom GPS point:

```env
TARGET_NAME=Wichita Metro
TARGET_MODE=radius
TARGET_STATES=KS
TARGET_POINTS=Wichita:37.6872,-97.3301
TARGET_RADIUS_MILES=50
```

Use `TARGET_MODE=state` for one or more full states. Use `TARGET_MODE=radius` for a city/town/custom GPS setup. For radius setups, look up the latitude and longitude and put it in `TARGET_POINTS`; `TARGET_STATES` should include the state being monitored and any nearby border states the radius crosses, such as `TARGET_STATES=KS,OK`.

## Discord and Microsoft Teams Channels

Use the single webhook variables for normal Discord setups:

```env
BRIEF_WEBHOOK_URL=https://discord.com/api/webhooks/REPLACE_ME
ALERT_WEBHOOK_URL=https://discord.com/api/webhooks/REPLACE_ME
```

To also post to Microsoft Teams, add Teams incoming webhook URLs:

```env
TEAMS_BRIEF_WEBHOOK_URL=https://example.webhook.office.com/REPLACE_ME
TEAMS_ALERT_WEBHOOK_URL=https://example.webhook.office.com/REPLACE_ME
```

Discord and Teams can be used together, or you can leave the Discord variables
empty and only configure Teams.

To post to more than one channel, add extra URLs to the plural variables:

```env
BRIEF_WEBHOOK_URLS=https://discord.com/api/webhooks/REPLACE_ME,https://discord.com/api/webhooks/REPLACE_ME
ALERT_WEBHOOK_URLS=https://discord.com/api/webhooks/REPLACE_ME https://discord.com/api/webhooks/REPLACE_ME
TEAMS_BRIEF_WEBHOOK_URLS=https://example.webhook.office.com/REPLACE_ME,https://example.webhook.office.com/REPLACE_ME
TEAMS_ALERT_WEBHOOK_URLS=https://example.webhook.office.com/REPLACE_ME https://example.webhook.office.com/REPLACE_ME
```

## Unraid Notes

The service is still friendly to an Unraid appdata deployment:

- Source files can live under `/mnt/user/appdata/ok-weather-discord-bot/`.
- Runtime state and logs stay in `./data`, mounted into the container as `/data`.
- Configuration comes from `.env`, based on `config.example.env`.

Typical Unraid start command:

```bash
cd /mnt/user/appdata/ok-weather-discord-bot
docker compose pull
docker compose up -d
```

## Docker Hub Publishing

This repo includes a GitHub Actions workflow that publishes the image to Docker Hub as:

```text
thatfellanick/ok-weather-discord-bot
```

Before the first publish, create that Docker Hub repository and add these GitHub repository secrets under `Settings` -> `Secrets and variables` -> `Actions`:

- `DOCKERHUB_USERNAME`: your Docker Hub username
- `DOCKERHUB_TOKEN`: a Docker Hub access token with read/write access

The workflow publishes multi-platform images for `linux/amd64` and `linux/arm64`.

Tags:

- `latest` on pushes to `main`
- `main` on pushes to `main`
- version tags such as `v2.5.4` when you push a matching git tag
- `sha-...` tags for exact commit builds

## Environment Variables

All supported environment variables are shown in `config.example.env`.

| Variable | Purpose | Default |
| --- | --- | --- |
| `BRIEF_WEBHOOK_URL` | Discord webhook for scheduled briefings, startup messages, test briefings, and manual briefings. | empty |
| `BRIEF_WEBHOOK_URLS` | Optional extra brief webhooks, separated by commas or spaces. | empty |
| `ALERT_WEBHOOK_URL` | Discord webhook for NWS alerts and SPC RSS items. | empty |
| `ALERT_WEBHOOK_URLS` | Optional extra alert webhooks, separated by commas or spaces. | empty |
| `TEAMS_BRIEF_WEBHOOK_URL` | Microsoft Teams webhook for scheduled briefings, startup messages, test briefings, and manual briefings. | empty |
| `TEAMS_BRIEF_WEBHOOK_URLS` | Optional extra Teams brief webhooks, separated by commas or spaces. | empty |
| `TEAMS_ALERT_WEBHOOK_URL` | Microsoft Teams webhook for NWS alerts and SPC RSS items. | empty |
| `TEAMS_ALERT_WEBHOOK_URLS` | Optional extra Teams alert webhooks, separated by commas or spaces. | empty |
| `NWS_USER_AGENT` | User-Agent sent to NWS/SPC requests. Include a contact email. | `ok-weather-discord-bot/2.4` |
| `TARGET_NAME` | Human-readable area name used in Discord posts. | `Oklahoma` |
| `TARGET_MODE` | `state` for full-state monitoring or `radius` for GPS/radius monitoring. | `state` |
| `TARGET_STATES` | Comma-separated state abbreviations for NWS alert polling. | `OK` |
| `TARGET_POINTS` | Semicolon-separated `Name:lat,lon` forecast/radius points. | Oklahoma points |
| `TARGET_RADIUS_MILES` | Mile radius around `TARGET_POINTS` when `TARGET_MODE=radius`. | `0` |
| `TARGET_BBOX` | Optional SPC GIS envelope as `west,south,east,north`. | built in |
| `TZ` | Timezone used for scheduling and displayed times. | `America/Chicago` |
| `POLL_SECONDS` | Main polling loop interval in seconds. | `180` |
| `HTTP_MAX_RETRIES` | Maximum attempts for each NWS/SPC source fetch. | `3` |
| `SEVERE_THUNDERSTORM_WARNING_MODE` | `all` posts every Severe Thunderstorm Warning; `high_end` only posts stronger-worded warnings. | `all` |
| `BRIEF_HOUR` | Hour for the daily brief in `TZ`, 24-hour clock. | `9` |
| `BRIEF_MINUTE` | Minute for the daily brief in `TZ`. | `0` |
| `AFTERNOON_SEVERE_BRIEF_ENABLED` | Send a rest-of-day severe weather brief in the afternoon. | `true` |
| `AFTERNOON_SEVERE_BRIEF_HOUR` | Hour for the rest-of-day severe weather brief in `TZ`. | `15` |
| `AFTERNOON_SEVERE_BRIEF_MINUTE` | Minute for the rest-of-day severe weather brief in `TZ`. | `30` |
| `STATE_FILE` | Persistent JSON state file path inside the container. | `/data/state.json` |
| `LOG_FILE` | Log file path inside the container. | `/data/weather.log` |
| `SEND_STARTUP_MESSAGE` | Send a one-time startup message when enabled. | `true` |
| `TEST_BRIEF_ON_START` | Send a brief immediately at container start when enabled. | `false` |
| `TRIGGER_BRIEF_FILE` | File path watched for manual brief requests. | `/data/trigger_brief` |
| `TRIGGER_ALERT_TEST_FILE` | File path watched for alert webhook test posts. | `/data/trigger_alert_test` |
| `AFD_OFFICES` | Optional comma-separated NWS offices used for Forecaster Notes. Leave blank to auto-detect from `TARGET_POINTS`; the default Oklahoma profile falls back to `OUN,TSA`. | auto |
| `INCLUDE_BRIEF_IMAGES` | Include SPC outlook map images, SPC RSS item images, and radar loop embeds. | `true` |
| `RADAR_STATIONS` | Optional comma-separated radar IDs. Leave blank to auto-detect from `TARGET_POINTS`; the default Oklahoma profile falls back to `KTLX,KINX,KFDR`. | auto |
| `DISCORD_MAX_RETRIES` | Maximum Discord webhook attempts per post. | `3` |
| `TEAMS_MAX_RETRIES` | Maximum Microsoft Teams webhook attempts per post. | `DISCORD_MAX_RETRIES` |
| `BOT_IMAGE` | Docker image used by Compose. Pin this to a version tag if you do not want `latest`. | `thatfellanick/ok-weather-discord-bot:latest` |

## Manual Triggers

Create the trigger file on the Docker host to request a briefing outside the scheduled time:

```bash
touch ./data/trigger_brief
```

The bot checks for this file during the normal polling loop. After a successful webhook post, it removes the trigger file. If posting fails, the file is left in place so the next loop can retry.

To test the alert webhook directly:

```bash
touch ./data/trigger_alert_test
```

The bot posts a test message to the alert webhook(s) and removes the file after a successful post.

## Notes

This is a good starting point, not a replacement for NOAA Weather Radio, Wireless Emergency Alerts, RadarScope, or official warning systems.
