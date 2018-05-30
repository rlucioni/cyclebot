import logging
import os
import time
from datetime import date, timedelta
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

CYCLE_ALERT_HITS = int(os.environ.get('CYCLE_ALERT_HITS', 3))
FAVORITE_PLAYER_IDS = [
    int(player_id) for player_id in os.environ.get('FAVORITE_PLAYER_IDS', '660271,592450').split(',')
]
HITS = {
    'single': '1B',
    'double': '2B',
    'triple': '3B',
    'home run': 'HR',
}
MIN_CAPTIVATING_INDEX = int(os.environ.get('MIN_CAPTIVATING_INDEX', 75))
MLB_STATS_ORIGIN = 'https://statsapi.mlb.com'
PITCHING_ALERT_INNINGS = int(os.environ.get('PITCHING_ALERT_INNINGS', 7))
PLAYBACK_RESOLUTION = os.environ.get('PLAYBACK_RESOLUTION', '2500K')
REDDIT_CLIENT_ID = os.environ.get('REDDIT_CLIENT_ID')
REDDIT_CLIENT_SECRET = os.environ.get('REDDIT_CLIENT_SECRET')
REDDIT_PASSWORD = os.environ.get('REDDIT_PASSWORD')
REDDIT_USERAGENT = os.environ.get('REDDIT_USERAGENT')
REDDIT_USERNAME = os.environ.get('REDDIT_USERNAME')
REDIS_EXPIRE_SECONDS = int(os.environ.get('REDIS_EXPIRE_SECONDS', 3600 * 24))
REDIS_HOST = os.environ.get('REDIS_HOST', 'localhost')
REDIS_KEY_VERSION = str(os.environ.get('REDIS_KEY_VERSION', 1))
REDIS_PASSWORD = os.environ.get('REDIS_PASSWORD', '')
REDIS_PORT = int(os.environ.get('REDIS_PORT', 6379))
SLACK_API_TOKEN = os.environ.get('SLACK_API_TOKEN')
SLACK_CHANNEL = os.environ.get('SLACK_CHANNEL', '#cyclebot')
STALE_PLAY_SECONDS = int(os.environ.get('STALE_PLAY_SECONDS', 900))


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
        self.game_key = None

        self.feed = None
        self.probable_pitchers = []
        self.plays = []
        self.inning_ordinal = None
        self.players = {}
        self.team = {}

        self.content = None
        self.highlights = {}
        self.content_key = None

    def poll(self):
        self.ingest_game_keys()

        for game_key in self.game_keys:
            self.game_key = game_key

            try:
                self.process_game()
            except:
                logger.exception(f'unable to process game {game_key}')

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

                home = game['teams']['home']['team']['name']
                away = game['teams']['away']['team']['name']
                # valid states: 'preview', 'live', 'final'
                state = game['status']['abstractGameState'].lower()
                detailed_state = game['status'].get('detailedState', '').lower()
                optional = ''

                if state == 'preview':
                    start = parser.parse(game['gameDate']).strftime('%H:%M')
                    optional = f' ({start}, {detailed_state})'
                else:
                    optional = f' ({detailed_state})'

                logger.info(f'{game_key}: {away} @ {home}, {state}{optional}')

                if state == 'live':
                    # TODO: ignore seriesDescription == 'Spring Training'
                    self.game_keys.add(game_key)

    def process_game(self):
        logger.info(f'processing game {self.game_key}')

        self.ingest_game_feed()
        self.ingest_game_content()

        for play in self.plays:
            try:
                self.process_play(play)
            except:
                start_time = play['about'].get('startTime')
                logger.exception(f'unable to process play @ {start_time}')

        for player_id in self.players:
            self.cycle_alert(player_id)

    def ingest_game_feed(self):
        logger.info(f'ingesting feed for game {self.game_key}')

        response = requests.get(f'{MLB_STATS_ORIGIN}/api/v1.1/game/{self.game_key}/feed/live')
        self.feed = response.json()

        self.probable_pitchers = [
            int(pitcher['id']) for pitcher in self.feed['gameData']['probablePitchers'].values()
        ]

        # plays are ordered least to most recent
        self.plays = self.feed['liveData']['plays']['allPlays']
        self.inning_ordinal = self.feed['liveData']['linescore'].get('currentInningOrdinal')

        self.players = {}
        for team in self.feed['liveData']['boxscore']['teams'].values():
            self.team = team['team']

            for player in team['players'].values():
                self.process_player(player)

    def process_player(self, player):
        player_id = int(player['person']['id'])
        self.players[player_id] = {
            'id': player_id,
            'name': player['person']['fullName'],
            'team_name': self.team['name'],
            'hits': player['stats']['batting'].get('hits', 0),
            'at_bats': player['stats']['batting'].get('atBats', 0),
            'hrs': player['seasonStats']['batting'].get('homeRuns', 0),
            'unique_hits': [],
        }

        if player_id in self.probable_pitchers:
            self.pitching_alerts(player)

    def pitching_alerts(self, player):
        player_name = player['person']['fullName']
        player_id = player['person']['id']
        team_name = self.team['name']

        hits = player['stats']['pitching'].get('hits', 0)
        runs = player['stats']['pitching'].get('runs', 0)
        pitches_thrown = player['stats']['pitching'].get('pitchesThrown', 0)
        innings_pitched = float(player['stats']['pitching'].get('inningsPitched', '0.0'))

        is_alertable = innings_pitched >= PITCHING_ALERT_INNINGS
        is_no_hitter = is_alertable and not hits
        is_cgso = is_alertable and not runs

        alert = None
        if is_no_hitter:
            alert = 'no-hitter'
            adjective = 'hitless'
        elif is_cgso:
            alert = 'cgso'
            adjective = 'scoreless'

        if alert:
            cache_key = self.make_key(self.game_key, player_id, alert, innings_pitched)
            is_cached = bool(self.redis.get(cache_key))

            if is_cached:
                logger.info(
                    f'ignoring cached {alert}: '
                    f'{player_name} ({team_name}) with {innings_pitched} {adjective} innings'
                )
                return

            logger.info(
                f'new {alert} alert: '
                f'{player_name} ({team_name}) with {innings_pitched} {adjective} innings'
            )

            self.redis.set(cache_key, 1, ex=REDIS_EXPIRE_SECONDS)
            self.post_slack_message(
                f'{alert.upper()} ALERT: '
                f'{player_name} ({team_name}) has thrown {pitches_thrown} pitches '
                f'over {innings_pitched} {adjective} innings'
            )

    def ingest_game_content(self):
        logger.info(f'ingesting content for game {self.game_key}')

        response = requests.get(f'{MLB_STATS_ORIGIN}/api/v1/game/{self.game_key}/content')
        self.content = response.json()

        self.highlights = {}
        for highlight in self.content['highlights']['live']['items']:
            self.highlights[int(highlight['id'])] = highlight

        pairs = [(self.now(), highlight_id) for highlight_id in self.highlights]
        self.content_key = self.make_key(self.game_key, 'content')

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

            is_hr = hit_code == 'HR'
            if is_hr:
                self.home_run_alert(play, batter)

            captivating_index = play['about'].get('captivatingIndex', 0)
            is_captivating = captivating_index >= MIN_CAPTIVATING_INDEX

            is_favorite = batter_id in FAVORITE_PLAYER_IDS

            if any([is_hr, is_captivating, is_favorite]):
                self.highlight_alert(play, batter, hit_code, captivating_index)

    def home_run_alert(self, play, batter):
        play_uuid = play['playEvents'][-1].get('playId')
        rbis = play['result']['rbi']

        if rbis == 1:
            alert = 'solo hr'
        elif rbis == 4:
            alert = 'grand slam hr'
        else:
            alert = f'{rbis}-run hr'

        batter_id = batter['id']
        batter_name = batter['name']
        batter_team = batter['team_name']
        hrs = batter['hrs']

        cache_key = self.make_key(play_uuid, batter_id)
        is_cached = bool(self.redis.get(cache_key))

        if is_cached:
            logger.info(f'ignoring cached {alert}: {play_uuid} {batter_name} ({batter_team})')
            return

        self.redis.set(cache_key, 1, ex=REDIS_EXPIRE_SECONDS)

        logger.info(f'new {alert} alert: {play_uuid} {batter_name} ({batter_team})')

        self.post_slack_message(
            f'{alert.upper()} ALERT: {batter_name}, {batter_team} ({hrs} HR)'
        )

    def highlight_alert(self, play, batter, hit_code, captivating_index):
        play_uuid = play['playEvents'][-1].get('playId')
        if not play_uuid:
            start_time = play['about'].get('startTime')
            logger.info(f'no uuid for play @ {start_time}')
            return

        batter_name = batter['name']
        batter_id = batter['id']

        play_end = int(parser.parse(play['about']['endTime']).timestamp())
        seconds_elapsed = self.now() - play_end
        is_stale = seconds_elapsed > STALE_PLAY_SECONDS

        if is_stale:
            logger.info(
                'ignoring stale highlight '
                f'{play_uuid} {batter_name} {hit_code} {captivating_index}, '
                f'{seconds_elapsed} seconds elapsed'
            )
            return

        play_key = self.make_key(play_uuid)
        is_cached = bool(self.redis.get(play_key))

        if is_cached:
            logger.info(
                'ignoring cached highlight '
                f'{play_uuid} {batter_name} {hit_code} {captivating_index}'
            )
            return

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
            highlight = self.highlights.get(int(highlight_id))
            if not highlight:
                logger.info(f'no ingested highlight with id {highlight_id}')
                continue

            for keyword in highlight['keywordsAll']:
                if keyword['type'] == 'sv_id':
                    highlights_by_sv_id[keyword['value']] = highlight

                if keyword['type'] == 'player_id':
                    highlights_by_player_id[int(keyword['value'])] = highlight

        # fall back to player_id when sv_id is missing
        highlight = highlights_by_sv_id.get(play_uuid) or highlights_by_player_id.get(batter_id)
        if highlight:
            logger.info(f'new highlight for play {play_uuid}')

            self.redis.set(play_key, 1, ex=REDIS_EXPIRE_SECONDS)

            for playback in highlight['playbacks']:
                playback_url = playback['url']
                if PLAYBACK_RESOLUTION in playback_url:
                    break

            description = highlight['description']

            self.post_slack_message(f'<{playback_url}|{description}>')
            self.post_reddit_link(description, playback_url)
        else:
            logger.info(f'highlight unavailable for play {play_uuid}')

    def cycle_alert(self, player_id):
        player = self.players[player_id]
        unique_hits = player['unique_hits']
        unique_hit_count = len(unique_hits)

        if unique_hit_count >= CYCLE_ALERT_HITS:
            name = player['name']
            team_name = player['team_name']
            joined_hits = ', '.join(unique_hits)

            cache_key = self.make_key(self.game_key, player_id, unique_hit_count)
            is_cached = bool(self.redis.get(cache_key))

            if is_cached:
                logger.info(f'ignoring cached cycle: {name} ({team_name}) with {joined_hits}')
                return

            logger.info(f'new cycle alert: {name} ({team_name}) with {joined_hits}')

            self.redis.set(cache_key, 1, ex=REDIS_EXPIRE_SECONDS)

            hits = player['hits']
            at_bats = player['at_bats']
            if unique_hit_count == len(HITS):
                self.post_slack_message(
                    f'CYCLE ALERT: {name} ({team_name}) {hits}-{at_bats} has hit for the cycle!'
                )
            else:
                # TODO: include data about how likely player is to get missing hit
                # (count of missing hit / plate apps)
                self.post_slack_message(
                    f'CYCLE ALERT: {name} ({team_name}) {hits}-{at_bats} '
                    f'with {joined_hits} in the {self.inning_ordinal} inning'
                )

    def now(self):
        return int(time.time())

    def make_key(self, *args):
        key = '-'.join([REDIS_KEY_VERSION] + [str(arg) for arg in args])
        return md5(key.encode('utf-8')).hexdigest()

    def post_slack_message(self, message, channel=SLACK_CHANNEL):
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


def poll():
    try:
        Cyclebot().poll()
    except:
        logger.exception('something went wrong')


def exception_handler(*args, **kwargs):
    # prevent invocation retry
    return True


if __name__ == '__main__':
    poll()
