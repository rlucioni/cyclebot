import logging
import os
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
REDIS_PORT = int(os.environ.get('REDIS_PORT', 6379))
REDIS_PASSWORD = os.environ.get('REDIS_PASSWORD', '')
redis = StrictRedis(host=REDIS_HOST, port=REDIS_PORT, password=REDIS_PASSWORD)

MLB_STATS_ORIGIN = 'https://statsapi.mlb.com'
MLB_SEARCH_TEMPLATE = 'https://search-api.mlb.com/svc/search/v2/mlb_global_sitesearch_en/query?q={play_uuid}'
MLB_CONTENT_TEMPLATE = 'https://content.mlb.com/mlb/item/id/v1/{asset_id}/details/web-v1.json'

HITS = {
    'single': '1B',
    'double': '2B',
    'triple': '3B',
    'home run': 'HR',
}

CAPTIVATING_INDEX_THRESHOLD = int(os.environ.get('CAPTIVATING_INDEX_THRESHOLD', 70))
PLAYBACK_RESOLUTION = os.environ.get('PLAYBACK_RESOLUTION', '2500K')
UNIQUE_HIT_COUNT_THRESHOLD = int(os.environ.get('UNIQUE_HIT_COUNT_THRESHOLD', 2))


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


def make_key(*args):
    key = '-'.join([str(CACHE_VERSION)] + [str(arg) for arg in args])
    return hash(key)


def post_message(message, channel='#sandbox'):
    slack.api_call(
        'chat.postMessage',
        channel=channel,
        text=message
    )


def handle_captivating(play):
    captivating_index = play['about'].get('captivatingIndex', 0)
    if captivating_index >= CAPTIVATING_INDEX_THRESHOLD:
        play_uuid = play['playEvents'][-1]['playId']

        cache_key = make_key(play_uuid)
        is_cached = bool(redis.get(cache_key))

        if is_cached:
            logger.info(f'skipping play {play_uuid} with captivating index of {captivating_index}')
            return

        logger.info(f'notifying about play {play_uuid} with captivating index of {captivating_index}')
        redis.set(cache_key, 1, 3600 * 24)

        response = requests.get(MLB_SEARCH_TEMPLATE.format(play_uuid=play_uuid))
        data = response.json()
        asset_id = data['docs'][0]['asset_id']

        response = requests.get(MLB_CONTENT_TEMPLATE.format(asset_id=asset_id))
        data = response.json()
        for playback in data['playbacks']:
            playback_url = playback['url']
            if PLAYBACK_RESOLUTION in playback_url:
                description = data['description']
                post_message(f'HIGHLIGHT: <{playback_url}|{description}>')


def cyclewatch():
    formatted_dates = get_formatted_dates()
    game_keys = set()

    for formatted_date in formatted_dates:
        logger.info(f'getting game keys for {formatted_date}')

        response = requests.get(f'{MLB_STATS_ORIGIN}/api/v1/schedule?sportId=1&date={formatted_date}')
        data = response.json()

        # Returned dates list can be empty. It can also contain multiple dates,
        # so we filter to make sure we get the date we want.
        games = []
        for day in data['dates']:
            if day['date'] == formatted_date:
                games = day['games']

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

        players = {}
        for team in data['liveData']['boxscore']['teams'].values():
            for player in team['players'].values():
                player_id = player['person']['id']
                players[player_id] = {
                    'name': player['person']['fullName'],
                    'hits': player['stats']['batting'].get('hits', 0),
                    'at_bats': player['stats']['batting'].get('atBats', 0),
                    'unique_hits': [],
                }

        # plays come sorted in chronological order
        plays = data['liveData']['plays']['allPlays']
        for play in plays:
            handle_captivating(play)

            event = play['result'].get('event', '').lower()
            hit_code = HITS.get(event)

            if hit_code:
                batter_id = play['matchup']['batter']['id']
                batter = players[batter_id]

                if hit_code not in batter['unique_hits']:
                    batter['unique_hits'].append(hit_code)

        inning_ordinal = data['liveData']['linescore'].get('currentInningOrdinal')
        for player_id, player in players.items():
            unique_hits = player['unique_hits']
            unique_hit_count = len(unique_hits)

            # TODO: message if player completes the cycle
            if unique_hit_count >= UNIQUE_HIT_COUNT_THRESHOLD:
                name = player['name']
                joined_hits = ', '.join(unique_hits)

                cache_key = make_key(game_key, player_id, unique_hit_count)
                is_cached = bool(redis.get(cache_key))

                if is_cached:
                    logger.info(f'skipping {name} with {unique_hit_count} unique hits, in cache')
                    continue

                logger.info(f'notifying about {name} with {joined_hits}')
                redis.set(cache_key, 1, 3600 * 24)

                hits = player['hits']
                at_bats = player['at_bats']
                post_message(
                    f'CYCLE ALERT: {name} {hits}-{at_bats} with {joined_hits} in the {inning_ordinal} inning'
                )


if __name__ == '__main__':
    cyclewatch()
