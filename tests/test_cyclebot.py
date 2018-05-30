import urllib.parse
from unittest.mock import patch

import pytest
import responses
from freezegun import freeze_time
from redis import StrictRedis

from cyclebot import poll
from tests.factories import (
    Batting,
    Content,
    Feed,
    Highlight,
    Pitching,
    Play,
    Player,
    Schedule,
    Stats,
)


@freeze_time('2018-04-13 14:15:00')
@patch('cyclebot.CYCLE_ALERT_HITS', 3)
@patch('cyclebot.MIN_CAPTIVATING_INDEX', 75)
@patch('cyclebot.PITCHING_ALERT_INNINGS', 7)
@patch('cyclebot.SLACK_API_TOKEN', 'fake-token')
@patch('cyclebot.STALE_PLAY_SECONDS', 1800)
class TestCyclebot:
    game_key = 123456
    yesterday_url = 'https://statsapi.mlb.com/api/v1/schedule?sportId=1&date=2018-04-12'
    today_url = 'https://statsapi.mlb.com/api/v1/schedule?sportId=1&date=2018-04-13'
    feed_url = f'https://statsapi.mlb.com/api/v1.1/game/{game_key}/feed/live'
    content_url = f'https://statsapi.mlb.com/api/v1/game/{game_key}/content'
    slack_url = 'https://slack.com/api/chat.postMessage'

    @pytest.fixture(autouse=True)
    def flush_redis(self):
        StrictRedis().flushall()

    @pytest.fixture(autouse=True)
    def add_empty_yesterday(self):
        responses.add(
            responses.GET,
            self.yesterday_url,
            match_querystring=True,
            json=Schedule().serialized(),
        )

    def assert_calls(self, *urls):
        assert len(responses.calls) == len(urls)

        for index, url in enumerate(urls):
            assert responses.calls[index].request.url == url

    def reset_calls(self):
        responses.calls.reset()

    def mock_slack(self):
        responses.add(
            responses.POST,
            self.slack_url,
            json={'ok': True},
        )

    def assert_slack(self, call, message):
        body = dict(urllib.parse.parse_qsl(call.request.body))
        assert body['channel'] == '#cyclebot'
        assert body['text'] == message

    @responses.activate
    def test_ingest_preview(self):
        schedule = Schedule([self.game_key, 'preview'])
        responses.add(
            responses.GET,
            self.today_url,
            match_querystring=True,
            json=schedule.serialized(),
        )

        poll()

        self.assert_calls(self.yesterday_url, self.today_url)

    @responses.activate
    def test_ingest_live(self):
        schedule = Schedule([self.game_key, 'live'])
        responses.add(
            responses.GET,
            self.today_url,
            match_querystring=True,
            json=schedule.serialized(),
        )

        feed = Feed()
        responses.add(
            responses.GET,
            self.feed_url,
            json=feed.serialized(),
        )

        content = Content()
        responses.add(
            responses.GET,
            self.content_url,
            json=content.serialized(),
        )

        poll()

        self.assert_calls(self.yesterday_url, self.today_url, self.feed_url, self.content_url)

    @responses.activate
    def test_ingest_final(self):
        schedule = Schedule([self.game_key, 'final'])
        responses.add(
            responses.GET,
            self.today_url,
            match_querystring=True,
            json=schedule.serialized(),
        )

        poll()

        self.assert_calls(self.yesterday_url, self.today_url)

    @responses.activate
    def test_pitching_alerts(self):
        schedule = Schedule([self.game_key, 'live'])
        responses.add(
            responses.GET,
            self.today_url,
            match_querystring=True,
            json=schedule.serialized(),
        )

        pitching = Pitching()
        stats = Stats(pitching=pitching)

        alice = Player(101010, 'alice', stats=stats)
        bob = Player(202020, 'bob')

        feed = Feed(away=[alice], home=[bob])
        responses.add(
            responses.GET,
            self.feed_url,
            json=feed.serialized(),
        )

        content = Content()
        responses.add(
            responses.GET,
            self.content_url,
            json=content.serialized(),
        )

        poll()

        self.assert_calls(self.yesterday_url, self.today_url, self.feed_url, self.content_url)
        self.reset_calls()

        pitching.pitches_thrown = 86
        pitching.innings_pitched = '7.0'
        responses.replace(
            responses.GET,
            self.feed_url,
            json=feed.serialized(),
        )

        self.mock_slack()

        poll()

        # warm up cache
        self.assert_calls(self.yesterday_url, self.today_url, self.feed_url, self.slack_url, self.content_url)
        self.assert_slack(
            responses.calls[3],
            'NO-HITTER ALERT: alice (New York Yankees) '
            'has thrown 86 pitches over 7.0 hitless innings'
        )
        self.reset_calls()

        poll()

        # hit cache
        self.assert_calls(self.yesterday_url, self.today_url, self.feed_url, self.content_url)
        self.reset_calls()

        pitching.pitches_thrown = 87
        responses.replace(
            responses.GET,
            self.feed_url,
            json=feed.serialized(),
        )

        poll()

        # hit cache
        self.assert_calls(self.yesterday_url, self.today_url, self.feed_url, self.content_url)
        self.reset_calls()

        pitching.pitches_thrown = 88
        pitching.innings_pitched = '7.1'
        responses.replace(
            responses.GET,
            self.feed_url,
            json=feed.serialized(),
        )

        poll()

        self.assert_calls(self.yesterday_url, self.today_url, self.feed_url, self.slack_url, self.content_url)
        self.assert_slack(
            responses.calls[3],
            'NO-HITTER ALERT: alice (New York Yankees) '
            'has thrown 88 pitches over 7.1 hitless innings'
        )
        self.reset_calls()

        pitching.hits = 1
        pitching.pitches_thrown = 90
        responses.replace(
            responses.GET,
            self.feed_url,
            json=feed.serialized(),
        )

        poll()

        # warm up cache
        self.assert_calls(self.yesterday_url, self.today_url, self.feed_url, self.slack_url, self.content_url)
        self.assert_slack(
            responses.calls[3],
            'CGSO ALERT: alice (New York Yankees) '
            'has thrown 90 pitches over 7.1 scoreless innings'
        )
        self.reset_calls()

        poll()

        # hit cache
        self.assert_calls(self.yesterday_url, self.today_url, self.feed_url, self.content_url)
        self.reset_calls()

        pitching.pitches_thrown = 92
        pitching.innings_pitched = '7.2'
        responses.replace(
            responses.GET,
            self.feed_url,
            json=feed.serialized(),
        )

        poll()

        self.assert_calls(self.yesterday_url, self.today_url, self.feed_url, self.slack_url, self.content_url)
        self.assert_slack(
            responses.calls[3],
            'CGSO ALERT: alice (New York Yankees) '
            'has thrown 92 pitches over 7.2 scoreless innings'
        )
        self.reset_calls()

        pitching.hits = 2
        pitching.runs = 1
        pitching.pitches_thrown = 95
        responses.replace(
            responses.GET,
            self.feed_url,
            json=feed.serialized(),
        )

        poll()

        self.assert_calls(self.yesterday_url, self.today_url, self.feed_url, self.content_url)
        self.reset_calls()

        pitching.innings_pitched = '8.0'
        responses.replace(
            responses.GET,
            self.feed_url,
            json=feed.serialized(),
        )

        poll()

        self.assert_calls(self.yesterday_url, self.today_url, self.feed_url, self.content_url)

    @responses.activate
    def test_home_run_alert(self):
        schedule = Schedule([self.game_key, 'live'])
        responses.add(
            responses.GET,
            self.today_url,
            match_querystring=True,
            json=schedule.serialized(),
        )

        season_batting = Batting(home_runs=18)
        season_stats = Stats(batting=season_batting)

        alice = Player(101010, 'alice')
        bob = Player(202020, 'bob', season_stats=season_stats)

        plays = []
        solo_hr = Play(bob.id, event='Home Run', rbi=1)
        two_run_hr = Play(bob.id, event='Home Run', rbi=2)
        three_run_hr = Play(bob.id, event='Home Run', rbi=3)
        grand_slam = Play(bob.id, event='Home Run', rbi=4)

        plays.append(solo_hr)

        feed = Feed(away=[alice, bob], home=[alice], plays=plays)
        responses.add(
            responses.GET,
            self.feed_url,
            json=feed.serialized(),
        )

        content = Content()
        responses.add(
            responses.GET,
            self.content_url,
            json=content.serialized(),
        )

        self.mock_slack()

        poll()

        self.assert_calls(self.yesterday_url, self.today_url, self.feed_url, self.content_url, self.slack_url)
        self.assert_slack(responses.calls[4], 'SOLO HR ALERT: bob, New York Yankees (18 HR)')
        self.reset_calls()

        plays.append(two_run_hr)

        responses.replace(
            responses.GET,
            self.feed_url,
            json=feed.serialized(),
        )

        poll()

        self.assert_calls(self.yesterday_url, self.today_url, self.feed_url, self.content_url, self.slack_url)
        self.assert_slack(responses.calls[4], '2-RUN HR ALERT: bob, New York Yankees (18 HR)')
        self.reset_calls()

        plays.append(three_run_hr)

        responses.replace(
            responses.GET,
            self.feed_url,
            json=feed.serialized(),
        )

        poll()

        self.assert_calls(self.yesterday_url, self.today_url, self.feed_url, self.content_url, self.slack_url)
        self.assert_slack(responses.calls[4], '3-RUN HR ALERT: bob, New York Yankees (18 HR)')
        self.reset_calls()

        plays.append(grand_slam)

        responses.replace(
            responses.GET,
            self.feed_url,
            json=feed.serialized(),
        )

        poll()

        self.assert_calls(self.yesterday_url, self.today_url, self.feed_url, self.content_url, self.slack_url)
        self.assert_slack(responses.calls[4], 'GRAND SLAM HR ALERT: bob, New York Yankees (18 HR)')

    @responses.activate
    def test_highlight_alert(self):
        schedule = Schedule([self.game_key, 'live'])
        responses.add(
            responses.GET,
            self.today_url,
            match_querystring=True,
            json=schedule.serialized(),
        )

        season_batting = Batting(home_runs=14)
        season_stats = Stats(batting=season_batting)

        alice = Player(101010, 'alice')
        bob = Player(202020, 'bob', season_stats=season_stats)

        home_run = Play(bob.id, event='Home Run', rbi=1)

        feed = Feed(away=[alice, bob], home=[alice], plays=[home_run])
        responses.add(
            responses.GET,
            self.feed_url,
            json=feed.serialized(),
        )

        not_bob_id = bob.id - 1
        sv_id_highlight = Highlight('123', not_bob_id, sv_id=home_run.id)

        content = Content(highlights=[sv_id_highlight])
        responses.add(
            responses.GET,
            self.content_url,
            json=content.serialized(),
        )

        self.mock_slack()

        poll()

        # warm up cache
        self.assert_calls(
            self.yesterday_url,
            self.today_url,
            self.feed_url,
            self.content_url,
            self.slack_url,
            self.slack_url,
        )
        self.assert_slack(responses.calls[4], 'SOLO HR ALERT: bob, New York Yankees (14 HR)')
        self.assert_slack(responses.calls[5], '<https://www.example.com/123/2500K.mp4|something happened>')
        self.reset_calls()

        poll()

        # hit cache
        self.assert_calls(self.yesterday_url, self.today_url, self.feed_url, self.content_url)
        self.reset_calls()

        self.flush_redis()

        player_id_highlight = Highlight('123', bob.id)

        content = Content(highlights=[player_id_highlight])
        responses.replace(
            responses.GET,
            self.content_url,
            json=content.serialized(),
        )

        poll()

        # warm up cache
        self.assert_calls(
            self.yesterday_url,
            self.today_url,
            self.feed_url,
            self.content_url,
            self.slack_url,
            self.slack_url,
        )
        self.assert_slack(responses.calls[4], 'SOLO HR ALERT: bob, New York Yankees (14 HR)')
        self.assert_slack(responses.calls[5], '<https://www.example.com/123/2500K.mp4|something happened>')
        self.reset_calls()

        poll()

        # hit cache
        self.assert_calls(self.yesterday_url, self.today_url, self.feed_url, self.content_url)
        self.reset_calls()

        self.flush_redis()

        # make sure STALE_PLAY_SECONDS is respected
        with freeze_time('2018-04-13 15:15:00'):
            poll()

        self.assert_calls(
            self.yesterday_url,
            self.today_url,
            self.feed_url,
            self.content_url,
            self.slack_url,
        )
        self.assert_slack(responses.calls[4], 'SOLO HR ALERT: bob, New York Yankees (14 HR)')
        self.reset_calls()

        boring_play = Play(bob.id, captivating_index=10)
        exciting_play = Play(bob.id, captivating_index=90)

        feed = Feed(away=[alice, bob], home=[alice], plays=[boring_play, exciting_play])
        responses.replace(
            responses.GET,
            self.feed_url,
            json=feed.serialized(),
        )

        boring_highlight = Highlight('123', bob.id, sv_id=boring_play.id)
        exciting_highlight = Highlight('456', bob.id, sv_id=exciting_play.id)

        content = Content(highlights=[boring_highlight, exciting_highlight])
        responses.replace(
            responses.GET,
            self.content_url,
            json=content.serialized(),
        )

        poll()

        self.assert_calls(self.yesterday_url, self.today_url, self.feed_url, self.content_url, self.slack_url)
        self.assert_slack(responses.calls[4], '<https://www.example.com/456/2500K.mp4|something happened>')
        self.reset_calls()

        content = Content(highlights=[boring_highlight])
        responses.replace(
            responses.GET,
            self.content_url,
            json=content.serialized(),
        )

        with patch('cyclebot.FAVORITE_PLAYER_IDS', [bob.id]):
            poll()

        self.assert_calls(self.yesterday_url, self.today_url, self.feed_url, self.content_url, self.slack_url)
        self.assert_slack(responses.calls[4], '<https://www.example.com/123/2500K.mp4|something happened>')

    @responses.activate
    def test_cycle_alert(self):
        schedule = Schedule([self.game_key, 'live'])
        responses.add(
            responses.GET,
            self.today_url,
            match_querystring=True,
            json=schedule.serialized(),
        )

        batting = Batting()
        stats = Stats(batting=batting)

        season_batting = Batting(home_runs=18)
        season_stats = Stats(batting=season_batting)

        alice = Player(101010, 'alice')
        bob = Player(202020, 'bob', stats=stats, season_stats=season_stats)

        plays = []
        single = Play(bob.id, event='Single')
        double = Play(bob.id, event='Double')
        triple = Play(bob.id, event='Triple')
        home_run = Play(bob.id, event='Home Run', rbi=1)
        strikeout = Play(bob.id, event='Strikeout')

        plays.append(single)

        feed = Feed(away=[alice, bob], home=[alice], plays=plays)
        responses.add(
            responses.GET,
            self.feed_url,
            json=feed.serialized(),
        )

        content = Content()
        responses.add(
            responses.GET,
            self.content_url,
            json=content.serialized(),
        )

        poll()

        self.assert_calls(self.yesterday_url, self.today_url, self.feed_url, self.content_url)
        self.reset_calls()

        plays.append(double)

        responses.replace(
            responses.GET,
            self.feed_url,
            json=feed.serialized(),
        )

        poll()

        self.assert_calls(self.yesterday_url, self.today_url, self.feed_url, self.content_url)
        self.reset_calls()

        plays.append(strikeout)

        responses.replace(
            responses.GET,
            self.feed_url,
            json=feed.serialized(),
        )

        poll()

        self.assert_calls(self.yesterday_url, self.today_url, self.feed_url, self.content_url)
        self.reset_calls()

        plays.append(double)

        responses.replace(
            responses.GET,
            self.feed_url,
            json=feed.serialized(),
        )

        poll()

        self.assert_calls(self.yesterday_url, self.today_url, self.feed_url, self.content_url)
        self.reset_calls()

        plays.append(triple)
        batting.hits = 4
        batting.at_bats = 5
        feed.inning_ordinal = '7th'

        responses.replace(
            responses.GET,
            self.feed_url,
            json=feed.serialized(),
        )

        self.mock_slack()

        poll()

        # warm up cache
        self.assert_calls(self.yesterday_url, self.today_url, self.feed_url, self.content_url, self.slack_url)
        self.assert_slack(
            responses.calls[4],
            'CYCLE ALERT: bob (New York Yankees) '
            '4-5 with 1B, 2B, 3B in the 7th inning'
        )
        self.reset_calls()

        poll()

        # hit cache
        self.assert_calls(self.yesterday_url, self.today_url, self.feed_url, self.content_url)
        self.reset_calls()

        plays.append(home_run)
        batting.hits = 5
        batting.at_bats = 6
        feed.inning_ordinal = '8th'

        responses.replace(
            responses.GET,
            self.feed_url,
            json=feed.serialized(),
        )

        poll()

        self.assert_calls(
            self.yesterday_url,
            self.today_url,
            self.feed_url,
            self.content_url,
            self.slack_url,
            self.slack_url,
        )
        self.assert_slack(responses.calls[4], 'SOLO HR ALERT: bob, New York Yankees (18 HR)')
        self.assert_slack(
            responses.calls[5],
            'CYCLE ALERT: bob (New York Yankees) '
            '5-6 has hit for the cycle!'
        )
