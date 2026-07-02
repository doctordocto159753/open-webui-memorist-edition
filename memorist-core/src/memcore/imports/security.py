import os
import zipfile
from pathlib import PurePosixPath


class ImportSecurityError(ValueError):
    """Raised when an import archive violates staging security policy."""


MAX_COMPRESSED_BYTES = 50 * 1024 * 1024
MAX_EXPANDED_BYTES = 250 * 1024 * 1024
MAX_COMPRESSION_RATIO = 100
MAX_FILE_COUNT = 10_000
MAX_NESTED_ARCHIVE_DEPTH = 1


def validate_zip_archive(path: str) -> list[zipfile.ZipInfo]:
    size = os.path.getsize(path)
    if size > MAX_COMPRESSED_BYTES:
        raise ImportSecurityError("archive compressed size exceeds limit")
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        if len(infos) > MAX_FILE_COUNT:
            raise ImportSecurityError("archive contains too many files")
        total_expanded = 0
        nested_archives = 0
        for info in infos:
            _validate_member(info)
            total_expanded += info.file_size
            if info.filename.lower().endswith((".zip", ".tar", ".gz", ".rar", ".7z")):
                nested_archives += 1
        if total_expanded > MAX_EXPANDED_BYTES:
            raise ImportSecurityError("archive expanded size exceeds limit")
        if total_expanded and size and total_expanded / max(size, 1) > MAX_COMPRESSION_RATIO:
            raise ImportSecurityError("archive compression ratio exceeds limit")
        if nested_archives > MAX_NESTED_ARCHIVE_DEPTH:
            raise ImportSecurityError("nested archive depth exceeds limit")
        return infos


def _validate_member(info: zipfile.ZipInfo) -> None:
    path = PurePosixPath(info.filename.replace("\\", "/"))
    if path.is_absolute():
        raise ImportSecurityError("archive contains an absolute path")
    if ".." in path.parts:
        raise ImportSecurityError("archive contains path traversal")
    mode = (info.external_attr >> 16) & 0o170000
    if mode in {0o120000, 0o10000, 0o20000, 0o60000}:
        raise ImportSecurityError("archive contains unsupported link or device entry")
