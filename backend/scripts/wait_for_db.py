"""
Cross-platform PostgreSQL health check.
Pengganti bash 'until pg_isready' yang tidak jalan di Windows.
"""
import subprocess
import sys
import time

MAX_RETRIES = 30
SLEEP_SECONDS = 1

for attempt in range(1, MAX_RETRIES + 1):
    result = subprocess.run(
        [
            "docker", "compose", "exec", "-T", "postgres",
            "pg_isready", "-U", "prometheus", "-d", "prometheus",
        ],
        capture_output=True,
    )
    if result.returncode == 0:
        sys.exit(0)

    print(f"  Attempt {attempt}/{MAX_RETRIES} — waiting...", flush=True)
    time.sleep(SLEEP_SECONDS)

print("ERROR: PostgreSQL did not become ready in time.", file=sys.stderr)
sys.exit(1)