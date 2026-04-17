from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from postgrest.exceptions import APIError
from supabase import create_client, Client
from typing import Optional
from datetime import datetime, timezone
from dotenv import load_dotenv
import asyncio
import httpx
import os
import random

load_dotenv()


def uuid7() -> str:
    ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    rand_a = random.getrandbits(12)
    rand_b = random.getrandbits(62)
    value = (ms & 0xFFFFFFFFFFFF) << 80
    value |= 0x7 << 76          # version 7
    value |= rand_a << 64
    value |= 0b10 << 62         # variant
    value |= rand_b
    hex_str = f"{value:032x}"
    return f"{hex_str[:8]}-{hex_str[8:12]}-{hex_str[12:16]}-{hex_str[16:20]}-{hex_str[20:]}"


CORS_HEADERS = {"Access-Control-Allow-Origin": "*"}


def normalize(row: dict) -> dict:
    if row.get("created_at"):
        dt = datetime.fromisoformat(row["created_at"])
        row["created_at"] = dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    if row.get("gender_probability") is not None:
        row["gender_probability"] = float(row["gender_probability"])
    return row

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


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
        headers=CORS_HEADERS,
    )


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
        headers=CORS_HEADERS,
    )


class ProfileCreate(BaseModel):
    name: str
    gender: Optional[str] = None
    gender_probability: Optional[float] = None
    sample_size: Optional[int] = None
    age: Optional[int] = None
    age_group: Optional[str] = None
    country_id: Optional[str] = None
    country_probability: Optional[float] = None


class ProfileUpdate(BaseModel):
    name: Optional[str] = None
    gender: Optional[str] = None
    gender_probability: Optional[float] = None
    sample_size: Optional[int] = None
    age: Optional[int] = None
    age_group: Optional[str] = None
    country_id: Optional[str] = None
    country_probability: Optional[float] = None


@app.get("/api/profiles")
def list_profiles():
    result = supabase.table("profiles").select("*").execute()
    return JSONResponse(content=result.data, headers=CORS_HEADERS)


@app.get("/api/profiles/{profile_id}")
def get_profile(profile_id: str):
    try:
        result = supabase.table("profiles").select("*").eq("id", profile_id).single().execute()
    except APIError as e:
        if e.code == "PGRST116":
            raise HTTPException(status_code=404, detail="Profile not found")
        raise HTTPException(status_code=500, detail=e.message)
    return JSONResponse(content=result.data, headers=CORS_HEADERS)


async def enrich(name: str) -> dict:
    async with httpx.AsyncClient(timeout=10) as client:
        gender_req = client.get(f"https://api.genderize.io/?name={name}")
        age_req    = client.get(f"https://api.agify.io/?name={name}")
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
            existing = supabase.table("profiles").select("*").eq("name", profile.name).single().execute()
            return JSONResponse(
                status_code=200,
                content={"status": "success", "message": "Profile already exists", "data": normalize(existing.data)},
                headers=CORS_HEADERS,
            )
        raise HTTPException(status_code=500, detail=e.message)
    return JSONResponse(status_code=201, content=normalize(result.data[0]), headers=CORS_HEADERS)


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
    return JSONResponse(content=result.data[0], headers=CORS_HEADERS)


@app.delete("/api/profiles/{profile_id}")
def delete_profile(profile_id: str):
    supabase.table("profiles").delete().eq("id", profile_id).execute()
    return JSONResponse(status_code=204, content=None, headers=CORS_HEADERS)
