"""Minimal AWS Signature Version 4 signer (stdlib only)."""

from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime
from urllib.parse import quote, urlparse


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _hmac_sha256(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def _credential_scope(datestamp: str, region: str, service: str) -> str:
    return f"{datestamp}/{region}/{service}/aws4_request"


def _signing_key(secret_key: str, datestamp: str, region: str, service: str) -> bytes:
    k_date = _hmac_sha256(("AWS4" + secret_key).encode("utf-8"), datestamp)
    k_region = hmac.new(k_date, region.encode("utf-8"), hashlib.sha256).digest()
    k_service = hmac.new(k_region, service.encode("utf-8"), hashlib.sha256).digest()
    return hmac.new(k_service, b"aws4_request", hashlib.sha256).digest()


def sign_headers(
    *,
    method: str,
    url: str,
    body: bytes,
    region: str,
    service: str,
    access_key: str,
    secret_key: str,
    session_token: str | None = None,
    extra_headers: dict[str, str] | None = None,
    now: datetime | None = None,
) -> dict[str, str]:
    """Return request headers including Authorization (SigV4)."""
    parsed = urlparse(url)
    host = parsed.netloc
    # SigV4 for non-S3 services requires the canonical URI to be URI-encoded a
    # second time (botocore does this): the request path already contains a
    # percent-encoded model id like ".../model/amazon.nova-lite-v1%3A0/converse",
    # so we re-encode, turning "%3A" into "%253A". Signing the single-encoded
    # path yields a signature AWS rejects with SignatureDoesNotMatch.
    canonical_uri = quote(parsed.path or "/", safe="/")
    # Query already encoded if present
    canonical_query = parsed.query or ""

    now = now or datetime.now(UTC)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    datestamp = now.strftime("%Y%m%d")
    payload_hash = _sha256_hex(body)

    headers: dict[str, str] = {
        "host": host,
        "x-amz-date": amz_date,
        "x-amz-content-sha256": payload_hash,
        "content-type": "application/json",
    }
    if session_token:
        headers["x-amz-security-token"] = session_token
    if extra_headers:
        for k, v in extra_headers.items():
            headers[k.lower()] = v

    signed_header_keys = sorted(headers.keys())
    canonical_headers = "".join(f"{k}:{headers[k].strip()}\n" for k in signed_header_keys)
    signed_headers = ";".join(signed_header_keys)

    canonical_request = "\n".join(
        [
            method.upper(),
            canonical_uri,
            canonical_query,
            canonical_headers,
            signed_headers,
            payload_hash,
        ]
    )
    scope = _credential_scope(datestamp, region, service)
    string_to_sign = "\n".join(
        [
            "AWS4-HMAC-SHA256",
            amz_date,
            scope,
            _sha256_hex(canonical_request.encode("utf-8")),
        ]
    )
    signature = hmac.new(
        _signing_key(secret_key, datestamp, region, service),
        string_to_sign.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    authorization = (
        f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )

    out = {k: headers[k] for k in signed_header_keys if k != "host"}
    out["Host"] = host
    out["Authorization"] = authorization
    # Preserve Content-Type casing for urllib
    if "content-type" in headers:
        out["Content-Type"] = headers["content-type"]
    return out


def quote_path_segment(value: str) -> str:
    """URL-encode a single path segment (Bedrock model ids contain ':')."""
    return quote(value, safe="")
