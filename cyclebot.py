import logging
import os
from collections import defaultdict
from datetime import date, datetime, timedelta
from hashlib import md5
from logging.config import dictConfig

import requests
from pytz import timezone
from redis import StrictRedis
from slackclient import SlackClient


dictConfig({
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'standard': {
            'format': '{asctime} {levelname} {process} [{filename}:{lineno}] - {message}',
            'style': '{',
        }
    },
    'handlers': {
        'console': {
            'level': 'INFO',
            'class': 'logging.StreamHandler',
            'formatter': 'standard',
        },
    },
    'loggers': {
        '': {
            'handlers': ['console'],
            'level': 'DEBUG',
            'propagate': True,
        },
    },
})

logger = logging.getLogger(__name__)

SLACK_API_TOKEN = os.environ['SLACK_API_TOKEN']
slack = SlackClient(SLACK_API_TOKEN)

CACHE_VERSION = 1
REDIS_HOST = os.environ.get('REDIS_HOST', '0.0.0.0')
REDIS_PASSWORD = os.environ.get('REDIS_PASSWORD', '')
redis = StrictRedis(host=REDIS_HOST, password=REDIS_PASSWORD)

MLB_STATS_ORIGIN = 'https://statsapi.mlb.com'
HITS = {
    'single',
    'double',
    'triple',
    'home run',
}


def post_message(message, channel='#sandbox'):
    slack.api_call(
        'chat.postMessage',
        channel=channel,
        text=message
    )


def get_formatted_dates():
    now = datetime.now(tz=timezone('America/New_York'))
    timestamp = now.timestamp()
    today = date.fromtimestamp(timestamp)

    # Handle cases where games run late by looking at yesterday's games as well
    # as today's games. The MLB API looks like it could handle this case by
    # returning game data for multiple dates, but can't be sure.
    yesterday = today - timedelta(days=1)

    return [yesterday.isoformat(), today.isoformat()]


def hash(text):
    return md5(text.encode('utf-8')).hexdigest()


def make_key(game_key, batter, hits):
    return f'{CACHE_VERSION}-{game_key}-{hash(batter)}-{len(hits)}'


def cyclewatch():
    formatted_dates = get_formatted_dates()
    game_keys = set()

    for formatted_date in formatted_dates:
        logger.info(f'getting game keys for {formatted_date}')

        response = requests.get(f'{MLB_STATS_ORIGIN}/api/v1/schedule?sportId=1&date={formatted_date}')
        data = response.json()

        # Returned dates array can be empty. It can also contain multiple dates,
        # so we filter to make sure we get the date we want.
        returned_dates = data['dates']
        games = []
        for returned_date in returned_dates:
            if returned_date['date'] == formatted_date:
                games = returned_date['games']

        for game in games:
            game_key = game['gamePk']

            # valid states: 'preview', 'live', 'final'
            state = game['status']['abstractGameState'].lower()
            if state != 'live':
                logger.info(f'skipping game {game_key}, state is {state}')
                continue

            # TODO: ignore seriesDescription == 'Spring Training'
            game_keys.add(game_key)

    for game_key in game_keys:
        logger.info(f'getting game data for game {game_key}')

        response = requests.get(f'{MLB_STATS_ORIGIN}/api/v1.1/game/{game_key}/feed/live')
        data = response.json()

        # plays are ordered chronologically
        plays = data['liveData']['plays']['allPlays']
        batters = defaultdict(set)
        for play in plays:
            event = play['result'].get('event', '').lower()
            if event in HITS:
                batter = play['matchup']['batter']['fullName']
                batters[batter].add(event)

        inning_ordinal = data['liveData']['linescore'].get('currentInningOrdinal')
        for batter, hits in batters.items():
            # TODO: generate message like 'Whit Merrifield is 3-3 with a HR, 3B, and 2B in the 6th inning'
            # requires hits/at-bats, order of hits
            hit_count = len(hits)
            if hit_count >= 2:
                joined_hits = ', '.join(hits)
                cache_key = make_key(game_key, batter, hits)

                in_cache = bool(redis.get(cache_key))
                if in_cache:
                    logger.info(f'skipping {batter} with {joined_hits}, in cache')
                    continue

                logger.info(f'notifying about {batter} with {joined_hits}')
                redis.set(cache_key, 1, 3600 * 24)
                post_message(f'{batter} has {joined_hits} in the {inning_ordinal} inning')


if __name__ == '__main__':
    cyclewatch()
