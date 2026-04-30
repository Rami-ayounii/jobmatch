"""Cloudflare R2 / S3-compatible storage client."""
import logging

import boto3

from app.core.config import settings

logger = logging.getLogger(__name__)


def _s3_client() -> "boto3.client":  # type: ignore[name-defined]
    return boto3.client(
        "s3",
        endpoint_url=f"https://{settings.r2_account_id}.r2.cloudflarestorage.com",
        aws_access_key_id=settings.r2_access_key_id,
        aws_secret_access_key=settings.r2_secret_access_key,
        region_name="auto",
    )


async def upload_resume(tenant_id: str, candidate_id: str, data: bytes, filename: str) -> str:
    """Upload raw resume bytes to R2; returns the object key."""
    import asyncio

    key = f"{tenant_id}/{candidate_id}/{filename}"

    def _upload() -> None:
        _s3_client().put_object(
            Bucket=settings.r2_bucket_resumes,
            Key=key,
            Body=data,
        )

    await asyncio.to_thread(_upload)
    logger.info("resume_uploaded key=%s", key)
    return key


async def download_resume(key: str) -> bytes:
    import asyncio

    def _download() -> bytes:
        obj = _s3_client().get_object(Bucket=settings.r2_bucket_resumes, Key=key)
        return obj["Body"].read()

    return await asyncio.to_thread(_download)


async def delete_resume(key: str) -> None:
    import asyncio

    def _delete() -> None:
        _s3_client().delete_object(Bucket=settings.r2_bucket_resumes, Key=key)

    await asyncio.to_thread(_delete)
    logger.info("resume_deleted key=%s", key)
