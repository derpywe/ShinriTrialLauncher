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
    download_url = assets[0]["browser_download_url"] if assets else RELEASES_PAGE_URL

    return {
        "version": latest_tag,
        "url": data.get("html_url", RELEASES_PAGE_URL),
        "download_url": download_url,
        "notes": data.get("body", ""),
    }