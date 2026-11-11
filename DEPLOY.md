# Deployment

This repository builds one FastAPI image with its dashboard served by the app. The container listens on `0.0.0.0:$PORT` (`8000` by default). On startup, the app applies Alembic migrations and inserts any missing demo orders. `/health` checks that the process is serving requests; `/ready` returns success only when the database responds.

## Environment variables

Set these in your container host's environment or secret manager:

| Variable | Required for production | Purpose |
|---|---:|---|
| `APP_ENV=production` | Yes | Enables production startup checks. |
| `AUTH_JWT_SECRET` | Yes | Signs short-lived customer and agent session tokens. Use a randomly generated secret. |
| `GOOGLE_API_KEY` | Yes | Gemini model access. |
| `GROQ_API_KEY` | Yes | Groq model access. |
| `DATABASE_URL` | Yes for durable deployment | PostgreSQL connection URL used for application data and LangGraph checkpoints. |
| `PORT` | Usually supplied by host | HTTP listen port; defaults to `8000` when unset. |

The app also accepts optional configuration variables such as `MAIN_PROVIDER`, `GEMINI_MODEL`, `GROQ_MODEL`, `LLM_TIMEOUT_SECONDS`, `LLM_MAX_RETRIES`, `LLM_RETRY_BASE_SECONDS`, `LLM_DAILY_CALL_CAP`, `SESSION_TOKEN_TTL_SECONDS`, `FRONTEND_ORIGIN`, `DATABASE_CONNECT_TIMEOUT`, and `LOG_LEVEL`. See [.env.example](./.env.example) for the documented defaults and the full set of settings. Never put live credentials in source control or image build arguments.

The production app requires both model credentials and `AUTH_JWT_SECRET` at startup. Use a managed or otherwise durable PostgreSQL database for a public deployment; local SQLite files are suitable for development only.

## Run the stack with Docker Compose

1. Install Docker with the Compose plugin.
2. Copy `.env.example` to `.env`. For local development, keep the Compose Postgres defaults or set `POSTGRES_DB`, `POSTGRES_USER`, and `POSTGRES_PASSWORD`. If overriding those values, Compose uses them in its default `DATABASE_URL`; an explicit `DATABASE_URL` takes precedence. URL-encode reserved characters in a custom database password when placing it in `DATABASE_URL`.
3. Add `GOOGLE_API_KEY` and `GROQ_API_KEY` for model-backed ticket processing. Replace `AUTH_JWT_SECRET` with a random local value if the sandbox will be accessible to others.
4. Start both services:

   ```sh
   docker compose up --build
   ```

5. Open <http://localhost:8000>. Check `/health` for process liveness and `/ready` for database readiness.
6. Stop the stack with `docker compose down`. The named Postgres volume remains. `docker compose down -v` removes that local data volume.

The app waits for Postgres to become healthy before starting. App startup runs migrations and seeds missing demo orders. No separate migration command is required for this image.

## Deploy the image to a container host

Build and push the same image to a registry your host can access:

```sh
docker build -t REGISTRY/PROJECT/support-agent:TAG .
docker push REGISTRY/PROJECT/support-agent:TAG
```

Configure the host to run that image with the environment variables above, expose the host-provided `PORT`, and attach a reachable PostgreSQL database through `DATABASE_URL`. Configure the host's readiness probe to call `/ready`; `/health` is suitable for a liveness probe. Store secrets in the platform's secret manager. Keep `WEB_CONCURRENCY=1` unless approval serialization is made safe across multiple worker processes.

## Railway

Create a project from this repository and let Railway build the root `Dockerfile`, or deploy the image from your registry. Add a Railway Postgres service, set the app's `DATABASE_URL` to the database's private connection URL, and add `APP_ENV=production`, `AUTH_JWT_SECRET`, `GOOGLE_API_KEY`, and `GROQ_API_KEY` to the app service variables. Enable public networking for the app and configure the deployment health check to use `/ready`. Railway documents [Dockerfile detection](https://docs.railway.com/builds/dockerfiles), [service variables](https://docs.railway.com/variables), and [health checks](https://docs.railway.com/deployments/healthchecks).

## Google Cloud Run

Build and push the image to Artifact Registry, create or select a Cloud Run service, then deploy the image. Cloud Run supplies `PORT` to the ingress container; this app listens on that value. Configure the required variables and secrets in the service revision, connect it to a reachable PostgreSQL database, and allow the service to receive public traffic only if that is intended for your sandbox. Use `/ready` as the readiness endpoint. See Google's [container deployment guide](https://docs.cloud.google.com/run/docs/deploying) and [container port configuration](https://docs.cloud.google.com/run/docs/configuring/services/containers).

Cloud Run can scale down to zero when idle by default; minimum instances can keep capacity warm and may affect cost. See its [instance scaling guide](https://docs.cloud.google.com/run/docs/about-instance-autoscaling).

## Plans, free tiers, and billing

Platform offers change over time. Free tiers or trials may sleep or scale to zero, impose small CPU, memory, storage, or usage limits, expire credits, or require a billing account or payment card for particular features. Check each provider's current terms and pricing before choosing a host. See the current [Railway plans](https://docs.railway.com/pricing/plans) and [Cloud Run pricing](https://cloud.google.com/run/pricing).

No deployment has been performed as part of preparing this guide.
