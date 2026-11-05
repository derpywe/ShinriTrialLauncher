import os
import subprocess
import sys
import tempfile
import uuid

import requests

GITHUB_REPO = "derpywe/ShinriTrialLauncher"
RELEASES_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
RELEASES_PAGE_URL = f"https://github.com/{GITHUB_REPO}/releases/latest"


def _parse_version(version_str: str):
    cleaned = version_str.strip().lstrip("vV")
    parts = []
    for part in cleaned.split("."):
        digits = ""
        for ch in part:
            if ch.isdigit():
                digits += ch
            else:
                break
        parts.append(int(digits) if digits else 0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


def check_for_update(current_version: str, timeout: float = 5.0):
    resp = requests.get(RELEASES_API_URL, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()

    latest_tag = data.get("tag_name", "")
    if not latest_tag:
        return None

    if _parse_version(latest_tag) <= _parse_version(current_version):
        return None

    assets = data.get("assets", [])
    exe_url = ""
    exe_sha256 = ""
    for asset in assets:
        if asset.get("name", "").endswith(".exe"):
            exe_url = asset["browser_download_url"]
            digest = asset.get("digest", "")
            if isinstance(digest, str) and digest.startswith("sha256:"):
                exe_sha256 = digest[7:]
            break

    return {
        "version": latest_tag,
        "url": data.get("html_url", RELEASES_PAGE_URL),
        "download_url": exe_url,
        "download_sha256": exe_sha256,
        "notes": data.get("body", ""),
    }


def get_update_download_path() -> str:
    """Куда скачиваем новый exe перед установкой."""
    from launcher_config import BASE_DIR

    upd_dir = BASE_DIR / "update"
    return str(upd_dir / "ShinriTrialLauncher_new.exe")


def download_update(url: str, dest_path: str, progress_cb=None, timeout: float = 120.0, expected_sha256: str = ""):
    """Скачивает обновление во временный файл, проверяет и кладёт в dest_path.

    progress_cb(done_bytes, total_bytes) вызывается по мере загрузки.
    При любом подозрении на повреждение (обрыв загрузки, неверная сумма)
    файл удаляется и поднимается исключение.
    """
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    resp = requests.get(url, stream=True, timeout=timeout, allow_redirects=True)
    resp.raise_for_status()
    total = int(resp.headers.get("Content-Length", 0) or 0)
    done = 0
    tmp = dest_path + ".part"
    with open(tmp, "wb") as f:
        for chunk in resp.iter_content(chunk_size=131072):
            if not chunk:
                continue
            f.write(chunk)
            done += len(chunk)
            if progress_cb:
                progress_cb(done, total)

    size = os.path.getsize(tmp)
    if size == 0:
        os.remove(tmp)
        raise RuntimeError("Скачанный файл пуст")
    if total > 0 and done < total:
        os.remove(tmp)
        raise RuntimeError(f"Загрузка оборвалась: получено {done} из {total} байт")
    if size < 2 * 1024 * 1024:
        os.remove(tmp)
        raise RuntimeError("Скачанный файл подозрительно мал (повреждён)")
    if not _is_valid_pe(tmp):
        os.remove(tmp)
        raise RuntimeError("Скачанный файл не является исполняемым (.exe)")
    if expected_sha256:
        actual = _sha256_file(tmp)
        if actual.lower() != expected_sha256.lower():
            os.remove(tmp)
            raise RuntimeError("Контрольная сумма файла не совпала (повреждён)")
    os.replace(tmp, dest_path)
    return dest_path


def _is_valid_pe(path: str) -> bool:
    try:
        with open(path, "rb") as f:
            if f.read(2) != b"MZ":
                return False
            f.seek(0x3C)
            raw = f.read(4)
            if len(raw) < 4:
                return False
            pe_off = int.from_bytes(raw, "little")
            f.seek(pe_off)
            return f.read(4) == b"PE\0\0"
    except Exception:
        return False


def _sha256_file(path: str) -> str:
    import hashlib

    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def install_update(new_path: str) -> bool:
    """Подменяет запущенный exe новым и перезапускает его.

    Работает только в собранном (frozen) виде. Запускает PowerShell-хелпер,
    который дожидается выхода текущего процесса, делает резервную копию,
    заменяет exe и запускает новую версию. Возвращает True, если хелпер запущен.
    """
    if not getattr(sys, "frozen", False):
        return False

    old_path = sys.executable
    bak_path = old_path + ".bak"
    pid = os.getpid()

    script = (
        "$ErrorActionPreference = 'SilentlyContinue'\n"
        f"$pidToWait = {pid}\n"
        f"$new = {_ps_quote(new_path)}\n"
        f"$old = {_ps_quote(old_path)}\n"
        f"$bak = {_ps_quote(bak_path)}\n"
        "\n"
        "# wait for the launcher to exit\n"
        "while (Get-Process -Id $pidToWait -ErrorAction SilentlyContinue) { Start-Sleep -Milliseconds 400 }\n"
        "\n"
        "# backup current exe\n"
        "Copy-Item $old $bak -Force -ErrorAction SilentlyContinue\n"
        "# replace with the new exe\n"
        "Move-Item $new $old -Force\n"
        "if (-not (Test-Path $old)) {\n"
        "    Move-Item $bak $old -Force -ErrorAction SilentlyContinue\n"
        "    exit 1\n"
        "}\n"
        "# launch the new version\n"
        "Start-Process $old -WorkingDirectory (Split-Path $old)\n"
        "# remove this helper script\n"
        "Remove-Item $MyInvocation.MyCommand.Path -Force -ErrorAction SilentlyContinue\n"
    )

    script_path = os.path.join(
        tempfile.gettempdir(), f"shinri_update_{uuid.uuid4().hex}.ps1"
    )
    with open(script_path, "w", encoding="utf-8-sig") as f:
        f.write(script)

    creationflags = 0
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.Popen(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script_path],
        creationflags=creationflags,
        close_fds=True,
    )
    return True