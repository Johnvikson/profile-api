# profile-api

A REST API for user profiles built with **FastAPI** and **Supabase**, deployed on **Vercel**.  
Profiles are enriched automatically using [Genderize](https://genderize.io), [Agify](https://agify.io), and [Nationalize](https://nationalize.io).

---

## Table of contents

- [Local development](#local-development)
- [Database setup](#database-setup)
- [Seeding the database](#seeding-the-database)
- [Endpoints](#endpoints)
  - [GET /api/profiles](#get-apiprofiles)
  - [GET /api/profiles/search](#get-apiprofilessearch)
  - [GET /api/profiles/:id](#get-apiprofilesid)
  - [POST /api/profiles](#post-apiprofiles)
  - [PUT /api/profiles/:id](#put-apiprofilesid)
  - [DELETE /api/profiles/:id](#delete-apiprofilesid)
- [Error responses](#error-responses)
- [Deployment](#deployment)

---

## Local development

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Copy and fill in environment variables
cp .env.example .env
# Edit .env — set SUPABASE_URL and SUPABASE_ANON_KEY

# 3. Start the dev server
uvicorn api.index:app --reload
```

The API will be available at `http://localhost:8000`.

---

## Database setup

Run this SQL in the Supabase SQL editor to add the `country_name` column (safe to run more than once):

```sql
ALTER TABLE public.profiles ADD COLUMN IF NOT EXISTS country_name VARCHAR;
```

---

## Seeding the database

Copy `seed_profiles.json` to the project root, then run:

```bash
python seed.py
```

The script:
- Reads `seed_profiles.json` from the project root
- Generates a UUID v7 `id` for every profile
- Upserts all profiles into Supabase on the `name` column (re-running is safe)
- Prints progress every 50 records

---

## Endpoints

### GET /api/profiles

List profiles with optional filters, sorting, and pagination.

#### Query parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `gender` | string | — | Filter by gender (`male` / `female`, case-insensitive) |
| `age_group` | string | — | Filter by age group (`child` / `teenager` / `adult` / `senior`, case-insensitive) |
| `country_id` | string | — | Filter by ISO 3166-1 alpha-2 country code (case-insensitive) |
| `min_age` | integer | — | Minimum age (inclusive) |
| `max_age` | integer | — | Maximum age (inclusive) |
| `min_gender_probability` | float | — | Minimum gender probability (0–1) |
| `min_country_probability` | float | — | Minimum country probability (0–1) |
| `sort_by` | string | `created_at` | Sort column: `age` \| `created_at` \| `gender_probability` |
| `order` | string | `asc` | Sort direction: `asc` \| `desc` |
| `page` | integer | `1` | Page number |
| `limit` | integer | `10` | Page size (max 50) |

#### Example request

```
GET /api/profiles?gender=female&country_id=NG&sort_by=age&order=desc&page=1&limit=5
```

#### Example response

```json
{
  "status": "success",
  "page": 1,
  "limit": 5,
  "total": 312,
  "data": [
    {
      "id": "01960000-0000-7000-8000-000000000001",
      "name": "Amara",
      "gender": "female",
      "gender_probability": 0.98,
      "sample_size": 4201,
      "age": 54,
      "age_group": "adult",
      "country_id": "NG",
      "country_probability": 0.71,
      "country_name": "Nigeria",
      "created_at": "2025-01-15T10:30:00Z"
    }
  ]
}
```

---

### GET /api/profiles/search

Natural language search. No AI — rule-based pattern matching only.

#### Query parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `q` | string | **required** | Natural language query (see patterns below) |
| `page` | integer | `1` | Page number |
| `limit` | integer | `10` | Page size (max 50) |

#### Supported patterns

| Pattern | Effect |
|---------|--------|
| `male` / `female` | Sets gender filter |
| `young` | Sets `min_age=16`, `max_age=24` |
| `child` / `teenager` / `adult` / `senior` | Sets age_group filter |
| `above X` / `over X` | Sets `min_age=X` |
| `below X` / `under X` | Sets `max_age=X` |
| `from [country name]` | Looks up ISO country code from built-in dictionary |

Patterns are combinable. Example: `"female from nigeria above 25"` applies gender, country, and min_age filters simultaneously.

#### Supported country names (sample)

Nigeria, Ghana, Kenya, Tanzania, Angola, Cameroon, Ethiopia, Uganda, Senegal, Mali, South Africa, Egypt, Morocco, Algeria, Tunisia, Libya, Sudan, South Sudan, Somalia, Mozambique, Madagascar, Zimbabwe, Zambia, Malawi, Botswana, Namibia, Rwanda, Burundi, Congo, DRC, Ivory Coast, Burkina Faso, Niger, Chad, Gabon, Benin, Togo, Guinea, Sierra Leone, Liberia, Gambia, Cape Verde, Mauritania, Eritrea, Djibouti, Comoros, Mauritius, Lesotho, Eswatini — and more.

#### Example requests

```
GET /api/profiles/search?q=female+from+nigeria&page=1&limit=10
GET /api/profiles/search?q=male+above+30&limit=20
GET /api/profiles/search?q=young+female+from+ghana
GET /api/profiles/search?q=senior+male+from+south+africa
```

#### Example response

```json
{
  "status": "success",
  "page": 1,
  "limit": 10,
  "total": 87,
  "data": [...]
}
```

#### Error — uninterpretable query

```json
{
  "status": "error",
  "message": "Unable to interpret query"
}
```

---

### GET /api/profiles/:id

Fetch a single profile by its UUID.

#### Example request

```
GET /api/profiles/01960000-0000-7000-8000-000000000001
```

#### Example response

```json
{
  "status": "success",
  "data": {
    "id": "01960000-0000-7000-8000-000000000001",
    "name": "Amara",
    "gender": "female",
    "gender_probability": 0.98,
    "sample_size": 4201,
    "age": 54,
    "age_group": "adult",
    "country_id": "NG",
    "country_probability": 0.71,
    "country_name": "Nigeria",
    "created_at": "2025-01-15T10:30:00Z"
  }
}
```

---

### POST /api/profiles

Create a new profile. The name is enriched automatically via third-party APIs.

#### Request body

```json
{ "name": "Amara" }
```

#### Example response (201 Created)

```json
{
  "status": "success",
  "data": {
    "id": "01960000-0000-7000-8000-000000000002",
    "name": "Amara",
    "gender": "female",
    "gender_probability": 0.98,
    "sample_size": 4201,
    "age": 54,
    "age_group": "adult",
    "country_id": "NG",
    "country_probability": 0.71,
    "country_name": null,
    "created_at": "2025-01-15T10:30:00Z"
  }
}
```

If the name already exists, a `200` is returned with the existing profile and `"message": "Profile already exists"`.

---

### PUT /api/profiles/:id

Update one or more fields on an existing profile.

#### Request body (all fields optional)

```json
{
  "name": "Amara",
  "gender": "female",
  "gender_probability": 0.98,
  "sample_size": 4201,
  "age": 54,
  "age_group": "adult",
  "country_id": "NG",
  "country_probability": 0.71,
  "country_name": "Nigeria"
}
```

#### Example response

```json
{
  "status": "success",
  "data": { ... }
}
```

---

### DELETE /api/profiles/:id

Delete a profile. Returns `204 No Content`.

---

## Error responses

All errors follow this shape:

```json
{
  "status": "error",
  "message": "Human-readable description"
}
```

| Status | Meaning |
|--------|---------|
| `400` | Missing / invalid parameter |
| `404` | Profile not found |
| `500` | Internal server error |

Every response (success or error) includes the header `Access-Control-Allow-Origin: *`.

---

## Deployment

Deploy to Vercel via the CLI or dashboard. Set these environment variables in your Vercel project:

| Variable | Description |
|----------|-------------|
| `SUPABASE_URL` | Your Supabase project URL |
| `SUPABASE_ANON_KEY` | Your Supabase anonymous key |
