"""Credential store: values encrypted with Windows DPAPI (per Windows user account).

Lookup order for a credential named e.g. "mouser_api_key":
  1. environment variable ALM_MOUSER_API_KEY
  2. %APPDATA%\\AltiumLibraryManager\\credentials.json (DPAPI-encrypted, base64)
Secrets are never logged.
"""

from __future__ import annotations

import base64
import ctypes
import json
import os
import sys
from pathlib import Path

from .config import app_dir, atomic_write_text

_DESCRIPTION = "AltiumLibraryManager"
_CRYPTPROTECT_UI_FORBIDDEN = 0x01

if sys.platform == "win32":
    from ctypes import wintypes

    class _Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    _crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(_Blob), wintypes.LPCWSTR, ctypes.POINTER(_Blob),
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob),
    ]
    _crypt32.CryptProtectData.restype = wintypes.BOOL
    _crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.POINTER(_Blob),
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob),
    ]
    _crypt32.CryptUnprotectData.restype = wintypes.BOOL
    _kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    _kernel32.LocalFree.restype = ctypes.c_void_p

    def _call(func, data: bytes, *mid_args) -> bytes:
        buf = ctypes.create_string_buffer(data, len(data))
        blob_in = _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
        blob_out = _Blob()
        if not func(ctypes.byref(blob_in), *mid_args, None, None, None,
                    _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(blob_out)):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            return ctypes.string_at(blob_out.pbData, blob_out.cbData)
        finally:
            _kernel32.LocalFree(ctypes.cast(blob_out.pbData, ctypes.c_void_p))

    def protect(data: bytes) -> bytes:
        return _call(_crypt32.CryptProtectData, data, _DESCRIPTION)

    def unprotect(data: bytes) -> bytes:
        return _call(_crypt32.CryptUnprotectData, data, None)

else:  # pragma: no cover - the application targets Windows
    def protect(data: bytes) -> bytes:
        raise RuntimeError("Encrypted credential storage requires Windows (DPAPI).")

    unprotect = protect


class CredentialStore:
    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else app_dir() / "credentials.json"

    def _read(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    @staticmethod
    def env_name(name: str) -> str:
        return "ALM_" + name.upper()

    def get(self, name: str) -> str:
        env = os.environ.get(self.env_name(name), "").strip()
        if env:
            return env
        stored = self._read().get(name)
        if not stored:
            return ""
        try:
            return unprotect(base64.b64decode(stored)).decode("utf-8")
        except Exception:  # corrupted, or encrypted by another Windows user
            return ""

    def set(self, name: str, value: str) -> None:
        data = self._read()
        value = (value or "").strip()
        if value:
            data[name] = base64.b64encode(protect(value.encode("utf-8"))).decode("ascii")
        else:
            data.pop(name, None)
        atomic_write_text(self.path, json.dumps(data, indent=2))
