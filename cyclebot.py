import logging
from datetime import datetime
from logging.config import dictConfig

import requests
from pytz import timezone


MLB_STATS_ORIGIN = 'https://statsapi.mlb.com'

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


def now():
    return datetime.now(tz=timezone('America/New_York'))


def cyclewatch():
    today = now().strftime('%Y-%m-%d')
    logger.info(f'getting game keys for {today}')

    response = requests.get(f'{MLB_STATS_ORIGIN}/api/v1/schedule?sportId=1&date={today}')
    data = response.json()

    games = data['dates'][0]['games']
    game_keys = []
    for game in games:
        game_key = game['gamePk']

        # valid states: 'preview', 'live', 'final'
        state = game['status']['abstractGameState'].lower()
        if state != 'live':
            logger.info(f'skipping game {game_key}, state is {state}')
            continue

        game_keys.append(game['gamePk'])

    for game_key in game_keys:
        logger.info(f'getting game data for game {game_key}')

        response = requests.get(f'{MLB_STATS_ORIGIN}/api/v1.1/game/{game_key}/feed/live')
        data = response.json()

        score = data['liveData']['linescore']['teams']
        logger.info(score)
