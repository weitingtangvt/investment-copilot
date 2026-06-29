from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import quote
from urllib import error, request

from .storage import Storage

logger = logging.getLogger(__name__)

IMA_BASE_URL = "https://ima.qq.com/openapi/wiki/v1"

MEDIA_TYPE_BY_EXT = {
    "pdf": (1, "application/pdf"),
    "doc": (3, "application/msword"),
    "docx": (3, "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    "ppt": (4, "application/vnd.ms-powerpoint"),
    "pptx": (4, "application/vnd.openxmlformats-officedocument.presentationml.presentation"),
    "xls": (5, "application/vnd.ms-excel"),
    "xlsx": (5, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    "csv": (5, "text/csv"),
    "md": (7, "text/markdown"),
    "markdown": (7, "text/markdown"),
    "png": (9, "image/png"),
    "jpg": (9, "image/jpeg"),
    "jpeg": (9, "image/jpeg"),
    "webp": (9, "image/webp"),
    "txt": (13, "text/plain"),
    "xmind": (14, "application/x-xmind"),
    "mp3": (15, "audio/mpeg"),
    "m4a": (15, "audio/x-m4a"),
    "wav": (15, "audio/wav"),
    "aac": (15, "audio/aac"),
}

SIZE_LIMIT_BY_MEDIA_TYPE = {
    5: 10 * 1024 * 1024,
    7: 10 * 1024 * 1024,
    13: 10 * 1024 * 1024,
    14: 10 * 1024 * 1024,
    9: 30 * 1024 * 1024,
}
DEFAULT_FILE_SIZE_LIMIT = 200 * 1024 * 1024

SUCCESS_STATUSES = {"synced", "already_synced", "already_exists"}


class IMASyncError(RuntimeError):
    pass


def _json_post(url: str, payload: Dict[str, Any], headers: Dict[str, str], timeout: int = 30) -> Dict[str, Any]:
    body = json.dumps(payload or {}, ensure_ascii=False).encode("utf-8")
    req = request.Request(url, data=body, method="POST")
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        raise IMASyncError(detail or f"IMA TextFailed ({exc.code})") from exc
    except error.URLError as exc:
        raise IMASyncError(f"IMA TextFailed: {exc.reason}") from exc

    try:
        parsed = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        raise IMASyncError("IMA Text") from exc

    if isinstance(parsed, dict) and "retcode" in parsed:
        retcode = int(parsed.get("retcode") or 0)
        if retcode != 0:
            raise IMASyncError(str(parsed.get("errmsg") or f"IMA TextErrorText {retcode}"))
        data = parsed.get("data")
        return data if isinstance(data, dict) else {}

    if isinstance(parsed, dict) and "code" in parsed:
        code = int(parsed.get("code") or 0)
        if code != 0:
            raise IMASyncError(str(parsed.get("msg") or parsed.get("errmsg") or f"IMA TextErrorText {code}"))
        data = parsed.get("data")
        return data if isinstance(data, dict) else {}

    if isinstance(parsed, dict):
        return parsed

    raise IMASyncError("IMA Text")


def _sha1_hex(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()


def _hmac_sha1_hex(key: str, value: str) -> str:
    return hmac.new(key.encode("utf-8"), value.encode("utf-8"), hashlib.sha1).hexdigest()


def _build_cos_authorization(
    *,
    secret_id: str,
    secret_key: str,
    method: str,
    pathname: str,
    headers_to_sign: Dict[str, str],
    start_time: int,
    expired_time: int,
) -> str:
    key_time = f"{start_time};{expired_time}"
    sign_key = _hmac_sha1_hex(secret_key, key_time)
    signed_keys = sorted((headers_to_sign or {}).keys())
    http_headers = "&".join(f"{key.lower()}={quote(str(headers_to_sign[key]), safe='')}" for key in signed_keys)
    http_string = f"{method.lower()}\n{pathname}\n\n{http_headers}\n"
    string_to_sign = f"sha1\n{key_time}\n{_sha1_hex(http_string)}\n"
    signature = _hmac_sha1_hex(sign_key, string_to_sign)
    header_list = ";".join(key.lower() for key in signed_keys)
    return "&".join(
        [
            "q-sign-algorithm=sha1",
            f"q-ak={secret_id}",
            f"q-sign-time={key_time}",
            f"q-key-time={key_time}",
            f"q-header-list={header_list}",
            "q-url-param-list=",
            f"q-signature={signature}",
        ]
    )


def inspect_local_file(file_path: Path) -> Dict[str, Any]:
    path = Path(file_path)
    if not path.exists():
        raise IMASyncError(f"Text: {path}")

    ext = path.suffix.lstrip(".").lower()
    if ext not in MEDIA_TYPE_BY_EXT:
        raise IMASyncError(f"TextUploadText: .{ext or 'unknown'}")

    media_type, content_type = MEDIA_TYPE_BY_EXT[ext]
    file_size = path.stat().st_size
    size_limit = SIZE_LIMIT_BY_MEDIA_TYPE.get(media_type, DEFAULT_FILE_SIZE_LIMIT)
    if file_size > size_limit:
        raise IMASyncError(f"Text IMA Text: {path.name} ({file_size} bytes)")

    return {
        "file_name": path.name,
        "file_ext": ext,
        "file_size": file_size,
        "content_type": content_type,
        "media_type": media_type,
        "last_modify_time": int(path.stat().st_mtime),
    }


class IMAKnowledgeBaseClient:
    def __init__(self, client_id: str, api_key: str, *, timeout: int = 30):
        self.client_id = str(client_id or "").strip()
        self.api_key = str(api_key or "").strip()
        self.timeout = max(5, int(timeout or 30))

    @property
    def headers(self) -> Dict[str, str]:
        return {
            "ima-openapi-clientid": self.client_id,
            "ima-openapi-apikey": self.api_key,
            "Content-Type": "application/json; charset=utf-8",
        }

    def _post(self, endpoint: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        url = f"{IMA_BASE_URL}{endpoint}"
        return _json_post(url, payload, self.headers, timeout=self.timeout)

    def get_addable_knowledge_base_list(self, limit: int = 50) -> Dict[str, Any]:
        cursor = ""
        items = []
        for _ in range(20):
            data = self._post(
                "/get_addable_knowledge_base_list",
                {"cursor": cursor, "limit": max(1, min(int(limit or 50), 50))},
            )
            items.extend(list(data.get("addable_knowledge_base_list") or []))
            if data.get("is_end"):
                break
            next_cursor = str(data.get("next_cursor") or "")
            if not next_cursor or next_cursor == cursor:
                break
            cursor = next_cursor
        return {"items": items}

    def detect_target_knowledge_base(self) -> Dict[str, str]:
        items = self.get_addable_knowledge_base_list().get("items") or []
        if not items:
            raise IMASyncError("Text IMA Text")

        preferred = next((item for item in items if str(item.get("name") or "").strip() == "Text"), None)
        target = preferred or items[0]
        return {
            "id": str(target.get("id") or "").strip(),
            "name": str(target.get("name") or "").strip(),
        }

    def check_repeated_name(
        self,
        *,
        knowledge_base_id: str,
        file_name: str,
        media_type: int,
        folder_id: Optional[str] = None,
    ) -> bool:
        payload: Dict[str, Any] = {
            "knowledge_base_id": knowledge_base_id,
            "params": [{"name": file_name, "media_type": int(media_type)}],
        }
        if folder_id:
            payload["folder_id"] = folder_id
        data = self._post("/check_repeated_names", payload)
        for item in data.get("results") or []:
            if str(item.get("name") or "") == file_name:
                return bool(item.get("is_repeated"))
        return False

    def create_media(
        self,
        *,
        knowledge_base_id: str,
        file_name: str,
        file_size: int,
        content_type: str,
        file_ext: str,
    ) -> Dict[str, Any]:
        return self._post(
            "/create_media",
            {
                "file_name": file_name,
                "file_size": int(file_size),
                "content_type": content_type,
                "knowledge_base_id": knowledge_base_id,
                "file_ext": file_ext,
            },
        )

    def add_knowledge(
        self,
        *,
        knowledge_base_id: str,
        media_type: int,
        media_id: str,
        title: str,
        file_info: Dict[str, Any],
        folder_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "media_type": int(media_type),
            "media_id": media_id,
            "title": title,
            "knowledge_base_id": knowledge_base_id,
            "file_info": file_info,
        }
        if folder_id:
            payload["folder_id"] = folder_id
        return self._post("/add_knowledge", payload)

    def upload_file_to_cos(self, file_path: Path, credential: Dict[str, Any], content_type: str) -> None:
        path = Path(file_path)
        file_bytes = path.read_bytes()

        bucket = str(credential.get("bucket_name") or "").strip()
        region = str(credential.get("region") or "").strip()
        secret_id = str(credential.get("secret_id") or "").strip()
        secret_key = str(credential.get("secret_key") or "").strip()
        token = str(credential.get("token") or "").strip()
        cos_key = str(credential.get("cos_key") or "").lstrip("/")

        if not all([bucket, region, secret_id, secret_key, token, cos_key]):
            raise IMASyncError("COS UploadText")

        hostname = f"{bucket}.cos.{region}.myqcloud.com"
        pathname = f"/{cos_key}"
        start_time = int(credential.get("start_time") or int(time.time()))
        expired_time = int(credential.get("expired_time") or (start_time + 3600))
        signed_headers = {
            "content-length": str(len(file_bytes)),
            "host": hostname,
        }
        authorization = _build_cos_authorization(
            secret_id=secret_id,
            secret_key=secret_key,
            method="PUT",
            pathname=pathname,
            headers_to_sign=signed_headers,
            start_time=start_time,
            expired_time=expired_time,
        )

        req = request.Request(
            f"https://{hostname}{pathname}",
            data=file_bytes,
            method="PUT",
            headers={
                "Content-Type": content_type,
                "Content-Length": str(len(file_bytes)),
                "Authorization": authorization,
                "x-cos-security-token": token,
            },
        )
        try:
            with request.urlopen(req, timeout=self.timeout) as resp:
                if resp.status < 200 or resp.status >= 300:
                    raise IMASyncError(f"COS UploadFailed ({resp.status})")
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")
            raise IMASyncError(detail or f"COS UploadFailed ({exc.code})") from exc
        except error.URLError as exc:
            raise IMASyncError(f"COS UploadFailed: {exc.reason}") from exc


def _result_payload(
    *,
    success: bool,
    status: str,
    snapshot_type: str,
    sync_key: str,
    local_file: Path,
    title: str,
    knowledge_base_id: str = "",
    knowledge_base_name: str = "",
    media_id: str = "",
    synced_at: str = "",
    error: str = "",
    attempted: bool = False,
    retry_available: bool = False,
) -> Dict[str, Any]:
    return {
        "success": bool(success),
        "status": str(status or "").strip(),
        "snapshot_type": snapshot_type,
        "sync_key": sync_key,
        "local_file": str(local_file),
        "title": title,
        "knowledge_base_id": knowledge_base_id,
        "knowledge_base_name": knowledge_base_name,
        "media_id": media_id,
        "synced_at": synced_at,
        "error": error,
        "attempted": bool(attempted),
        "retry_available": bool(retry_available),
    }


def sync_snapshot_to_ima(
    storage: Storage,
    *,
    snapshot_type: str,
    sync_key: str,
    local_file: Path,
    title: str,
    force: bool = False,
) -> Dict[str, Any]:
    path = Path(local_file)
    existing = storage.get_ima_sync_record(sync_key)
    if existing and not force:
        existing_status = str(existing.get("status") or "").strip()
        if existing_status in {"synced", "already_exists"}:
            return _result_payload(
                success=True,
                status="already_synced",
                snapshot_type=snapshot_type,
                sync_key=sync_key,
                local_file=path,
                title=str(existing.get("title") or title),
                knowledge_base_id=str(existing.get("knowledge_base_id") or ""),
                knowledge_base_name=str(existing.get("knowledge_base_name") or ""),
                media_id=str(existing.get("media_id") or ""),
                synced_at=str(existing.get("synced_at") or ""),
                attempted=False,
                retry_available=False,
            )
        if existing_status == "failed":
            return _result_payload(
                success=False,
                status="failed",
                snapshot_type=snapshot_type,
                sync_key=sync_key,
                local_file=path,
                title=str(existing.get("title") or title),
                knowledge_base_id=str(existing.get("knowledge_base_id") or ""),
                knowledge_base_name=str(existing.get("knowledge_base_name") or ""),
                media_id=str(existing.get("media_id") or ""),
                synced_at=str(existing.get("synced_at") or ""),
                error=str(existing.get("error") or ""),
                attempted=False,
                retry_available=True,
            )

    config = storage.get_ima_config()
    client_id = str(config.get("client_id") or "").strip()
    api_key = str(config.get("api_key") or "").strip()
    if not client_id or not api_key:
        result = _result_payload(
            success=False,
            status="not_configured",
            snapshot_type=snapshot_type,
            sync_key=sync_key,
            local_file=path,
            title=title,
            attempted=False,
            retry_available=True,
            error="IMA Text Client ID / API Key",
        )
        storage.save_ima_sync_record(sync_key, result)
        return result

    try:
        meta = inspect_local_file(path)
        client = IMAKnowledgeBaseClient(client_id, api_key)

        knowledge_base_id = str(config.get("knowledge_base_id") or "").strip()
        knowledge_base_name = str(config.get("knowledge_base_name") or "").strip()
        if not knowledge_base_id:
            target = client.detect_target_knowledge_base()
            knowledge_base_id = target["id"]
            knowledge_base_name = target["name"]
            storage.save_ima_config(
                knowledge_base_id=knowledge_base_id,
                knowledge_base_name=knowledge_base_name,
            )

        if client.check_repeated_name(
            knowledge_base_id=knowledge_base_id,
            file_name=meta["file_name"],
            media_type=meta["media_type"],
        ):
            result = _result_payload(
                success=True,
                status="already_exists",
                snapshot_type=snapshot_type,
                sync_key=sync_key,
                local_file=path,
                title=title,
                knowledge_base_id=knowledge_base_id,
                knowledge_base_name=knowledge_base_name,
                synced_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
                attempted=True,
            )
            storage.save_ima_sync_record(sync_key, result)
            return result

        created = client.create_media(
            knowledge_base_id=knowledge_base_id,
            file_name=meta["file_name"],
            file_size=meta["file_size"],
            content_type=meta["content_type"],
            file_ext=meta["file_ext"],
        )
        media_id = str(created.get("media_id") or "").strip()
        credential = created.get("cos_credential") or {}
        client.upload_file_to_cos(path, credential, meta["content_type"])
        add_result = client.add_knowledge(
            knowledge_base_id=knowledge_base_id,
            media_type=meta["media_type"],
            media_id=media_id,
            title=title,
            file_info={
                "cos_key": str(credential.get("cos_key") or ""),
                "file_size": int(meta["file_size"]),
                "last_modify_time": int(meta["last_modify_time"]),
                "password": "",
                "file_name": meta["file_name"],
            },
        )
        result = _result_payload(
            success=True,
            status="synced",
            snapshot_type=snapshot_type,
            sync_key=sync_key,
            local_file=path,
            title=title,
            knowledge_base_id=knowledge_base_id,
            knowledge_base_name=knowledge_base_name,
            media_id=str(add_result.get("media_id") or media_id),
            synced_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
            attempted=True,
        )
        storage.save_ima_sync_record(sync_key, result)
        return result
    except Exception as exc:
        logger.exception("IMA sync failed for %s", sync_key)
        error_text = str(exc)
        knowledge_base_id = str(config.get("knowledge_base_id") or "").strip()
        knowledge_base_name = str(config.get("knowledge_base_name") or "").strip()
        result = _result_payload(
            success=False,
            status="failed",
            snapshot_type=snapshot_type,
            sync_key=sync_key,
            local_file=path,
            title=title,
            knowledge_base_id=knowledge_base_id,
            knowledge_base_name=knowledge_base_name,
            error=error_text,
            attempted=True,
            retry_available=True,
        )
        storage.save_ima_sync_record(sync_key, result)
        return result
