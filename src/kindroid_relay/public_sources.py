"""Bounded public reads. No model tools, credentials, arbitrary URLs or retries."""
from __future__ import annotations

import json
import re
import time
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from html import unescape
from urllib.parse import urlsplit

import httpx


def plain(value: str, maximum: int) -> str:
    return ' '.join(unescape(re.sub(r'<[^>]*>', '', value)).split())[:maximum]


def public_link(value: str) -> str:
    parts = urlsplit(value)
    if (parts.scheme not in {'http', 'https'} or not parts.hostname or parts.username or parts.password
            or parts.port not in {None, 80, 443} or '.' not in parts.hostname
            or parts.hostname.endswith(('.local', '.localhost'))):
        raise ValueError('invalid public source link')
    import ipaddress
    try:
        ipaddress.ip_address(parts.hostname)
    except ValueError:
        return value[:2000]
    raise ValueError('numeric source links are not supported')


class PublicSources:
    """At most one request per lookup, 256 KiB, 15 seconds; fixed destinations."""
    def read(self, endpoint: str, params: dict | None = None) -> bytes:
        if endpoint not in {'https://en.wikipedia.org/w/api.php',
                            'https://feeds.bbci.co.uk/news/world/rss.xml',
                            'https://news.google.com/rss/search'}:
            raise ValueError('public destination is not allowed')
        started = time.monotonic()
        with httpx.Client(timeout=5, follow_redirects=False, trust_env=False,
                          headers={'User-Agent': 'KindroidRelay/0.1 (bounded local research)',
                                   'Accept-Encoding': 'identity'}) as client:
            with client.stream('GET', endpoint, params=params) as response:
                if response.status_code != 200:
                    raise ValueError('public source unavailable; not retried')
                data = bytearray()
                for chunk in response.iter_bytes(8192):
                    data.extend(chunk)
                    if len(data) > 262144 or time.monotonic() - started > 15:
                        raise ValueError('public source exceeded read budget')
                return bytes(data)

    def news(self, area: str = '') -> list[dict]:
        endpoint = 'https://news.google.com/rss/search' if area else 'https://feeds.bbci.co.uk/news/world/rss.xml'
        raw = self.read(endpoint, {'q': area, 'hl': 'en', 'gl': 'DE', 'ceid': 'DE:en'} if area else None)
        if b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():
            raise ValueError('RSS entities are not allowed')
        root = ET.fromstring(raw)
        now, result = int(time.time()), []
        for item in root.findall('./channel/item')[:20]:
            try:
                published = int(parsedate_to_datetime(item.findtext('pubDate', '')).timestamp())
                if not now - 3 * 86400 <= published <= now + 300:
                    continue
                title = plain(item.findtext('title', ''), 160)
                url = public_link(item.findtext('link', ''))
                if not title:
                    continue
                result.append({'title': title, 'url': url, 'excerpt': plain(item.findtext('description', ''), 180),
                               'published_at': published, 'retrieved_at': now, 'query': ''})
            except (ValueError, TypeError, OverflowError):
                continue
        return result[:6]

    def research(self, topic: str) -> list[dict]:
        if not isinstance(topic, str) or not 1 <= len(topic.strip()) <= 180:
            raise ValueError('public topic required')
        raw = self.read('https://en.wikipedia.org/w/api.php', {
            'action': 'query', 'format': 'json', 'formatversion': 2, 'generator': 'search',
            'gsrsearch': topic, 'gsrnamespace': 0, 'gsrlimit': 3, 'prop': 'extracts',
            'exintro': 1, 'explaintext': 1, 'exchars': 450, 'exlimit': 3})
        pages = json.loads(raw).get('query', {}).get('pages', [])
        result = []
        for page in sorted(pages, key=lambda p: p.get('index', 0))[:3]:
            if type(page.get('pageid')) is not int or page['pageid'] <= 0 or not page.get('extract'):
                continue
            result.append({'title': plain(page['title'], 120), 'excerpt': plain(page['extract'], 450),
                'url': f'https://en.wikipedia.org/?curid={page["pageid"]}',
                'published_at': None, 'retrieved_at': int(time.time())})
        if not result:
            raise ValueError('no usable public reference found')
        return result
