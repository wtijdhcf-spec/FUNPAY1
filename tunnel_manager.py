"""
Менеджер онлайн-туннеля (Cloudflare Tunnel)
Позволяет безопасно открывать Web Dashboard через интернет с телефона (4G/LTE/Wi-Fi)
без белого IP и без настройки роутера.
"""

import logging
import os
import re
import subprocess
import sys
import threading
from typing import Optional

import requests

logger = logging.getLogger("TunnelManager")

CLOUDFLARED_EXE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cloudflared.exe")
DOWNLOAD_URL = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe"


class CloudflareTunnel:
    def __init__(self, local_port: int = 5000):
        self.local_port = local_port
        self.process: Optional[subprocess.Popen] = None
        self.public_url: Optional[str] = None
        self._url_event = threading.Event()

    def ensure_cloudflared(self) -> bool:
        """Проверяет наличие cloudflared.exe, если нет — скачивает официальный бинарник"""
        if os.path.exists(CLOUDFLARED_EXE) and os.path.getsize(CLOUDFLARED_EXE) > 1000000:
            return True

        print("[*] Загрузка модуля онлайн-доступа Cloudflare Tunnel...")
        try:
            resp = requests.get(DOWNLOAD_URL, stream=True, timeout=30)
            if resp.status_code == 200:
                temp_file = CLOUDFLARED_EXE + ".tmp"
                with open(temp_file, "wb") as f:
                    for chunk in resp.iter_content(chunk_size=65536):
                        if chunk:
                            f.write(chunk)
                if os.path.exists(CLOUDFLARED_EXE):
                    try:
                        os.remove(CLOUDFLARED_EXE)
                    except Exception:
                        pass
                os.rename(temp_file, CLOUDFLARED_EXE)
                print("[+] Модуль онлайн-доступа успешно установлен.")
                return True
            else:
                logger.error(f"Не удалось скачать cloudflared: HTTP {resp.status_code}")
                return False
        except Exception as e:
            logger.error(f"Ошибка при скачивании cloudflared: {e}")
            return False

    def start(self, timeout: int = 25) -> Optional[str]:
        """
        Запускает защищенный туннель и ожидает получение публичного HTTPS URL
        """
        if not self.ensure_cloudflared():
            return None

        cmd = [
            CLOUDFLARED_EXE,
            "tunnel",
            "--url",
            f"http://127.0.0.1:{self.local_port}",
            "--no-autoupdate",
        ]

        try:
            # Запускаем cloudflared в фоне без лишних окон
            creation_flags = 0
            if sys.platform == "win32":
                creation_flags = subprocess.CREATE_NO_WINDOW

            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=creation_flags,
            )

            # Фоновый поток для чтения логов и извлечения URL
            t = threading.Thread(target=self._read_tunnel_output, daemon=True)
            t.start()

            # Ждем появления URL
            if self._url_event.wait(timeout=timeout):
                return self.public_url

            logger.warning("Время ожидания генерации ссылки онлайн-туннеля истекло.")
            return None

        except Exception as e:
            logger.error(f"Ошибка запуска cloudflared туннеля: {e}")
            return None

    def _read_tunnel_output(self):
        """Читает вывод cloudflared и извлекает trycloudflare.com URL"""
        if not self.process or not self.process.stderr:
            return

        url_pattern = re.compile(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com")

        for line in self.process.stderr:
            match = url_pattern.search(line)
            if match:
                self.public_url = match.group(0)
                self._url_event.set()
                break

    def stop(self):
        """Останавливает процесс туннеля"""
        if self.process:
            try:
                self.process.terminate()
                self.process.wait(timeout=3)
            except Exception:
                try:
                    self.process.kill()
                except Exception:
                    pass
            self.process = None
            self.public_url = None
        self._url_event.clear()
