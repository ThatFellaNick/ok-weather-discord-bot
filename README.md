# Oklahoma Weather Discord Bot

Python Docker service for Unraid that posts Oklahoma NWS/SPC alerts to Discord and sends a daily 9 AM weather brief.

## Important
Regenerate Discord webhooks before using this if you pasted them into ChatGPT or anywhere else. A webhook URL can post to that channel.

## Setup on Unraid

1. Open Unraid web UI.
2. Go to `Shares` and make sure you have an `appdata` share.
3. Create this folder on your server:
   `/mnt/user/appdata/ok-weather-discord-bot/`
4. Copy the contents of this folder into it.
5. Edit `docker-compose.yml`.
6. Replace:
   - `PASTE_REGENERATED_BRIEF_WEBHOOK_HERE`
   - `PASTE_REGENERATED_ALERT_WEBHOOK_HERE`
7. Replace `your_email@example.com` in `NWS_USER_AGENT` with your email. NWS asks API users to identify their app/user-agent.
8. In Unraid, open a terminal and run:

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

## What it watches

- NWS active alerts for Oklahoma:
  `https://api.weather.gov/alerts/active?area=OK`
- SPC RSS feed:
  `https://www.spc.noaa.gov/products/spcrss.xml`

## Defaults

- Alerts poll every 180 seconds.
- Daily brief posts at 9:00 AM Central.
- Dedup state is saved to `./data/state.json`.

## Notes

This is a good starting point, not a replacement for NOAA Weather Radio, Wireless Emergency Alerts, RadarScope, or official warning systems.
