# Insighta Labs — Stage 3 Documentation

Profile intelligence platform: a FastAPI backend, a Next.js web portal, and a Python CLI — all sharing one GitHub OAuth flow and one JWT token scheme.

---

## Table of Contents

1. [System Architecture](#1-system-architecture)
2. [Auth Flow](#2-auth-flow)
3. [Token Handling](#3-token-handling)
4. [Role Enforcement](#4-role-enforcement)
5. [Natural Language Parsing](#5-natural-language-parsing)
6. [API Endpoints](#6-api-endpoints)
7. [CLI Usage](#7-cli-usage)
8. [Running Locally](#8-running-locally)
9. [Environment Variables](#9-environment-variables)

---

## 1. System Architecture

Three separate repositories; the backend is the single source of truth for auth and data.

```
┌─────────────────────────────────────────────────────────────────────┐
│                        GitHub OAuth                                 │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
                ┌──────────────▼──────────────┐
                │         profile-api          │
                │  FastAPI + Supabase          │
                │  profile-api-zeta.vercel.app │
                └──────┬───────────────┬───────┘
                       │               │
          ┌────────────▼───┐   ┌───────▼──────────────┐
          │  insighta-web  │   │    insighta-cli        │
          │  Next.js 14    │   │    Python CLI          │
          │  insighta-web- │   │    pip install         │
          │  ruddy.vercel  │   │    insighta            │
          │  .app          │   │                        │
          └────────────────┘   └────────────────────────┘
```

| Repo | Tech | URL |
|---|---|---|
| `profile-api` | FastAPI, Supabase, slowapi, python-jose | `https://profile-api-zeta.vercel.app` |
| `insighta-web` | Next.js 14, TypeScript, Tailwind CSS | `https://insighta-web-ruddy.vercel.app` |
| `insighta-cli` | Python, Click, Rich | `pip install insighta` |

**Supabase tables:**

| Table | Purpose |
|---|---|
| `profiles` | 2 026 name-enriched records (gender, age, country via external APIs) |
| `users` | One row per GitHub account; stores `role`, `is_active`, `github_id` |
| `refresh_tokens` | Opaque refresh tokens with TTL; deleted on use (rotation) |

---

## 2. Auth Flow

### Web flow

```
Browser                    insighta-web              profile-api             GitHub
  │                             │                         │                     │
  │  GET /                      │                         │                     │
  │────────────────────────────>│                         │                     │
  │  <login page>               │                         │                     │
  │<────────────────────────────│                         │                     │
  │                             │                         │                     │
  │  click "Continue with GitHub"                         │                     │
  │  GET profile-api-zeta.vercel.app/auth/github          │                     │
  │──────────────────────────────────────────────────────>│                     │
  │                             │           302 → github.com/login/oauth/...    │
  │<──────────────────────────────────────────────────────│                     │
  │                             │                         │                     │
  │  follow redirect            │                         │                     │
  │─────────────────────────────────────────────────────────────────────────────>
  │  user approves              │                         │                     │
  │<─────────────────────────────────────────────────────────────────────────────
  │  302 → profile-api/auth/github/callback?code=...      │                     │
  │──────────────────────────────────────────────────────>│                     │
  │                             │      exchange code for gh token               │
  │                             │      fetch /user, /user/emails                │
  │                             │      upsert users table                       │
  │                             │      issue JWT + refresh token                │
  │  302 → insighta-web/auth/callback?access_token=...&refresh_token=...        │
  │<──────────────────────────────────────────────────────│                     │
  │                             │                         │                     │
  │  GET /auth/callback?...     │                         │                     │
  │────────────────────────────>│                         │                     │
  │                             │  set HTTP-only cookies  │                     │
  │  302 → /dashboard           │  (access_token, refresh_token)                │
  │<────────────────────────────│                         │                     │
```

**Key points:**
- `GITHUB_REDIRECT_URI` = `https://profile-api-zeta.vercel.app/auth/github/callback` — the backend's own callback, not the frontend
- `FRONTEND_URL` = `https://insighta-web-ruddy.vercel.app/auth/callback` — where the backend redirects the user after issuing tokens
- The OAuth `state` parameter is a `secrets.token_urlsafe(16)` value stored in memory with a timestamp; it is **one-time use** (popped on first match) to prevent CSRF replay
- Tokens travel as query params only for the single redirect from backend → frontend; the frontend's `/auth/callback` route handler immediately moves them into HTTP-only cookies so they are never accessible to JavaScript

### CLI flow

```
Terminal            localhost:8888        profile-api          GitHub
  │                      │                    │                   │
  │  insighta login       │                    │                   │
  │  GET /auth/github?cli=true                 │                   │
  │──────────────────────────────────────────>│                   │
  │                       │  302 → github.com (with state)        │
  │<──────────────────────────────────────────│                   │
  │  open browser         │                    │                   │
  │──────────────────────────────────────────────────────────────>│
  │  user approves        │                    │                   │
  │<──────────────────────────────────────────────────────────────│
  │                                302 → profile-api/auth/github/callback
  │──────────────────────────────────────────>│                   │
  │                       │  issue tokens      │                   │
  │                       │  302 → localhost:8888/callback?...     │
  │  HTTP server captures │<──────────────────│                   │
  │  access_token +       │                    │                   │
  │  refresh_token        │                    │                   │
  │  save → ~/.insighta/credentials.json       │                   │
```

Passing `?cli=true` to `/auth/github` causes the backend to redirect to `http://localhost:8888/callback` instead of the web frontend. The CLI starts a temporary `HTTPServer` on port 8888 before opening the browser and shuts it down the moment the callback fires (timeout: 120 s).

---

## 3. Token Handling

### Access token

- Format: **JWT** (HS256)
- Payload: `{ sub: user_id, role: "analyst"|"admin", exp: now+3min }`
- Signed with `JWT_SECRET`
- **TTL: 3 minutes**
- Validated on every protected request by decoding the JWT locally — no database round-trip required

### Refresh token

- Format: **opaque** — `secrets.token_urlsafe(48)` (64 URL-safe characters)
- Stored in the `refresh_tokens` Supabase table with an `expires_at` timestamp
- **TTL: 5 minutes**
- **Rotated on every use**: the old record is deleted *before* the new pair is issued; a replay attacker who presents an already-used token receives `401 Invalid refresh token`

### Auto-refresh — web

Every API call goes through the `backendProxy()` helper in `lib/backend.ts` (Next.js Route Handlers):

```
client request
      │
      ▼
Next.js Route Handler
      │  reads access_token + refresh_token from HTTP-only cookies
      │
      ▼
POST profile-api (Authorization: Bearer <access_token>, X-API-Version: 1)
      │
      ├── 2xx → return response to client
      │
      └── 401 → POST /auth/refresh { refresh_token }
                      │
                      ├── 200 → overwrite both cookies, retry original request
                      │
                      └── 401 → clear cookies, return 401 to client
```

Route Handlers run server-side, so they can both read and overwrite HTTP-only cookies — the browser never sees the raw token values.

### Auto-refresh — CLI

`_request()` in `insighta/api.py` applies the same retry pattern:

```python
response = requests.request(method, url, headers=headers, ...)
if response.status_code == 401:
    new_tokens = _refresh(creds["refresh_token"])
    if new_tokens:
        save_credentials(new_tokens)          # persists to ~/.insighta/credentials.json
        response = requests.request(...)      # retry once with new access token
    else:
        raise APIError(0, "session_expired")
```

---

## 4. Role Enforcement

Two roles exist: **`analyst`** (assigned automatically on first login) and **`admin`** (set manually in Supabase).

### FastAPI dependency chain

```python
def get_current_user(credentials) -> dict:
    # 1. Validate Bearer JWT          → 401 if missing or invalid
    # 2. Fetch user row from Supabase → 401 if not found
    # 3. Check is_active == True      → 403 if inactive
    return user

def require_analyst(user = Depends(get_current_user)) -> dict:
    return user          # any valid, active user passes

def require_admin(user = Depends(get_current_user)) -> dict:
    if user["role"] != "admin":
        raise HTTPException(403, "Admin role required")
    return user
```

### Endpoint role matrix

| Method | Path | Minimum role |
|---|---|---|
| GET | `/auth/me` | any authenticated user |
| GET | `/api/profiles` | analyst |
| GET | `/api/profiles/search` | analyst |
| GET | `/api/profiles/export` | analyst |
| GET | `/api/profiles/{id}` | analyst |
| POST | `/api/profiles` | **admin** |
| PUT | `/api/profiles/{id}` | **admin** |
| DELETE | `/api/profiles/{id}` | **admin** |

### API version header

All `/api/*` routes additionally require:

```
X-API-Version: 1
```

Missing or wrong value → `400 API version header required`. This is enforced via a `require_api_version` dependency injected into every route, separate from the auth dependency.

### Next.js middleware

`middleware.ts` runs before every request to `/dashboard`, `/profiles`, `/search`, and `/account`. If the `access_token` cookie is absent the user is immediately redirected to `/`. This is a fast, visible guard; the authoritative enforcement happens in the FastAPI dependency chain on the backend.

---

## 5. Natural Language Parsing

`GET /api/profiles/search?q=<query>` uses a **rule-based regex parser** — no LLM or external NLP library.

### Parsing rules (evaluated in order)

| Input pattern | Extracted field | Example |
|---|---|---|
| Contains `female` | `gender=female` | `"female from nigeria"` |
| Contains `male` (checked *after* `female`) | `gender=male` | `"male above 30"` |
| Contains `young` | `min_age=16, max_age=24` | `"young female"` |
| Contains `child` | `age_group=child` | `"child from ghana"` |
| Contains `teenager` | `age_group=teenager` | `"teenager from kenya"` |
| Contains `adult` | `age_group=adult` | `"adult male"` |
| Contains `senior` | `age_group=senior` | `"senior from south africa"` |
| `above N` or `over N` | `min_age=N` | `"above 40"` |
| `below N` or `under N` | `max_age=N` | `"under 18"` |
| `from <country name>` | `country_id=<ISO-2 code>` | `"from nigeria"` → `NG` |

**Why `female` is checked before `male`:** the string `"female"` contains the substring `"male"`. Checking `female` first prevents a false positive.

**Country matching:** names are matched against a 55-entry African-country dictionary sorted by string length descending, so `"south africa"` matches before a bare `"africa"` would.

```python
# Example parse results
"female from nigeria above 25"
→ { gender: "female", country_id: "NG", min_age: 25 }

"young male from ghana"
→ { gender: "male", country_id: "GH", min_age: 16, max_age: 24 }

"senior from south africa"
→ { age_group: "senior", country_id: "ZA" }
```

The parsed parameters are fed directly into `build_filtered_query()` — the same function used by `GET /api/profiles` — so pagination, totals, and `total_pages` work identically.

---

## 6. API Endpoints

**Base URL:** `https://profile-api-zeta.vercel.app`

All `/api/*` endpoints require:
- `Authorization: Bearer <access_token>` header
- `X-API-Version: 1` header

Rate limits: **10 req/min** on `/auth/*` · **60 req/min** on `/api/*` (per IP, via slowapi).

All responses use the envelope:
```json
{ "status": "success" | "error", "data": ..., "message": "..." }
```

---

### Auth endpoints

#### `GET /auth/github`

Initiates GitHub OAuth. Redirects the browser to GitHub's authorize page.

| Param | Type | Default | Description |
|---|---|---|---|
| `cli` | bool | `false` | When `true`, GitHub redirects back to `http://localhost:8888/callback` instead of the web frontend |

```bash
# Web (browser navigation)
https://profile-api-zeta.vercel.app/auth/github

# CLI
https://profile-api-zeta.vercel.app/auth/github?cli=true
```

---

#### `GET /auth/github/callback`

Receives the `?code=...&state=...` from GitHub. Not called directly — GitHub redirects here automatically.

On success: `302` to `FRONTEND_URL?access_token=...&refresh_token=...` (or `localhost:8888` for CLI).

---

#### `GET /auth/me`

Returns the currently authenticated user's profile.

```bash
curl https://profile-api-zeta.vercel.app/auth/me \
  -H "Authorization: Bearer <access_token>"
```

```json
{
  "status": "success",
  "data": {
    "id": "019723ab-...",
    "username": "johndoe",
    "email": "john@example.com",
    "role": "analyst",
    "avatar_url": "https://avatars.githubusercontent.com/u/..."
  }
}
```

---

#### `POST /auth/refresh`

Exchange a refresh token for a new token pair. The presented token is immediately deleted.

```bash
curl -X POST https://profile-api-zeta.vercel.app/auth/refresh \
  -H "Content-Type: application/json" \
  -d '{"refresh_token": "<token>"}'
```

```json
{
  "status": "success",
  "access_token": "<new_jwt>",
  "refresh_token": "<new_opaque_token>"
}
```

Errors: `401` if token not found; `401` if token expired (record is still deleted).

---

#### `POST /auth/logout`

Invalidates the refresh token server-side.

```bash
curl -X POST https://profile-api-zeta.vercel.app/auth/logout \
  -H "Content-Type: application/json" \
  -d '{"refresh_token": "<token>"}'
```

```json
{ "status": "success", "message": "Logged out" }
```

---

### Profile endpoints

#### `GET /api/profiles`

Paginated list of profiles with optional filtering and sorting.

| Param | Type | Default | Description |
|---|---|---|---|
| `gender` | string | — | `male` or `female` |
| `age_group` | string | — | `child`, `teenager`, `adult`, `senior` |
| `country_id` | string | — | ISO 3166-1 alpha-2 code, e.g. `NG` |
| `min_age` | int | — | Minimum age (inclusive) |
| `max_age` | int | — | Maximum age (inclusive) |
| `min_gender_probability` | float | — | e.g. `0.9` |
| `min_country_probability` | float | — | e.g. `0.5` |
| `sort_by` | string | `created_at` | `age` · `created_at` · `gender_probability` |
| `order` | string | `asc` | `asc` or `desc` |
| `page` | int | `1` | Page number |
| `limit` | int | `10` | Results per page (max 50) |

```bash
curl "https://profile-api-zeta.vercel.app/api/profiles?gender=female&country_id=NG&page=1&limit=5" \
  -H "Authorization: Bearer <token>" \
  -H "X-API-Version: 1"
```

```json
{
  "status": "success",
  "page": 1,
  "limit": 5,
  "total": 342,
  "total_pages": 69,
  "links": {
    "self": "/api/profiles?page=1&limit=5",
    "next": "/api/profiles?page=2&limit=5",
    "prev": null
  },
  "data": [
    {
      "id": "019723ab-...",
      "name": "Amaka",
      "gender": "female",
      "gender_probability": 0.98,
      "age": 32,
      "age_group": "adult",
      "country_id": "NG",
      "country_name": "Nigeria",
      "country_probability": 0.74,
      "created_at": "2026-04-15T10:32:00Z"
    }
  ]
}
```

---

#### `GET /api/profiles/search`

Natural language search. The query is parsed server-side into filter parameters.

| Param | Type | Default | Description |
|---|---|---|---|
| `q` | string | **required** | Natural language query |
| `page` | int | `1` | Page number |
| `limit` | int | `10` | Results per page (max 50) |

```bash
curl "https://profile-api-zeta.vercel.app/api/profiles/search?q=female+from+nigeria+above+25" \
  -H "Authorization: Bearer <token>" \
  -H "X-API-Version: 1"
```

Returns the same paginated envelope as `GET /api/profiles`. Returns `400` if the query cannot be interpreted.

---

#### `GET /api/profiles/export`

Downloads all matching profiles as a timestamped CSV file. Accepts the same filter params as `GET /api/profiles` but no pagination — returns all matching rows.

```bash
curl "https://profile-api-zeta.vercel.app/api/profiles/export?country_id=GH" \
  -H "Authorization: Bearer <token>" \
  -H "X-API-Version: 1" \
  -o profiles.csv
```

Response: `text/csv` · `Content-Disposition: attachment; filename="profiles_20260429T120000Z.csv"`

CSV columns: `id, name, gender, gender_probability, age, age_group, country_id, country_name, country_probability, created_at`

---

#### `GET /api/profiles/{id}`

Single profile by UUID.

```bash
curl "https://profile-api-zeta.vercel.app/api/profiles/019723ab-..." \
  -H "Authorization: Bearer <token>" \
  -H "X-API-Version: 1"
```

```json
{
  "status": "success",
  "data": {
    "id": "019723ab-...",
    "name": "Kwame",
    "gender": "male",
    "gender_probability": 0.98,
    "age": 34,
    "age_group": "adult",
    "country_id": "GH",
    "country_name": "Ghana",
    "country_probability": 0.87,
    "created_at": "2026-04-15T10:32:00Z"
  }
}
```

---

#### `POST /api/profiles` — admin only

Create a new profile. The name is enriched automatically via genderize.io, agify.io, and nationalize.io (parallel async requests).

```bash
curl -X POST "https://profile-api-zeta.vercel.app/api/profiles" \
  -H "Authorization: Bearer <admin_token>" \
  -H "X-API-Version: 1" \
  -H "Content-Type: application/json" \
  -d '{"name": "Fatima"}'
```

Returns `201` with the created profile. If the name already exists, returns `200` with the existing record and `"message": "Profile already exists"`.

---

#### `PUT /api/profiles/{id}` — admin only

Partial update. Any subset of fields may be provided; omitted fields are not changed.

```bash
curl -X PUT "https://profile-api-zeta.vercel.app/api/profiles/019723ab-..." \
  -H "Authorization: Bearer <admin_token>" \
  -H "X-API-Version: 1" \
  -H "Content-Type: application/json" \
  -d '{"age": 30, "age_group": "adult"}'
```

---

#### `DELETE /api/profiles/{id}` — admin only

```bash
curl -X DELETE "https://profile-api-zeta.vercel.app/api/profiles/019723ab-..." \
  -H "Authorization: Bearer <admin_token>" \
  -H "X-API-Version: 1"
```

Returns `204 No Content`.

---

### Error responses

All errors follow:

```json
{ "status": "error", "message": "Human-readable description" }
```

| Status | Meaning |
|---|---|
| `400` | Missing or invalid parameter / wrong API version header |
| `401` | Missing, invalid, or expired token |
| `403` | Authenticated but insufficient role / inactive account |
| `404` | Resource not found |
| `429` | Rate limit exceeded |
| `500` | Internal server error |

---

## 7. CLI Usage

### Install

```bash
pip install insighta

# Or from source
cd insighta-cli && pip install -e .
```

Verify: `insighta --version`

---

### `insighta login`

Opens GitHub OAuth in your default browser. A temporary HTTP server on port 8888 captures the tokens from the callback redirect. Credentials are saved to `~/.insighta/credentials.json`.

```
$ insighta login
Opening GitHub login in your browser...
Waiting for callback on http://localhost:8888/callback ...
✓ Logged in as @johndoe
```

---

### `insighta logout`

Invalidates the refresh token on the server and deletes `~/.insighta/credentials.json`.

```
$ insighta logout
✓ Logged out.
```

---

### `insighta whoami`

Displays the current user as a Rich panel.

```
$ insighta whoami
╭─ Logged-in user ─────────────────────╮
│ Username    @johndoe                  │
│ Email       john@example.com          │
│ Role        analyst                   │
│ ID          019723ab-...              │
╰───────────────────────────────────────╯
```

---

### `insighta profiles list`

```
Usage: insighta profiles list [OPTIONS]

Options:
  --gender TEXT                          male / female
  --country TEXT                         ISO country code, e.g. NG
  --age-group TEXT                       child / teenager / adult / senior
  --min-age INTEGER                      Minimum age (inclusive)
  --max-age INTEGER                      Maximum age (inclusive)
  --min-gender-prob FLOAT                Minimum gender probability (0-1)
  --min-country-prob FLOAT               Minimum country probability (0-1)
  --sort-by [age|created_at|gender_probability]
  --order [asc|desc]
  --page INTEGER                         Page number (default: 1)
  --limit INTEGER                        Results per page, max 50 (default: 10)
```

```bash
# Female profiles from Nigeria, sorted by age descending
insighta profiles list --gender female --country NG --sort-by age --order desc

# Adults with high gender confidence, page 2
insighta profiles list --age-group adult --min-gender-prob 0.95 --limit 20 --page 2
```

---

### `insighta profiles get <id>`

```bash
insighta profiles get 019723ab-1234-7000-abcd-000000000001
```

```
╭─ Profile ──────────────────────────────╮
│ ID         019723ab-...                │
│ Name       Kwame                       │
│ Gender     male (0.98)                 │
│ Age        34 (adult)                  │
│ Country    GH — Ghana (0.87)           │
│ Created    2026-04-15T10:32:00Z        │
╰────────────────────────────────────────╯
```

---

### `insighta profiles search <query>`

Wrap multi-word queries in quotes.

```bash
insighta profiles search "female from nigeria above 25"
insighta profiles search "young male from ghana"
insighta profiles search "senior from south africa" --page 2 --limit 20
```

---

### `insighta profiles create` — admin only

Creates a profile and auto-enriches it via external APIs.

```bash
insighta profiles create --name "Fatima"
```

```
✓ Profile created.
╭─ Profile ──────────────────────────────╮
│ Name       Fatima                      │
│ Gender     female (0.97)               │
│ Age        28 (adult)                  │
│ Country    NG — Nigeria (0.81)         │
╰────────────────────────────────────────╯
```

---

### `insighta profiles export`

Exports matching profiles to a CSV file in the current working directory.

```bash
# Export everything
insighta profiles export

# Export with filters
insighta profiles export --gender female --country NG
insighta profiles export --age-group senior --min-age 60
```

```
✓ Exported to profiles_20260429T120000Z.csv
```

---

## 8. Running Locally

### profile-api

```bash
cd profile-api
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env             # fill in the values from §9
uvicorn api.index:app --reload --port 8000
```

Interactive docs: `http://localhost:8000/docs`

Seed the database (requires `seed_profiles.json` in the project root):

```bash
python seed.py
```

### insighta-web

```bash
cd insighta-web
npm install
npm run dev
```

Open `http://localhost:3000`. The backend URL is hardcoded to the production API — no extra env vars needed for local dev.

### insighta-cli

```bash
cd insighta-cli
pip install -e .
insighta --version
insighta login
```

---

## 9. Environment Variables

### profile-api

| Variable | Required | Description |
|---|---|---|
| `SUPABASE_URL` | yes | Supabase project URL |
| `SUPABASE_ANON_KEY` | yes | Supabase anon/public key |
| `GITHUB_CLIENT_ID` | yes | GitHub OAuth App client ID |
| `GITHUB_CLIENT_SECRET` | yes | GitHub OAuth App client secret |
| `GITHUB_REDIRECT_URI` | yes | The **backend's own** OAuth callback: `https://profile-api-zeta.vercel.app/auth/github/callback`. Must also be registered in the GitHub OAuth App settings under "Authorization callback URL". |
| `JWT_SECRET` | yes | Secret for signing/verifying JWTs (HS256). Generate with `openssl rand -hex 32`. |
| `FRONTEND_URL` | yes | Where the backend redirects after OAuth: `https://insighta-web-ruddy.vercel.app/auth/callback` |

> **Important:** `GITHUB_REDIRECT_URI` is the backend's callback URL, not the frontend's. The frontend URL is stored separately in `FRONTEND_URL`.

### insighta-web

No `.env` file required. The backend URL is hardcoded. Tokens are stored in HTTP-only cookies server-side — no secrets are exposed to the browser.

### insighta-cli

No `.env` file. After `insighta login`, credentials are persisted to `~/.insighta/credentials.json` and auto-refreshed transparently on every command.

---

## CI

GitHub Actions runs on every PR to `main`:

| Repo | Jobs |
|---|---|
| `insighta-web` | `npm run lint` → `npm run build` |
| `insighta-cli` | `flake8 insighta/` → `python -m build` |
