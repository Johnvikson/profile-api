from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, field_validator
from postgrest.exceptions import APIError
from supabase import create_client, Client
from typing import Optional
from datetime import datetime, timezone
from dotenv import load_dotenv
import asyncio
import httpx
import os
import random
import re

load_dotenv()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def uuid7() -> str:
    ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    rand_a = random.getrandbits(12)
    rand_b = random.getrandbits(62)
    value = (ms & 0xFFFFFFFFFFFF) << 80
    value |= 0x7 << 76
    value |= rand_a << 64
    value |= 0b10 << 62
    value |= rand_b
    hex_str = f"{value:032x}"
    return f"{hex_str[:8]}-{hex_str[8:12]}-{hex_str[12:16]}-{hex_str[16:20]}-{hex_str[20:]}"


def normalize(row: dict) -> dict:
    if row.get("created_at"):
        dt = datetime.fromisoformat(row["created_at"])
        row["created_at"] = dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    if row.get("gender_probability") is not None:
        row["gender_probability"] = float(row["gender_probability"])
    if row.get("country_probability") is not None:
        row["country_probability"] = float(row["country_probability"])
    return row


CORS_HEADERS = {"Access-Control-Allow-Origin": "*"}

VALID_SORT_COLUMNS = {
    "age": "age",
    "created_at": "created_at",
    "gender_probability": "gender_probability",
}

COUNTRY_MAP: dict[str, str] = {
    "nigeria": "NG",
    "ghana": "GH",
    "kenya": "KE",
    "tanzania": "TZ",
    "angola": "AO",
    "cameroon": "CM",
    "ethiopia": "ET",
    "uganda": "UG",
    "senegal": "SN",
    "mali": "ML",
    "south africa": "ZA",
    "egypt": "EG",
    "morocco": "MA",
    "algeria": "DZ",
    "tunisia": "TN",
    "libya": "LY",
    "sudan": "SD",
    "south sudan": "SS",
    "somalia": "SO",
    "mozambique": "MZ",
    "madagascar": "MG",
    "zimbabwe": "ZW",
    "zambia": "ZM",
    "malawi": "MW",
    "botswana": "BW",
    "namibia": "NA",
    "rwanda": "RW",
    "burundi": "BI",
    "congo": "CG",
    "democratic republic of congo": "CD",
    "dr congo": "CD",
    "drc": "CD",
    "ivory coast": "CI",
    "cote d'ivoire": "CI",
    "burkina faso": "BF",
    "niger": "NE",
    "chad": "TD",
    "central african republic": "CF",
    "gabon": "GA",
    "equatorial guinea": "GQ",
    "benin": "BJ",
    "togo": "TG",
    "guinea": "GN",
    "guinea-bissau": "GW",
    "sierra leone": "SL",
    "liberia": "LR",
    "gambia": "GM",
    "cape verde": "CV",
    "mauritania": "MR",
    "eritrea": "ER",
    "djibouti": "DJ",
    "comoros": "KM",
    "mauritius": "MU",
    "seychelles": "SC",
    "lesotho": "LS",
    "eswatini": "SZ",
    "swaziland": "SZ",
}


def parse_search_query(q: str) -> dict | None:
    """
    Rule-based natural language parser. Returns a dict of filter params
    or None if the query could not be interpreted.
    """
    params: dict = {}
    text = q.lower().strip()

    # gender — check "female" before "male" to avoid substring collision
    if "female" in text:
        params["gender"] = "female"
    elif "male" in text:
        params["gender"] = "male"

    # age_group keywords
    if "young" in text:
        params["min_age"] = 16
        params["max_age"] = 24
    elif "child" in text:
        params["age_group"] = "child"
    elif "teenager" in text:
        params["age_group"] = "teenager"
    elif "adult" in text:
        params["age_group"] = "adult"
    elif "senior" in text:
        params["age_group"] = "senior"

    # above X / over X → min_age (may coexist with age_group)
    m = re.search(r"\b(?:above|over)\s+(\d+)", text)
    if m:
        params["min_age"] = int(m.group(1))

    # below X / under X → max_age
    m = re.search(r"\b(?:below|under)\s+(\d+)", text)
    if m:
        params["max_age"] = int(m.group(1))

    # from [country name] — greedy capture, trimmed before known keywords
    m = re.search(
        r"\bfrom\s+(.+?)(?=\s+(?:above|over|below|under|male|female|young|child"
        r"|teenager|adult|senior|aged?|who|and|with)|$)",
        text,
    )
    if m:
        raw = m.group(1).strip()
        # longest key match first
        matched = next(
            (COUNTRY_MAP[k] for k in sorted(COUNTRY_MAP, key=len, reverse=True) if raw == k or raw.startswith(k)),
            None,
        )
        if matched:
            params["country_id"] = matched

    return params if params else None


# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

supabase: Client = create_client(
    os.environ["SUPABASE_URL"],
    os.environ["SUPABASE_ANON_KEY"],
)


# ---------------------------------------------------------------------------
# Exception handlers
# ---------------------------------------------------------------------------

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    errors = exc.errors()
    msg = errors[0]["msg"] if errors else "Validation error"
    msg = msg.removeprefix("Value error, ")
    return JSONResponse(
        status_code=400,
        content={"status": "error", "message": msg},
        headers=CORS_HEADERS,
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"status": "error", "message": exc.detail},
        headers=CORS_HEADERS,
    )


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={"status": "error", "message": "Internal server error"},
        headers=CORS_HEADERS,
    )


# ---------------------------------------------------------------------------
# Request/response models
# ---------------------------------------------------------------------------

class ProfileCreate(BaseModel):
    name: str

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("name must not be empty")
        return v


class ProfileUpdate(BaseModel):
    name: Optional[str] = None
    gender: Optional[str] = None
    gender_probability: Optional[float] = None
    sample_size: Optional[int] = None
    age: Optional[int] = None
    age_group: Optional[str] = None
    country_id: Optional[str] = None
    country_probability: Optional[float] = None
    country_name: Optional[str] = None


# ---------------------------------------------------------------------------
# Shared query builder
# ---------------------------------------------------------------------------

def build_filtered_query(
    *,
    gender: Optional[str],
    age_group: Optional[str],
    country_id: Optional[str],
    min_age: Optional[int],
    max_age: Optional[int],
    min_gender_probability: Optional[float],
    min_country_probability: Optional[float],
    sort_by: str,
    order: str,
    page: int,
    limit: int,
):
    sort_col = VALID_SORT_COLUMNS.get(sort_by, "created_at")
    offset = (page - 1) * limit

    query = supabase.table("profiles").select("*", count="exact")

    if gender:
        query = query.ilike("gender", gender)
    if age_group:
        query = query.ilike("age_group", age_group)
    if country_id:
        query = query.ilike("country_id", country_id)
    if min_age is not None:
        query = query.gte("age", min_age)
    if max_age is not None:
        query = query.lte("age", max_age)
    if min_gender_probability is not None:
        query = query.gte("gender_probability", min_gender_probability)
    if min_country_probability is not None:
        query = query.gte("country_probability", min_country_probability)

    query = query.order(sort_col, desc=(order.lower() == "desc"))
    query = query.range(offset, offset + limit - 1)

    return query


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/api/profiles/search")
def search_profiles(
    q: Optional[str] = None,
    page: int = 1,
    limit: int = 10,
):
    if not q or not q.strip():
        return JSONResponse(
            status_code=400,
            content={"status": "error", "message": "Missing required parameter: q"},
            headers=CORS_HEADERS,
        )

    limit = min(limit, 50)
    if page < 1:
        page = 1

    params = parse_search_query(q)
    if params is None:
        return JSONResponse(
            status_code=400,
            content={"status": "error", "message": "Unable to interpret query"},
            headers=CORS_HEADERS,
        )

    query = build_filtered_query(
        gender=params.get("gender"),
        age_group=params.get("age_group"),
        country_id=params.get("country_id"),
        min_age=params.get("min_age"),
        max_age=params.get("max_age"),
        min_gender_probability=None,
        min_country_probability=None,
        sort_by="created_at",
        order="asc",
        page=page,
        limit=limit,
    )

    result = query.execute()
    profiles = [normalize(r) for r in result.data]

    return JSONResponse(
        content={
            "status": "success",
            "page": page,
            "limit": limit,
            "total": result.count or 0,
            "data": profiles,
        },
        headers=CORS_HEADERS,
    )


@app.get("/api/profiles")
def list_profiles(
    gender: Optional[str] = None,
    age_group: Optional[str] = None,
    country_id: Optional[str] = None,
    min_age: Optional[int] = None,
    max_age: Optional[int] = None,
    min_gender_probability: Optional[float] = None,
    min_country_probability: Optional[float] = None,
    sort_by: str = "created_at",
    order: str = "asc",
    page: int = 1,
    limit: int = 10,
):
    limit = min(limit, 50)
    if page < 1:
        page = 1

    if sort_by not in VALID_SORT_COLUMNS:
        return JSONResponse(
            status_code=400,
            content={"status": "error", "message": f"Invalid sort_by value. Must be one of: {', '.join(VALID_SORT_COLUMNS)}"},
            headers=CORS_HEADERS,
        )

    if order.lower() not in ("asc", "desc"):
        return JSONResponse(
            status_code=400,
            content={"status": "error", "message": "Invalid order value. Must be 'asc' or 'desc'"},
            headers=CORS_HEADERS,
        )

    query = build_filtered_query(
        gender=gender,
        age_group=age_group,
        country_id=country_id,
        min_age=min_age,
        max_age=max_age,
        min_gender_probability=min_gender_probability,
        min_country_probability=min_country_probability,
        sort_by=sort_by,
        order=order,
        page=page,
        limit=limit,
    )

    result = query.execute()
    profiles = [normalize(r) for r in result.data]

    return JSONResponse(
        content={
            "status": "success",
            "page": page,
            "limit": limit,
            "total": result.count or 0,
            "data": profiles,
        },
        headers=CORS_HEADERS,
    )


@app.get("/api/profiles/{profile_id}")
def get_profile(profile_id: str):
    try:
        result = (
            supabase.table("profiles")
            .select("*")
            .eq("id", profile_id)
            .single()
            .execute()
        )
    except APIError as e:
        if e.code == "PGRST116":
            raise HTTPException(status_code=404, detail="Profile not found")
        raise HTTPException(status_code=500, detail=e.message)
    return JSONResponse(
        content={"status": "success", "data": normalize(result.data)},
        headers=CORS_HEADERS,
    )


async def enrich(name: str) -> dict:
    async with httpx.AsyncClient(timeout=10) as client:
        gender_req  = client.get(f"https://api.genderize.io/?name={name}")
        age_req     = client.get(f"https://api.agify.io/?name={name}")
        country_req = client.get(f"https://api.nationalize.io/?name={name}")
        gender_res, age_res, country_res = await asyncio.gather(gender_req, age_req, country_req)

    gender_data  = gender_res.json()
    age_data     = age_res.json()
    country_data = country_res.json()

    top_country = None
    top_probability = None
    countries = country_data.get("country", [])
    if countries:
        top = max(countries, key=lambda c: c["probability"])
        top_country = top["country_id"]
        top_probability = top["probability"]

    age = age_data.get("age")
    if age is None:
        age_group = None
    elif age <= 12:
        age_group = "child"
    elif age <= 19:
        age_group = "teenager"
    elif age <= 59:
        age_group = "adult"
    else:
        age_group = "senior"

    return {
        "gender":              gender_data.get("gender"),
        "gender_probability":  gender_data.get("probability"),
        "sample_size":         gender_data.get("count"),
        "age":                 age,
        "age_group":           age_group,
        "country_id":          top_country,
        "country_probability": top_probability,
    }


@app.post("/api/profiles", status_code=201)
async def create_profile(profile: ProfileCreate):
    enriched = await enrich(profile.name)
    payload = {"id": uuid7(), "name": profile.name, **enriched}
    try:
        result = supabase.table("profiles").insert(payload).execute()
    except APIError as e:
        if e.code == "23505":
            existing = (
                supabase.table("profiles")
                .select("*")
                .eq("name", profile.name)
                .single()
                .execute()
            )
            return JSONResponse(
                status_code=200,
                content={
                    "status": "success",
                    "message": "Profile already exists",
                    "data": normalize(existing.data),
                },
                headers=CORS_HEADERS,
            )
        raise HTTPException(status_code=500, detail=e.message)
    return JSONResponse(
        status_code=201,
        content={"status": "success", "data": normalize(result.data[0])},
        headers=CORS_HEADERS,
    )


@app.put("/api/profiles/{profile_id}")
def update_profile(profile_id: str, profile: ProfileUpdate):
    updates = {k: v for k, v in profile.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")
    try:
        result = supabase.table("profiles").update(updates).eq("id", profile_id).execute()
    except APIError as e:
        raise HTTPException(status_code=500, detail=e.message)
    if not result.data:
        raise HTTPException(status_code=404, detail="Profile not found")
    return JSONResponse(
        content={"status": "success", "data": normalize(result.data[0])},
        headers=CORS_HEADERS,
    )


@app.delete("/api/profiles/{profile_id}")
def delete_profile(profile_id: str):
    supabase.table("profiles").delete().eq("id", profile_id).execute()
    return JSONResponse(status_code=204, content=None, headers=CORS_HEADERS)
