# Deployment

> Render for the backend, Vercel for the frontend.
>
> [← Back to the README](../README.md)

## Render (backend)

* Build: `pip install -r requirements.txt`
* Start: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
* Env: everything from `.env.example`, plus
  * `ALLOWED_ORIGINS=https://your-app.vercel.app`
  * `POSTGRES_CHECKPOINT_URL=<Supabase connection string>` and add
    `langgraph-checkpoint-postgres` to `requirements.txt` — otherwise checkpoints
    live on the instance's ephemeral disk and are lost on redeploy.

## Vercel (frontend)

* Root directory: `frontend`
* Env: `NEXT_PUBLIC_BACKEND_WS_URL=wss://your-service.onrender.com`,
  `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`
