# Oklahoma Weather Discord Bot

Python Docker service for Unraid that posts Oklahoma NWS/SPC alerts to Discord and sends a daily Oklahoma weather brief.

## Important

Regenerate Discord webhooks before using this if you pasted them into ChatGPT or anywhere else. A webhook URL can post to that channel.

Never commit `.env`, Discord webhook URLs, state files, runtime data, or logs.

## Project Overview

The bot runs as a single long-lived Python process in Docker. It polls NWS and SPC data, posts selected alerts to Discord, and sends one scheduled daily briefing.

The service is designed for an Unraid appdata deployment:

- Source files live under `/mnt/user/appdata/ok-weather-discord-bot/`.
- Runtime state and logs are stored in `./data`, mounted into the container as `/data`.
- Configuration comes from `.env`, based on `config.example.env`.

## Data Sources

- NWS active alerts for Oklahoma:
  `https://api.weather.gov/alerts/active?area=OK`
- NWS point forecasts for configured Oklahoma city snapshots.
- NWS Area Forecast Discussions for configured offices:
  `https://api.weather.gov/products/types/AFD/locations/{office}/latest`
- SPC RSS feed:
  `https://www.spc.noaa.gov/products/spcrss.xml`
- SPC Day 1 convective outlook text:
  `https://www.spc.noaa.gov/products/outlook/day1otlk.txt`
- SPC Day 2 convective outlook text:
  `https://www.spc.noaa.gov/products/outlook/day2otlk.txt`
- NOAA/NWS SPC outlook map service for Oklahoma-intersecting categorical,
  tornado, hail, and wind probability polygons:
  `https://mapservices.weather.noaa.gov/vector/rest/services/outlooks/SPC_wx_outlks/MapServer`
- SPC outlook map images:
  `https://www.spc.noaa.gov/products/outlook/day1otlk.gif`
- NWS radar loop images from `radar.weather.gov`.

## Setup on Unraid

1. Open Unraid web UI.
2. Go to `Shares` and make sure you have an `appdata` share.
3. Create this folder on your server:
   `/mnt/user/appdata/ok-weather-discord-bot/`
4. Copy the contents of this folder into it.
5. Copy `config.example.env` to `.env`.
6. Edit `.env`.
7. Set `BRIEF_WEBHOOK_URL` and `ALERT_WEBHOOK_URL` to regenerated Discord webhooks.
8. Replace `your_email@example.com` in `NWS_USER_AGENT` with your email. NWS asks API users to identify their app/user-agent.
9. In Unraid, open a terminal and run:

```bash
cd /mnt/user/appdata/ok-weather-discord-bot
docker compose up -d --build
```

## View logs

```bash
docker logs -f ok-weather-bot
```

## Stop

```bash
cd /mnt/user/appdata/ok-weather-discord-bot
docker compose down
```

## Environment Variables

All supported environment variables are shown in `config.example.env`.

| Variable | Purpose | Default |
| --- | --- | --- |
| `BRIEF_WEBHOOK_URL` | Discord webhook for daily briefings, startup messages, test briefings, and manual briefings. | empty |
| `ALERT_WEBHOOK_URL` | Discord webhook for NWS alerts and SPC RSS items. | empty |
| `NWS_USER_AGENT` | User-Agent sent to NWS/SPC requests. Include a contact email. | `ok-weather-discord-bot/2.4` |
| `TZ` | Timezone used for scheduling and displayed times. | `America/Chicago` |
| `POLL_SECONDS` | Main polling loop interval in seconds. | `180` |
| `HTTP_MAX_RETRIES` | Maximum attempts for each NWS/SPC source fetch. | `3` |
| `SEVERE_THUNDERSTORM_WARNING_MODE` | `all` posts every Severe Thunderstorm Warning; `high_end` only posts stronger-worded warnings. | `all` |
| `BRIEF_HOUR` | Hour for the daily brief in `TZ`, 24-hour clock. | `9` |
| `BRIEF_MINUTE` | Minute for the daily brief in `TZ`. | `0` |
| `STATE_FILE` | Persistent JSON state file path inside the container. | `/data/state.json` |
| `LOG_FILE` | Log file path inside the container. | `/data/weather.log` |
| `SEND_STARTUP_MESSAGE` | Send a one-time startup message when enabled. | `true` |
| `TEST_BRIEF_ON_START` | Send a brief immediately at container start when enabled. | `false` |
| `TRIGGER_BRIEF_FILE` | File path watched for manual brief requests. | `/data/trigger_brief` |
| `TRIGGER_ALERT_TEST_FILE` | File path watched for alert webhook test posts. | `/data/trigger_alert_test` |
| `AFD_OFFICES` | Comma-separated NWS offices used for Forecaster Notes. | `OUN,TSA` |
| `INCLUDE_BRIEF_IMAGES` | Include SPC outlook map embeds and radar loop embeds. | `true` |
| `RADAR_STATIONS` | Comma-separated radar IDs. First station is shown when active notable alerts exist. | `KTLX,KINX,KFDR` |
| `DISCORD_MAX_RETRIES` | Maximum Discord webhook attempts per post. | `3` |

## Manual Brief Trigger

Create the trigger file on the Unraid host to request a briefing outside the scheduled time:

```bash
touch /mnt/user/appdata/ok-weather-discord-bot/data/trigger_brief
```

The bot checks for this file during the normal polling loop. After a successful Discord post, it removes the trigger file. If posting fails, the file is left in place so the next loop can retry.

To test the alert webhook directly:

```bash
touch /mnt/user/appdata/ok-weather-discord-bot/data/trigger_alert_test
```

The bot posts a test message to `ALERT_WEBHOOK_URL` and removes the file after a successful post.

## Defaults

- Alerts poll every 180 seconds.
- Daily brief posts at 9:00 AM Central.
- Dedup state is saved to `./data/state.json`.

## Notes

This is a good starting point, not a replacement for NOAA Weather Radio, Wireless Emergency Alerts, RadarScope, or official warning systems.
