# Deployment

## Local Docker Compose

1. Install Docker Desktop or Docker Engine with the Compose plugin.
2. Copy `.env.example` to `.env`. Keep the local Postgres values for Compose. Add your Gemini and Groq keys to `.env`; never commit that file.
3. Start the app and database:

   ```sh
   docker compose up --build
   ```

4. Open <http://localhost:8000>. Check liveness at `/health` and database readiness at `/ready`.
5. Stop the services with `docker compose down`. The named `postgres_data` volume remains. Use `docker compose down -v` only when you intentionally want to delete local database data.

Compose runs Postgres at host `db`. Running the Python app directly on your host uses the SQLite database at `DATABASE_PATH` by default. Apply migrations automatically by starting the app, or run `alembic upgrade head` and `python -m support_agent.seed` manually.

## Render Blueprint

The repository includes [`render.yaml`](./render.yaml), which creates one Docker web service and a Render Postgres database. Orders, refund idempotency records, tickets, audit events, and LangGraph checkpoints use that database.

1. Push this repository to a Git provider supported by Render.
2. In the Render dashboard, choose **New → Blueprint**, connect the repository, and select this repository's `render.yaml`.
3. Review the proposed resources and their plans/regions, then create the Blueprint. Render wires the private Postgres connection into `DATABASE_URL` and prompts for the two variables marked `sync: false`.
4. Enter `GOOGLE_API_KEY` and `GROQ_API_KEY` in Render's secret prompt or service environment settings. Do not put credentials in `render.yaml`, GitHub Actions, or this repository.
5. Wait for the web service health check to pass. `/ready` returns success only when it can connect to Postgres. Visit the service URL to open the dashboard.
6. Check the service logs for one-line JSON records. Application records include `ticket_id`; non-ticket process and request logs use `ticket_id: "system"`.

No deployment has been run as part of preparing these files.

## Persistence and application limits

- An empty `DATABASE_URL` selects SQLite at `DATABASE_PATH` for local development. Set `DATABASE_URL` to use Postgres for both SQLAlchemy models and the LangGraph checkpointer.
- For the installed LangGraph 1.2 line, local checkpointing uses `SqliteSaver` from `langgraph-checkpoint-sqlite`; deployments with `DATABASE_URL` use `PostgresSaver` from `langgraph-checkpoint-postgres`. The SQLite saver suits local synchronous use; Postgres is the shared production backend.
- The service applies Alembic migrations and seeds any missing demo orders during startup. `python -m support_agent.seed` applies migrations and inserts missing demo orders without changing existing rows. `/demo/reset` restores the five demo orders and clears their refund idempotency records.
- `WEB_CONCURRENCY=1` remains the Render default because approval locks are process-local. Postgres persistence is shared, but approval serialization across multiple workers would need a database-backed lock or compare-and-set.
- The app currently has no user authentication or authorization. Add access control for the support-agent approval and demo endpoints before using this public service with real customer information or transactions.
- The bundled order backend is seeded demo data. Refund updates and idempotency keys are committed together in a database transaction; integrate a payment provider only if it also accepts an idempotency key for its own external transaction.
