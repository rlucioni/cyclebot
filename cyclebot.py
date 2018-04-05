import logging
from datetime import date, datetime, timedelta
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


def get_formatted_dates():
    now = datetime.now(tz=timezone('America/New_York'))
    timestamp = now.timestamp()
    today = date.fromtimestamp(timestamp)

    # Handle cases where games run late by looking at yesterday's games as well
    # as today's games. The MLB API looks like it could handle this case by
    # returning game data for multiple dates, but can't be sure.
    yesterday = today - timedelta(days=1)

    return [yesterday.isoformat(), today.isoformat()]


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

        score = data['liveData']['linescore']['teams']
        logger.info(score)


if __name__ == '__main__':
    cyclewatch()
