# Deployment

## Local Docker Compose

1. Install Docker Desktop or Docker Engine with the Compose plugin.
2. Copy `.env.example` to `.env`. Keep the local Postgres values for Compose. Add your Gemini and Groq keys to `.env`; never commit that file.
3. Start the app and database:

   ```sh
   docker compose up --build
   ```

4. Open <http://localhost:8000>. Check liveness at `/health` and database readiness at `/ready`.
5. Stop the services with `docker compose down`. The named `postgres_data` and `audit_data` volumes remain. Use `docker compose down -v` only when you intentionally want to delete local database and audit data.

The default Compose Postgres host is `db`. When running the Python app directly on your host instead of in Compose, change `DATABASE_URL` in `.env` to use `localhost`.

## Render Blueprint

The repository includes [`render.yaml`](./render.yaml), which creates one Docker web service, a Render Postgres database, and a small persistent disk for the JSONL audit file. The Blueprint uses Render's documented Docker service fields, database connection reference, health-check path, and disk configuration.

1. Push this repository to a Git provider supported by Render.
2. In the Render dashboard, choose **New → Blueprint**, connect the repository, and select this repository's `render.yaml`.
3. Review the proposed resources and their plans/regions, then create the Blueprint. Render wires the private Postgres connection into `DATABASE_URL` and prompts for the two variables marked `sync: false`.
4. Enter `GOOGLE_API_KEY` and `GROQ_API_KEY` in Render's secret prompt or service environment settings. Do not put credentials in `render.yaml`, GitHub Actions, or this repository.
5. Wait for the web service health check to pass. `/ready` returns success only when it can connect to Postgres. Visit the service URL to open the dashboard.
6. Check the service logs for one-line JSON records. Application records include `ticket_id`; non-ticket process and request logs use `ticket_id: "system"`.

No deployment has been run as part of preparing these files.

## Current application limits

- Postgres is currently used by `/ready` as a dependency check. Tickets, approval state, mock orders, and LangGraph checkpoints are still held in process memory. Restarting or scaling beyond one worker loses or splits that state. Keep `WEB_CONCURRENCY=1` and one web instance until shared persistence is implemented.
- The Render disk persists the JSONL audit file, while structured application logs are also emitted to stdout.
- The app currently has no user authentication or authorization. Add access control for the support-agent approval and demo endpoints before using this public service with real customer information or transactions.
- The bundled order backend is mock data. Connect a real order system only after its identity and transaction guarantees are designed and tested.
