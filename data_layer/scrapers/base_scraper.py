"""
    PropIQ - Base Scraper
    All scrapers inherit from this. Handles retries, rate-limiting, logging,
    user-agent rotation, and proxy support.

    @author Minh Thang Nguyen
    @version June 20, 2026
"""

import time
import random
import logging
import requests
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional
from dataclasses import dataclass, field

logger = logging.getLogger('propiq.scrapers')

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
]

@dataclass
class ScraperConfig:
    source_name: str
    base_url: str
    requests_per_minute: float = 20.0
    max_retries: int = 3
    backoff_factor: float = 2.0
    timeout: int = 30
    use_proxy_rotation: bool = False
    proxies: list = field(default_factory=list)
    extra_headers: dict = field(default_factory=dict)

class BaseScraper(ABC):
    """
    Abstract base for all PropIQ scrapers.
    Subclass and implement:
        - fetch_listings(zip_code)
        - parse_listing(raw)
        - to_property_dict(parsed)
    """

    def __init__(self, config: ScraperConfig):
        self.config = config
        self.session = self._build_session()
        self._last_req = 0.0
        self._min_delay = 60.0 / config.requests_per_minute

    # Session
    def _build_session(self) -> requests.Session:
        s  = requests.Session()
        s.headers.update({
            'User-Agent': random.choice(USER_AGENTS),
            'Accept': 'application/json, text/html, */*',
            'Accept-Language': 'en-US, en; q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
            **self.config.extra_headers,
        })
        return s

    def _rotate_user_agent(self):
        self.session.headers['User-Agent'] = random.choice(USER_AGENTS)

    # Rate Limiting
    def _throttle(self):
        """Ensure we don't exceed requests_per_minute."""
        elapsed = time.time() - self._last_req
        if elapsed < self._min_delay:
            jitter = random.uniform(0, self._min_delay * 0.2)
            time.sleep(self._min_delay - elapsed + jitter)
        self._last_req = time.time()

    # HTTP With Retries
    def get(self, url: str, params: dict = None, **kwargs) -> Optional[requests.Response]:
        self._throttle()
        self._rotate_user_agent()

        proxy = None
        if self.config.use_proxy_rotation and self.config.proxies:
            proxy = {'http': random.choice(self.config.proxies),
                     'https': random.choice(self.config.proxies)}

        for attempt in range(self.config.max_retries):
            try:
                resp = self.session.get(
                    url,
                    params=params,
                    timeout=self.config.timeout,
                    proxies=proxy,
                    **kwargs
                )
                resp.raise_for_status()
                logger.debug(f'[{self.config.source_name}] GET {url} -> {resp.status_code}')
                return resp

            except requests.exceptions.HTTPError as e:
                status = e.response.status_code if e.response else 0
                if status == 429:
                    wait = (2 ** attempt) * self.config.backoff_factor + random.uniform(1, 5)
                    logger.warning(f'[{self.config.source_name}] 429 - waiting {wait:.1f}s')
                    time.sleep(wait)
                elif status in (403, 401):
                    logger.error(f'[{self.config.source_name}] Auth error {status} for {url}')
                    return None
                else:
                    logger.warning(f'[{self.config.source_name}] HTTP {status} attempt {attempt+1}')
                    time.sleep((2 ** attempt) * self.config.backoff_factor)

            except requests.exceptions.ConnectionError:
                wait = (2 ** attempt) * self.config.backoff_factor
                logger.warning(f'[{self.config.source_name}] Connection error, retry in {wait}s')
                time.sleep(wait)

            except requests.exceptions.Timeout:
                logger.warning(f'[{self.config.source_name}] Timeout on attempt {attempt+1}')
                time.sleep((2 ** attempt))

        logger.error(f'[{self.config.source_name}] All {self.config.max_retries} retries failed: {url}')
        return None

    # Abstract Interface
    @abstractmethod
    def fetch_listings(self, zip_code: list[str]) -> list[dict]:
        """
        Fetch raw listings data for the given zip code.
        Returns list of raw dicts (not yet normalized).
        """
        ...

    @abstractmethod
    def parse_listing(self, raw: dict) -> dict:
        """
        Normalize a single raw listing into PropIQ's standard shape.
        """
        ...

    @abstractmethod
    def to_property_dict(self, parsed: dict) -> dict:
        """
        Final mapping to match Property model fields.
        Override if the source needs extra transformation.
        """
        return parsed

    # Orchestration
    def run(self, zip_codes: list[str]) -> tuple[list[dict], dict]:
        """
        Full scrape run. Returns (properties, stats).
        Called by the pipeline scheduler.
        """
        started_at = datetime.utcnow()
        stats = {
            'fetched': 0,
            'parsed': 0,
            'errors': 0,
            'source': self.config.source_name
        }
        results = []

        logger.info(f'[{self.config.source_name}] Started scrape for {len(zip_codes)} zip codes]')

        try:
            raw_listings = self.fetch_listings(zip_codes)
            stats['fetched'] = len(raw_listings)

            for raw in raw_listings:
                try:
                    parsed = self.parse_listing(raw)
                    cleaned = self.to_property_dict(parsed)
                    results.append(cleaned)
                    stats['parsed'] += 1
                except Exception as e:
                    stats['errors'] += 1
                    logger.error(f'[{self.config.source_name}] Parse Error: {e}')

        except Exception as e:
            stats['errors'] += 1
            logger.error(f'[{self.config.source_name}] Fetch Error: {e}')

        duration = (datetime.utcnow() - started_at).total_seconds()
        stats['duration_secs'] = duration
        logger.info(f"[{self.config.source_name}] Done - {stats['parsed']}/{stats['fetched']} parsed in {duration:.1f}s")

        return results, stats