# profile-api

A simple REST API for user profiles built with FastAPI and Supabase, deployed on Vercel.

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/profiles` | List all profiles |
| GET | `/api/profiles/:id` | Get a profile by ID |
| POST | `/api/profiles` | Create a profile |
| PUT | `/api/profiles/:id` | Update a profile |
| DELETE | `/api/profiles/:id` | Delete a profile |

## Local development

```bash
pip install -r requirements.txt
uvicorn api.index:app --reload
```

## Deployment

Deploy to Vercel via the CLI or dashboard. Set the environment variables listed in `.env.example`.
