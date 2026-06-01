"""
R2 yükleme örneği — anahtarları buraya yazmayin; agent-base-api/.env kullanin.

Public URL (API ile ayni mantik):
  - R2_PUBLIC_BASE_URL=https://senin-domain.com   (özel domain / cloudflared)
  - veya R2_PUBLIC_R2_DEV_HOST=pub-xxxxx.r2.dev  (R2 konsol Public Development URL hostu)
"""

import os
import uuid

import boto3
from dotenv import load_dotenv

load_dotenv()

account_id = os.environ["R2_ACCOUNT_ID"]
access_key = os.environ["R2_ACCESS_KEY_ID"]
secret_key = os.environ["R2_SECRET_ACCESS_KEY"]
bucket = os.environ["R2_BUCKET_NAME"]

s3 = boto3.client(
    "s3",
    endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
    aws_access_key_id=access_key,
    aws_secret_access_key=secret_key,
    region_name="auto",
)

key = f"uploads/{uuid.uuid4()}.txt"
body = b"hello from test3\n"

s3.put_object(Bucket=bucket, Key=key, Body=body, ContentType="text/plain")

base = (os.getenv("R2_PUBLIC_BASE_URL") or "").strip().rstrip("/")
if not base:
    host = (os.getenv("R2_PUBLIC_R2_DEV_HOST") or "").strip().rstrip("/")
    if host:
        base = host if host.startswith("http") else f"https://{host}"
if not base:
    raise SystemExit("R2_PUBLIC_BASE_URL veya R2_PUBLIC_R2_DEV_HOST ayarlayin.")

url = f"{base}/{key}"
print("Public URL:", url)
