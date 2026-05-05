# -*- coding: utf-8 -*-
import hashlib
import uuid
from pathlib import Path


def build_document_fingerprint(file_path: str, source_file: str = "") -> str:
    hasher = hashlib.sha256()
    with open(file_path, "rb") as file_obj:
        for chunk in iter(lambda: file_obj.read(1024 * 1024), b""):
            hasher.update(chunk)

    if source_file:
        hasher.update(Path(source_file).name.encode("utf-8"))

    return hasher.hexdigest()


def build_ingestion_document_id(
    collection_name: str,
    source_file: str,
    local_key: str,
    *,
    model: str = "",
    namespace: str = "",
    document_fingerprint: str = "",
) -> str:
    source_name = Path(source_file).name
    seed = "|".join(
        [
            str(collection_name or ""),
            source_name,
            str(document_fingerprint or ""),
            str(model or ""),
            str(namespace or ""),
            str(local_key or ""),
        ]
    )
    return str(uuid.uuid5(uuid.NAMESPACE_URL, seed))
