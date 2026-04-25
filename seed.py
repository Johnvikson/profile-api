"""
Seed script — reads seed_profiles.json and upserts all profiles into Supabase.
Generates a UUID v7 id for each profile. Re-running is safe (upsert on name).

Usage:
    python seed.py
"""

import json
import os
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

SEED_FILE = Path(__file__).parent / "seed_profiles.json"
BATCH_SIZE = 50


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


def main() -> None:
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_ANON_KEY")
    if not url or not key:
        print("Error: SUPABASE_URL and SUPABASE_ANON_KEY must be set in .env", file=sys.stderr)
        sys.exit(1)

    if not SEED_FILE.exists():
        print(f"Error: {SEED_FILE} not found. Copy seed_profiles.json to the project root first.", file=sys.stderr)
        sys.exit(1)

    supabase = create_client(url, key)

    with open(SEED_FILE, encoding="utf-8") as f:
        raw = json.load(f)

    profiles = raw["profiles"] if isinstance(raw, dict) and "profiles" in raw else raw

    total = len(profiles)
    print(f"Loaded {total} profiles from {SEED_FILE.name}")

    inserted = 0
    skipped = 0

    for i in range(0, total, BATCH_SIZE):
        batch_raw = profiles[i : i + BATCH_SIZE]
        batch = []
        for p in batch_raw:
            record = {**p, "id": uuid7()}
            record.setdefault("name", None)
            batch.append(record)

        result = (
            supabase.table("profiles")
            .upsert(batch, on_conflict="name", ignore_duplicates=False)
            .execute()
        )

        count = len(result.data) if result.data else 0
        inserted += count
        skipped += len(batch) - count

        end = min(i + BATCH_SIZE, total)
        print(f"  [{end}/{total}] batch upserted — {count} written")

    print(f"\nDone. {inserted} upserted, {skipped} unchanged out of {total} total.")


if __name__ == "__main__":
    main()
