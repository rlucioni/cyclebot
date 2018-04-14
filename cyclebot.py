import logging
import os
from datetime import date, datetime, timedelta
from hashlib import md5
from logging.config import dictConfig

import requests
from dateutil import parser
from praw import Reddit
from redis import StrictRedis, RedisError
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

REDIS_HOST = os.environ.get('REDIS_HOST', 'localhost')
REDIS_PORT = int(os.environ.get('REDIS_PORT', 6379))
REDIS_PASSWORD = os.environ.get('REDIS_PASSWORD', '')
REDIS_KEY_VERSION = str(os.environ.get('REDIS_KEY_VERSION', 1))
REDIS_EXPIRE_SECONDS = int(os.environ.get('REDIS_EXPIRE_SECONDS', 3600 * 24))

MLB_STATS_ORIGIN = 'https://statsapi.mlb.com'
CAPTIVATING_INDEX_THRESHOLD = int(os.environ.get('CAPTIVATING_INDEX_THRESHOLD', 75))
STALE_PLAY_SECONDS = int(os.environ.get('STALE_PLAY_SECONDS', 300))
PLAYBACK_RESOLUTION = os.environ.get('PLAYBACK_RESOLUTION', '2500K')
UNIQUE_HIT_COUNT_THRESHOLD = int(os.environ.get('UNIQUE_HIT_COUNT_THRESHOLD', 3))
HITS = {
    'single': '1B',
    'double': '2B',
    'triple': '3B',
    'home run': 'HR',
}

SLACK_API_TOKEN = os.environ.get('SLACK_API_TOKEN')

REDDIT_CLIENT_ID = os.environ.get('REDDIT_CLIENT_ID')
REDDIT_CLIENT_SECRET = os.environ.get('REDDIT_CLIENT_SECRET')
REDDIT_USERAGENT = os.environ.get('REDDIT_USERAGENT')
REDDIT_USERNAME = os.environ.get('REDDIT_USERNAME')
REDDIT_PASSWORD = os.environ.get('REDDIT_PASSWORD')


# monkey patch to add support for nx/xx options
# https://github.com/andymccurdy/redis-py/issues/649
def zadd(self, name, items, nx=False, xx=False):
    if nx and xx:
        raise RedisError("ZADD can't use both NX and XX modes")

    pieces = []

    if nx:
        pieces.append('NX')
    if xx:
        pieces.append('XX')

    for pair in items:
        if len(pair) != 2:
            raise RedisError('ZADD items must be pairs')

        # score
        pieces.append(pair[0])
        # member
        pieces.append(pair[1])

    return self.execute_command('ZADD', name, *pieces)


StrictRedis.zadd = zadd


class Noop:
    def __init__(self, name):
        self.name = name

    def __getattr__(self, method):
        logger.info(f'{self.name} disabled, noop {method}')
        return self.noop

    def noop(self, *args, **kwargs):
        pass


class Cyclebot:
    def __init__(self):
        self.redis = StrictRedis(host=REDIS_HOST, port=REDIS_PORT, password=REDIS_PASSWORD)

        if SLACK_API_TOKEN:
            self.slack = SlackClient(SLACK_API_TOKEN)
        else:
            self.slack = Noop('slack')

        if REDDIT_USERNAME:
            reddit = Reddit(
                client_id=REDDIT_CLIENT_ID,
                client_secret=REDDIT_CLIENT_SECRET,
                user_agent=REDDIT_USERAGENT,
                username=REDDIT_USERNAME,
                password=REDDIT_PASSWORD,
            )
            self.subreddit = reddit.subreddit('baseball')
        else:
            self.subreddit = Noop('reddit')

        self.game_keys = set()

        self.feed = None
        self.plays = []
        self.inning_ordinal = None
        self.players = {}

        self.content = None
        self.highlights = {}
        self.content_key = None

    def poll(self):
        self.ingest_game_keys()

        for game_key in self.game_keys:
            self.process_game(game_key)

    def ingest_game_keys(self):
        today = date.today()
        yesterday = today - timedelta(days=1)

        for isoformatted in [yesterday.isoformat(), today.isoformat()]:
            logger.info(f'ingesting game keys for {isoformatted}')

            response = requests.get(f'{MLB_STATS_ORIGIN}/api/v1/schedule?sportId=1&date={isoformatted}')
            schedule = response.json()

            # Returned dates list can be empty. It can also contain multiple dates,
            # so we filter to make sure we get the date we want.
            games = []
            for day in schedule['dates']:
                if day['date'] == isoformatted:
                    games = day['games']

            for game in games:
                game_key = game['gamePk']

                # valid states: 'preview', 'live', 'final'
                state = game['status']['abstractGameState'].lower()
                if state != 'live':
                    logger.info(f'ignoring game {game_key}, state is {state}')
                    continue

                # TODO: ignore seriesDescription == 'Spring Training'
                self.game_keys.add(game_key)

    def process_game(self, game_key):
        logger.info(f'processing game {game_key}')

        self.ingest_game_feed(game_key)
        self.ingest_game_content(game_key)

        for play in self.plays:
            self.process_play(play)

        for player_id in self.players:
            self.cycle_check(player_id, game_key)

    def ingest_game_feed(self, game_key):
        logger.info(f'ingesting feed for game {game_key}')

        response = requests.get(f'{MLB_STATS_ORIGIN}/api/v1.1/game/{game_key}/feed/live')
        self.feed = response.json()

        # plays are ordered least to most recent
        self.plays = self.feed['liveData']['plays']['allPlays']
        self.inning_ordinal = self.feed['liveData']['linescore'].get('currentInningOrdinal')

        self.players = {}
        for team in self.feed['liveData']['boxscore']['teams'].values():
            for player in team['players'].values():
                player_id = int(player['person']['id'])
                self.players[player_id] = {
                    'id': player_id,
                    'name': player['person']['fullName'],
                    'hits': player['stats']['batting'].get('hits', 0),
                    'at_bats': player['stats']['batting'].get('atBats', 0),
                    'unique_hits': [],
                }

    def ingest_game_content(self, game_key):
        logger.info(f'ingesting content for game {game_key}')

        response = requests.get(f'{MLB_STATS_ORIGIN}/api/v1/game/{game_key}/content')
        self.content = response.json()

        self.highlights = {}
        for highlight in self.content['highlights']['live']['items']:
            self.highlights[int(highlight['id'])] = highlight

        pairs = [(self.now(), highlight_id) for highlight_id in self.highlights]
        self.content_key = self.make_key(game_key, 'content')

        if pairs:
            self.redis.zadd(self.content_key, pairs, nx=True)
            self.redis.expire(self.content_key, REDIS_EXPIRE_SECONDS)

    def process_play(self, play):
        event = play['result'].get('event', '').lower()
        hit_code = HITS.get(event)

        if hit_code:
            batter_id = int(play['matchup']['batter']['id'])
            batter = self.players[batter_id]

            if hit_code not in batter['unique_hits']:
                batter['unique_hits'].append(hit_code)

            captivating_index = play['about'].get('captivatingIndex', 0)
            if hit_code == 'HR' or captivating_index >= CAPTIVATING_INDEX_THRESHOLD:
                self.seek_highlight(play, batter, hit_code, captivating_index)

    def seek_highlight(self, play, batter, hit_code, captivating_index):
        play_uuid = play['playEvents'][-1]['playId']
        batter_name = batter['name']
        batter_id = batter['id']

        play_end = int(parser.parse(play['about']['endTime']).timestamp())
        seconds_elapsed = self.now() - play_end
        is_stale = seconds_elapsed > STALE_PLAY_SECONDS

        play_key = self.make_key(play_uuid)
        is_cached = bool(self.redis.get(play_key))

        if is_stale:
            logger.info(
                'ignoring stale play '
                f'{play_uuid} {batter_name} {hit_code} {captivating_index}, '
                f'{seconds_elapsed} seconds elapsed'
            )
        elif is_cached:
            logger.info(
                'ignoring cached play '
                f'{play_uuid} {batter_name} {hit_code} {captivating_index}'
            )
        else:
            logger.info(
                'seeking highlight for play '
                f'{play_uuid} {batter_name} {hit_code} {captivating_index}, '
                f'{seconds_elapsed} seconds elapsed'
            )

            highlight_ids = self.redis.zrangebyscore(self.content_key, play_end, '+inf')

            logger.info(f'{len(highlight_ids)} highlights since play {play_uuid}')

            highlights_by_sv_id = {}
            highlights_by_player_id = {}

            for highlight_id in highlight_ids:
                highlight = self.highlights[int(highlight_id)]

                for keyword in highlight['keywordsAll']:
                    if keyword['type'] == 'sv_id':
                        highlights_by_sv_id[keyword['value']] = highlight

                    if keyword['type'] == 'player_id':
                        highlights_by_player_id[int(keyword['value'])] = highlight

            # fall back to player_id when sv_id is missing
            highlight = highlights_by_sv_id.get(play_uuid) or highlights_by_player_id.get(batter_id)
            if highlight:
                logger.info(f'sharing highlight for play {play_uuid}')

                self.redis.set(play_key, 1, ex=REDIS_EXPIRE_SECONDS)

                for playback in highlight['playbacks']:
                    playback_url = playback['url']
                    if PLAYBACK_RESOLUTION in playback_url:
                        break

                description = highlight['description']
                self.post_slack_message(f'<{playback_url}|{description}> ({play_uuid})')
                self.post_reddit_link(description, playback_url)
            else:
                logger.info(f'highlight unavailable for play {play_uuid}')

    def cycle_check(self, player_id, game_key):
        player = self.players[player_id]
        unique_hits = player['unique_hits']
        unique_hit_count = len(unique_hits)

        # TODO: message if player completes the cycle
        if unique_hit_count >= UNIQUE_HIT_COUNT_THRESHOLD:
            name = player['name']
            joined_hits = ', '.join(unique_hits)

            cache_key = self.make_key(game_key, player_id, unique_hit_count)
            is_cached = bool(self.redis.get(cache_key))

            if is_cached:
                logger.info(f'ignoring {name} with {unique_hit_count} unique hits, in cache')
                return

            logger.info(f'notifying about {name} with {joined_hits}')
            self.redis.set(cache_key, 1, ex=REDIS_EXPIRE_SECONDS)

            hits = player['hits']
            at_bats = player['at_bats']
            # TODO: only post cycle alert if before 9th inning and player already has a 3B
            # TODO: include data about how likely player is to get missing hit (count of missing hit / plate apps)
            self.post_slack_message(
                f'CYCLE ALERT: {name} {hits}-{at_bats} with {joined_hits} in the {self.inning_ordinal} inning'
            )

    def now(self):
        return int(datetime.now().timestamp())

    def make_key(self, *args):
        key = '-'.join([REDIS_KEY_VERSION] + [str(arg) for arg in args])
        return md5(key.encode('utf-8')).hexdigest()

    def post_slack_message(self, message, channel='#sandbox'):
        self.slack.api_call(
            'chat.postMessage',
            channel=channel,
            text=message
        )

    def post_reddit_link(self, title, url):
        # https://praw.readthedocs.io/en/latest/code_overview/models/subreddit.html#praw.models.Subreddit.submit
        self.subreddit.submit(
            title,
            url=url,
            resubmit=False,
            send_replies=False,
        )


cyclebot = Cyclebot()


def poll():
    cyclebot.poll()


if __name__ == '__main__':
    poll()
