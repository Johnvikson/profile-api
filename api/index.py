from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from postgrest.exceptions import APIError
from supabase import create_client, Client
from typing import Optional
from datetime import datetime, timezone
from dotenv import load_dotenv
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


@app.post("/api/profiles", status_code=201)
def create_profile(profile: ProfileCreate):
    payload = {"id": uuid7(), **profile.model_dump()}
    try:
        result = supabase.table("profiles").insert(payload).execute()
    except APIError as e:
        if e.code == "23505":
            raise HTTPException(status_code=409, detail="A profile with that name already exists")
        raise HTTPException(status_code=500, detail=e.message)
    return JSONResponse(status_code=201, content=result.data[0], headers=CORS_HEADERS)


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
