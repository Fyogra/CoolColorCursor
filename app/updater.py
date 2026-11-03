"""Обновление программы: новая версия и описания изменений берутся из релизов на GitHub.

exe скачивается рядом со старым, сверяется с CCC.exe.sha256 из того же релиза и встаёт на место старого
(работающий exe Windows разрешает переименовать, но не удалить). Старый файл удаляется при следующем запуске.
"""
from __future__ import annotations

import hashlib
import html
import json
import os
import re
import subprocess
import sys
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple

from app.config import IS_WINDOWS, log
from app.version import REPO, VERSION

API = f"https://api.github.com/repos/{REPO}/releases"
FEED = f"https://github.com/{REPO}/releases.atom"     # запасной путь: у API лимит 60 запросов в час на IP (VPN)
DOWNLOAD = f"https://github.com/{REPO}/releases/download/{{tag}}/{{name}}"
UA = f"CCC/{VERSION}"
EXE_ASSET = "CCC.exe"
SHA_ASSET = "CCC.exe.sha256"
UPDATED_ARG = "--updated"             # новый exe ждёт, пока старый освободит место единственного экземпляра


@dataclass
class Release:
    tag: str
    version: Tuple[int, ...]
    notes: str
    page: str
    exe_url: str = ""
    sha_url: str = ""
    newer: List["Release"] = field(default_factory=list)   # все версии новее текущей, свежие первыми


def parse_version(text: str) -> Optional[Tuple[int, ...]]:
    """«v.2.0.10», «v2.0.10», «2.0.10» → (2, 0, 10)."""
    m = re.fullmatch(r"\s*v?\.?\s*(\d+(?:\.\d+)*)\s*", text or "")
    return tuple(int(x) for x in m.group(1).split(".")) if m else None


CURRENT = parse_version(VERSION)


def clean_notes(body: str) -> str:
    """Описание релиза без служебного хвоста про сборку и SHA-256."""
    lines = []
    for line in (body or "").replace("\r\n", "\n").split("\n"):
        if line.startswith("Собрано GitHub Actions"):
            break
        if line.startswith("SHA-256"):
            continue
        lines.append(line.rstrip())
    return "\n".join(lines).strip()


def _get(url: str, timeout: float = 10) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _releases() -> List[Release]:
    out = []
    for r in json.loads(_get(API + "?per_page=30")):
        version = parse_version(r.get("tag_name", ""))
        if not version or r.get("draft") or r.get("prerelease"):
            continue
        assets = {a.get("name"): a.get("browser_download_url", "") for a in r.get("assets") or []}
        out.append(Release(r["tag_name"], version, clean_notes(r.get("body", "")), r.get("html_url", ""),
                           assets.get(EXE_ASSET, ""), assets.get(SHA_ASSET, "")))
    out.sort(key=lambda x: x.version, reverse=True)
    return out


def _html_text(fragment: str) -> str:
    """HTML описания из ленты релизов → текст в том же виде, что и в описании («- » у пунктов списка)."""
    t = re.sub(r"\s*<li[^>]*>\s*", "\n- ", fragment)
    t = re.sub(r"<br\s*/?>|</p>|</ul>|</ol>", "\n", t)
    t = re.sub(r"<p[^>]*>", "\n", t)
    t = re.sub(r"<[^>]+>", "", t)
    t = html.unescape(t)
    t = "\n".join(line.strip() for line in t.split("\n"))
    return re.sub(r"\n{3,}", "\n\n", t).strip()


def _feed_releases() -> List[Release]:
    ns = {"a": "http://www.w3.org/2005/Atom"}
    out = []
    for entry in ET.fromstring(_get(FEED)).findall("a:entry", ns):
        tag = (entry.findtext("a:title", "", ns) or "").strip()
        version = parse_version(tag)
        if not version:
            continue
        link = entry.find("a:link", ns)
        out.append(Release(tag, version, clean_notes(_html_text(entry.findtext("a:content", "", ns) or "")),
                           link.get("href", "") if link is not None else "",
                           DOWNLOAD.format(tag=tag, name=EXE_ASSET), DOWNLOAD.format(tag=tag, name=SHA_ASSET)))
    out.sort(key=lambda x: x.version, reverse=True)
    return out


def _exists(url: str) -> bool:
    try:
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=10):
            return True
    except Exception:  # noqa: BLE001
        return False


def check() -> Tuple[Optional[Release], Optional[List[Release]]]:
    """(самая новая версия, если она новее текущей и у неё есть exe; все релизы) — или (None, None) без сети."""
    from_feed = False
    try:
        releases = _releases()
    except Exception as e:  # noqa: BLE001 — лимит API и т.п.: берём ленту релизов
        log.info("API релизов недоступно (%s), читаю ленту", e)
        try:
            releases, from_feed = _feed_releases(), True
        except Exception as e2:  # noqa: BLE001 — нет сети: проверим в следующий раз
            log.info("Проверка обновлений не удалась: %s", e2)
            return None, None
    newer = [r for r in releases if r.version > CURRENT]
    # В ленте не видно, приложен ли уже exe (его собирают несколько минут после публикации) — проверяем сами
    best = next((r for r in newer if r.exe_url and r.sha_url and (not from_feed or _exists(r.sha_url))), None)
    if best:
        best.newer = [r for r in newer if r.version <= best.version]
        log.info("Доступно обновление %s (сейчас %s)", best.tag, VERSION)
    return best, releases


def news(releases: List[Release], last_version: str, existed: bool) -> List[Release]:
    """Что поменялось с прошлого запуска: релизы новее last_version и не новее текущей версии.
    Если прошлая версия неизвестна (обновились с версии без этой функции) — только текущая; новичку — ничего."""
    last = parse_version(last_version)
    if last is None:
        return [r for r in releases if r.version == CURRENT] if existed else []
    return [r for r in releases if last < r.version <= CURRENT]


def can_self_update() -> bool:
    """Заменить себя может только собранный exe в папке, куда можно писать (запуск из исходников — нет)."""
    if not (IS_WINDOWS and getattr(sys, "frozen", False)):
        return False
    return os.access(os.path.dirname(sys.executable), os.W_OK)


def cleanup_old() -> bool:
    """Удаляет exe прошлой версии, оставшийся после обновления. False — он ещё занят (старый процесс закрывается)."""
    if not getattr(sys, "frozen", False):
        return True
    old = sys.executable + ".old"
    try:
        if os.path.exists(old):
            os.remove(old)
            log.info("Старая версия удалена: %s", old)
        return True
    except OSError:
        return False


def install(release: Release, progress: Callable[[int, int], None]) -> None:
    """Скачивает exe релиза, сверяет SHA-256 и ставит на место текущего. Ошибка — исключение с понятным текстом;
    при любой ошибке текущий exe остаётся как был."""
    exe = sys.executable
    new, old = exe + ".new", exe + ".old"
    expected = _get(release.sha_url, timeout=20).decode("utf-8", "replace").split()[0].lower()
    if not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise RuntimeError("в релизе нет правильной контрольной суммы")
    digest = hashlib.sha256()
    req = urllib.request.Request(release.exe_url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp, open(new, "wb") as f:
            total = int(resp.headers.get("Content-Length") or 0)
            done = 0
            while True:
                chunk = resp.read(256 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                digest.update(chunk)
                done += len(chunk)
                progress(done, total)
        if digest.hexdigest() != expected:
            raise RuntimeError("скачанный файл не совпал с контрольной суммой — попробуйте ещё раз")
        if os.path.exists(old):
            os.remove(old)
        os.replace(exe, old)
        try:
            os.replace(new, exe)
        except OSError:
            os.replace(old, exe)          # вернуть старый на место
            raise
    finally:
        if os.path.exists(new):
            try:
                os.remove(new)
            except OSError:
                pass
    log.info("Обновление %s установлено в %s", release.tag, exe)


def restart() -> None:
    """Запускает уже обновлённый exe; он подождёт, пока этот процесс закроется."""
    flags = 0x00000008 | 0x00000200 if IS_WINDOWS else 0  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    # Без сброса новый exe возьмёт временную папку PyInstaller этого процесса, а она удалится при выходе
    env = dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT="1")
    subprocess.Popen([sys.executable, UPDATED_ARG], creationflags=flags, close_fds=True, env=env)
