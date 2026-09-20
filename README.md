# SNine Client Status

Public SNine Client status page for `status.s9lab.site`.

## Features

- GitHub Pages frontend
- automatic service monitoring with GitHub Actions
- 30-day uptime calculation
- current response times
- automatic incidents when monitored services fail
- automatic recovery updates
- scheduled maintenance feed
- public JSON API
- no database required
- no external status provider required

## Public API

Once deployed:

- `/api/status.json`
- `/api/incidents.json`
- `/api/maintenance.json`
- `/api/history.json`

## Automatic checks

The workflow runs every 5 minutes and checks all configured services.

Public checks work immediately:
- S9Lab website
- SNC Roadmap

Private/internal checks can be configured through GitHub Actions secrets without exposing their real origin in this public repository:

- `SNINE_CORE_HEALTH_URL`
- `SNINE_AUTH_HEALTH_URL`
- `SNINE_COSMETICS_HEALTH_URL`
- `SNINE_LAUNCHER_HEALTH_URL`
- `SNINE_WEBSOCKET_HOST`
- `SNINE_WEBSOCKET_PORT`
- `SNINE_MINECRAFT_HOST`
- `SNINE_MINECRAFT_PORT`

When an optional private target is not configured, its component can derive its state from Core or use the manual fallback defined in `config/services.json`.

## Incidents

Automatic incidents are maintained by the monitor.

Manual incidents can be added to:

`config/incidents.json`

## Maintenance

Scheduled maintenance is managed in:

`config/maintenance.json`

## GitHub Pages

Enable:

`Settings -> Pages -> Source -> GitHub Actions`

Then set the custom domain:

`status.s9lab.site`

DNS:

`status CNAME silas9k.github.io`
