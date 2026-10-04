#!/usr/bin/env python3
import random
import logging
import requests

logger = logging.getLogger(__name__)


class ProxyRotator:
    def __init__(self, proxy_list_str: str, user: str, password: str):
        raw = [p.strip() for p in proxy_list_str.split(",") if p.strip()]
        self.proxies = []
        for proxy in raw:
            self.proxies.append(f"http://{user}:{password}@{proxy}")
        self._index = 0
        logger.info(f"ProxyRotator loaded with {len(self.proxies)} proxies")

    def get_proxy(self):
        if not self.proxies:
            return None
        proxy = random.choice(self.proxies)
        return {"http": proxy, "https": proxy}

    def get_session(self):
        session = requests.Session()
        proxy = self.get_proxy()
        if proxy:
            session.proxies.update(proxy)
            logger.info(f"Using proxy: {proxy['http'].split('@')[1]}")
        return session

    def test_proxies(self):
        logger.info("Testing all proxies...")
        for proxy in self.proxies:
            try:
                proxies = {"http": proxy, "https": proxy}
                resp = requests.get(
                    "https://httpbin.org/ip",
                    proxies=proxies,
                    timeout=8
                )
                ip = resp.json().get("origin", "unknown")
                display = proxy.split("@")[1]
                logger.info(f"  OK {display} -> IP: {ip}")
            except Exception as e:
                display = proxy.split("@")[1]
                logger.warning(f"  FAIL {display}: {e}")
