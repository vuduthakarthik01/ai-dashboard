# IndustrialMind AI

IndustrialMind AI is an autonomous factory manager built for small and medium manufacturers. It unifies AI CCTV safety monitoring, predictive maintenance, smart inventory forecasting, production and energy dashboards, AI quality inspection, and a live chat assistant into one real-time, role-based dashboard.

## What's in this repository

- **`index.html`** — the full dashboard. Self-contained, no build step. Open it directly in a browser, or host it as a static site (this filename is ready for GitHub Pages as-is).
- **`backend/`** — the FastAPI + PostgreSQL/TimescaleDB service: auth and roles, machine telemetry, predictive maintenance, inventory forecasting, CCTV alerts, quality inspection, energy monitoring, and the AI assistant endpoint. See `ARCHITECTURE.md` for the full design.
- **`ARCHITECTURE.md`** — system architecture, data model, and build order.

## Running the dashboard

Just open `index.html` in a browser — it runs its own live simulation, so it works standalone with no server. Note: the AI chat tab only connects to Claude when this file is opened through the Claude artifact viewer (the published link), not when opened locally or hosted elsewhere — everything else works the same regardless of how it's opened.

## Running the backend

```
cd backend
cp .env.example .env      # add your ANTHROPIC_API_KEY
docker compose up --build
```

Then visit ``.
http://localhost:8000/docs
## Hosting the dashboard on GitHub Pages

1. Push this repository to GitHub with `index.html` at the root.
2. In the repo, go to **Settings → Pages**.
3. Set Source to **Deploy from a branch**, Branch to **main**, folder to **/(root)**.
4. Save, wait a minute, and your live link will appear on that same page.
