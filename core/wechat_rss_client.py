#!/usr/bin/env python3
"""Text RSS Text - Text WeWe RSS Text"""

import requests
import feedparser
import logging
from pathlib import Path
from datetime import datetime, timedelta
import json
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)


class WeChatRSSClient:
    """Text RSS Text, Text WeWe RSS Text"""

    def __init__(self, wewe_rss_url: str = "http://localhost:4000", cache_dir: Optional[Path] = None):
        """Text RSS Text

        Args:
            wewe_rss_url: WeWe RSS Text
            cache_dir: Text, DefaultText ~/REDACTED/wechat_cache
        """
        self.wewe_rss_url = wewe_rss_url.rstrip('/')
        self.cache_dir = cache_dir or Path.home() / 'REDACTED' / 'wechat_cache'
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })

    def get_feed(self, mp_id: str, format: str = 'json', use_cache: bool = True) -> Dict:
        """Text RSS Text

        Args:
            mp_id: Text ID
            format: Text (json/rss/atom)
            use_cache: Text(6Text)

        Returns:
            Feed Text
        """
        cache_file = self.cache_dir / f"{mp_id}_{format}.json"

        # Text
        if use_cache and cache_file.exists():
            cache_time = datetime.fromtimestamp(cache_file.stat().st_mtime)
            if datetime.now() - cache_time < timedelta(hours=6):
                try:
                    with open(cache_file, 'r', encoding='utf-8') as f:
                        return json.load(f)
                except (json.JSONDecodeError, IOError) as e:
                    logger.warning("TextFailed: %s", e)

        # Text WeWe RSS API
        try:
            url = f"{self.wewe_rss_url}/feeds/{mp_id}.{format}"
            response = self.session.get(url, timeout=30)
            response.raise_for_status()

            if format == 'json':
                data = response.json()
            else:
                # Text RSS/Atom Text
                feed = feedparser.parse(response.content)
                data = self._parse_feed(feed)

            # SaveText
            with open(cache_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            return data

        except requests.exceptions.RequestException as e:
            logger.error("Text %s RSS Failed: %s", mp_id, e)
            # Text, Text
            if cache_file.exists():
                try:
                    with open(cache_file, 'r', encoding='utf-8') as f:
                        return json.load(f)
                except (json.JSONDecodeError, IOError):
                    pass
            return {}

    def get_articles(self, mp_id: str, keyword: Optional[str] = None, use_cache: bool = True) -> List[Dict]:
        """Text

        Args:
            mp_id: Text ID
            keyword: Text(Text)
            use_cache: Text

        Returns:
            Text, Text: 
            - title: Text
            - link: Text
            - published: Text
            - summary: Summary
            - content: Text(Text)
        """
        try:
            # Text RSS Text JSON, Text RSS Text
            feed_data = self.get_feed(mp_id, format='rss', use_cache=use_cache)

            # Text
            articles = []
            items = feed_data.get('items', [])

            for item in items:
                article = {
                    'title': item.get('title', ''),
                    'link': item.get('url', ''),
                    'published': item.get('date_published', ''),
                    'summary': item.get('summary', ''),
                    'content': item.get('content_html', ''),
                    'author': item.get('author', {}).get('name', '') if isinstance(item.get('author'), dict) else item.get('author', ''),
                    'mp_id': mp_id
                }
                articles.append(article)

            return articles

        except Exception as e:
            logger.error("Text %s TextFailed: %s", mp_id, e)
            return []

    def get_multiple_accounts_articles(self, mp_ids: List[str], keyword: Optional[str] = None) -> List[Dict]:
        """Text

        Args:
            mp_ids: Text ID Text
            keyword: Text(Text)

        Returns:
            Text, Text
        """
        all_articles = []

        for mp_id in mp_ids:
            try:
                articles = self.get_articles(mp_id, keyword=keyword, use_cache=False)
                all_articles.extend(articles)
            except Exception as e:
                logger.error("Text %s Failed: %s", mp_id, e)
                continue

        # Text(Text)
        def get_timestamp(article):
            published = article.get('published', '')
            if not published:
                return 0
            try:
                from dateutil import parser
                dt = parser.parse(published)
                return dt.timestamp()
            except (ValueError, TypeError, AttributeError):
                # TextFailed, Text
                return published

        all_articles.sort(key=get_timestamp, reverse=True)

        return all_articles

    def _parse_feed(self, feed) -> Dict:
        """Text feedparser Text

        Args:
            feed: feedparser.FeedParserDict Text

        Returns:
            Text Feed Text
        """
        import re

        data = {
            'title': feed.feed.get('title', ''),
            'link': feed.feed.get('link', ''),
            'description': feed.feed.get('description', ''),
            'items': []
        }

        for entry in feed.entries:
            # TextSummaryText
            summary = entry.get('summary', '')
            content_html = entry.get('content', [{}])[0].get('value', '') if entry.get('content') else ''

            # TextSummary: Text HTML Text, Text, Text
            if summary:
                # Text HTML Text
                clean_summary = re.sub(r'<[^>]+>', '', summary)
                # Text &amp; Text HTML Text
                if re.match(r'^(&[a-z]+;|\s|&)+$', clean_summary):
                    summary = ''
                else:
                    summary = clean_summary.strip()

            # TextSummaryText, Text
            if not summary and content_html:
                # TextHTMLText, TextSummary
                text = re.sub(r'<[^>]+>', '', content_html)
                text = re.sub(r'\s+', ' ', text).strip()
                # Text HTML Text
                if not re.match(r'^(&[a-z]+;|\s|&)+$', text):
                    summary = text[:200] + '...' if len(text) > 200 else text

            item = {
                'title': entry.get('title', ''),
                'url': entry.get('link', ''),
                'date_published': entry.get('published', ''),
                'summary': summary,
                'content_html': content_html,
                'author': {
                    'name': entry.get('author', '')
                }
            }
            data['items'].append(item)

        return data

    def check_service_health(self) -> bool:
        """Text WeWe RSS Text

        Returns:
            True Text, False Text
        """
        try:
            # Text feeds Text, Text
            response = self.session.get(f"{self.wewe_rss_url}/feeds", timeout=5)
            # 200 Text 401 Text(401 Text)
            return response.status_code in [200, 401]
        except Exception as e:
            logger.error("TextFailed: %s", e)
            return False

    def get_all_feeds(self) -> List[Dict]:
        """Text WeWe RSS Text

        Returns:
            Text, Text: 
            - id: Text ID
            - name: Text
            - intro: Text
            - cover: Text
        """
        try:
            response = self.session.get(f"{self.wewe_rss_url}/feeds", timeout=10)
            response.raise_for_status()

            feeds = response.json()
            return feeds if isinstance(feeds, list) else []
        except Exception as e:
            logger.error("TextFailed: %s", e)
            return []

    def clear_cache(self, mp_id: Optional[str] = None):
        """Text

        Args:
            mp_id: Text ID, Text None Text
        """
        if mp_id:
            for cache_file in self.cache_dir.glob(f"{mp_id}_*.json"):
                cache_file.unlink()
        else:
            for cache_file in self.cache_dir.glob("*.json"):
                cache_file.unlink()
