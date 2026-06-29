"""Twitter APIText"""

import json
import logging
import requests
import time
from typing import List, Dict, Optional
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)


class TwitterClient:
    """Twitter APIText (Text twitterapi.io)"""

    BASE_URL = "https://api.twitterapi.io"
    CACHE_DURATION = 86400  # Text1Text(86400Text)

    def __init__(self, api_key: str, cache_dir: Path = None):
        """
        Text

        Args:
            api_key: twitterapi.io APIText
            cache_dir: Text
        """
        self.api_key = api_key
        self.session = requests.Session()
        self.session.headers.update({
            'X-API-Key': api_key,  # twitterapi.io Text X-API-Key header
            'Content-Type': 'application/json'
        })
        self.request_delay = 1.0  # Text(Text), Text

        # SettingsText
        if cache_dir:
            self.cache_dir = cache_dir
        else:
            from pathlib import Path
            self.cache_dir = Path.home() / 'REDACTED' / 'twitter_cache'

        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _get_cache_path(self, username: str) -> Path:
        """Text"""
        return self.cache_dir / f"{username}.json"

    def _is_cache_valid(self, cache_path: Path) -> bool:
        """Text"""
        if not cache_path.exists():
            return False

        # Text
        cache_time = datetime.fromtimestamp(cache_path.stat().st_mtime)
        age = (datetime.now() - cache_time).total_seconds()

        return age < self.CACHE_DURATION

    def _load_cache(self, username: str) -> Optional[List[Dict]]:
        """TextLoadText"""
        cache_path = self._get_cache_path(username)

        if self._is_cache_valid(cache_path):
            try:
                with open(cache_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    logger.debug("Text @%s (%d Text)", username, len(data))
                    return data
            except Exception as e:
                logger.warning("TextFailed @%s: %s", username, e)

        return None

    def _save_cache(self, username: str, tweets: List[Dict]):
        """SaveText"""
        cache_path = self._get_cache_path(username)

        try:
            with open(cache_path, 'w', encoding='utf-8') as f:
                json.dump(tweets, f, ensure_ascii=False, indent=2)
            logger.debug("Text @%s (%d Text)", username, len(tweets))
        except Exception as e:
            logger.warning("SaveTextFailed @%s: %s", username, e)

    def get_user_tweets(self, username: str, limit: int = 20, use_cache: bool = True) -> List[Dict]:
        """
        Text

        Args:
            username: TwitterText(Text@)
            limit: Text
            use_cache: Text

        Returns:
            Text
        """
        # TextLoad
        if use_cache:
            cached_tweets = self._load_cache(username)
            if cached_tweets is not None:
                return cached_tweets

        try:
            # Text, Text
            time.sleep(self.request_delay)

            # twitterapi.io Text last_tweets Text
            url = f"{self.BASE_URL}/twitter/user/last_tweets"
            params = {
                'userName': username,  # Text userName
                'count': limit
            }

            logger.info("Text @%s Text...", username)

            response = self.session.get(url, params=params, timeout=30)

            # Text
            if response.status_code == 429:
                logger.warning("Text - @%s, Text", username)
                return []

            response.raise_for_status()
            data = response.json()

            # Text
            if isinstance(data, list):
                tweets = data
            elif isinstance(data, dict):
                # twitterapi.io Text: {"data": {"tweets": [...]}}
                if 'data' in data and isinstance(data['data'], dict):
                    tweets = data['data'].get('tweets', [])
                elif 'data' in data and isinstance(data['data'], list):
                    tweets = data.get('data', [])
                elif 'tweets' in data:
                    tweets = data.get('tweets', [])
                else:
                    tweets = []
            else:
                tweets = []

            # Text
            for tweet in tweets:
                if isinstance(tweet, dict):
                    tweet['_username'] = username

            logger.info("@%s: Text %d Text", username, len(tweets))

            # SaveText
            if tweets and use_cache:
                self._save_cache(username, tweets)

            return tweets

        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 429:
                logger.warning("Text - @%s, Text", username)
            else:
                logger.warning("HTTPError @%s: %s", username, e)
            return []
        except Exception as e:
            logger.warning("Text @%s TextFailed: %s", username, e)
            return []

    def get_user_info(self, username: str) -> Optional[Dict]:
        """
        Text
        Text

        Args:
            username: TwitterText

        Returns:
            Text
        """
        try:
            # twitterapi.io Text
            # Text
            url = f"{self.BASE_URL}/twitter/user/last_tweets"
            params = {
                'userName': username,
                'count': 1  # Text1Text
            }

            logger.debug("TextURL: %s", url)
            logger.debug("Text: %s", params)

            response = self.session.get(url, params=params, timeout=10)

            logger.debug("TextStatusText: %s", response.status_code)
            logger.debug("Text: %s", response.text[:500])

            response.raise_for_status()
            data = response.json()

            # TextSuccessText, Text
            if isinstance(data, list) and len(data) > 0:
                # Text
                tweet = data[0]
                return {
                    'username': username,
                    'name': tweet.get('user', {}).get('name', username),
                    'verified': True  # Text
                }
            elif isinstance(data, dict) and ('data' in data or 'tweets' in data):
                return {
                    'username': username,
                    'name': username,
                    'verified': True
                }
            else:
                logger.debug("Text: %s", data)
                return None

        except requests.exceptions.HTTPError as e:
            logger.warning("HTTPError - StatusText: %s", e.response.status_code)
            logger.debug("Text: %s", e.response.text)
            return None
        except Exception as e:
            logger.warning("Text @%s TextFailed: %s", username, e)
            return None

    def get_multiple_users_tweets(
        self,
        usernames: List[str],
        limit_per_user: int = 10,
        use_cache: bool = True
    ) -> List[Dict]:
        """
        Text

        Args:
            usernames: Text
            limit_per_user: Text
            use_cache: Text

        Returns:
            Text
        """
        all_tweets = []

        for username in usernames:
            tweets = self.get_user_tweets(username, limit_per_user, use_cache)
            all_tweets.extend(tweets)

        # Text(Text)
        # twitterapi.io Text createdAt Text, Text: "Sun Mar 01 13:25:12 +0000 2026"
        def get_sort_key(tweet):
            created_at = tweet.get('createdAt') or tweet.get('created_at', '')
            if not created_at:
                return datetime.min

            try:
                # TextTwitterDateText
                return datetime.strptime(created_at, '%a %b %d %H:%M:%S %z %Y')
            except (ValueError, TypeError):
                # TextFailed, Text(Text)
                return created_at

        all_tweets.sort(key=get_sort_key, reverse=True)

        logger.info("Text, Text %d Text", len(all_tweets))
        if all_tweets:
            first = all_tweets[0]
            last = all_tweets[-1]
            logger.debug("Text: %s (@%s)", (first.get('createdAt', 'N/A')[:20]), first.get('_username'))
            logger.debug("Text: %s (@%s)", (last.get('createdAt', 'N/A')[:20]), last.get('_username'))

        return all_tweets

    def search_tweets(self, query: str, limit: int = 20) -> List[Dict]:
        """
        SearchText

        Args:
            query: SearchText
            limit: ResultText

        Returns:
            Text
        """
        try:
            url = f"{self.BASE_URL}/twitter/search"
            params = {
                'query': query,
                'limit': limit
            }

            response = self.session.get(url, params=params, timeout=30)
            response.raise_for_status()
            data = response.json()

            return data.get('data', [])

        except Exception as e:
            logger.warning("SearchTextFailed: %s", e)
            return []
