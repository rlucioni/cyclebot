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


SLACK_URL = 'https://slack.com/api/chat.postMessage'


@pytest.fixture(autouse=True)
def flush_redis():
    StrictRedis().flushall()


def assert_calls(*urls):
    assert len(responses.calls) == len(urls)

    for index, url in enumerate(urls):
        assert responses.calls[index].request.url == url


def mock_slack():
    responses.add(
        responses.POST,
        SLACK_URL,
        json={'ok': True},
    )


def assert_slack(call, message):
    body = dict(urllib.parse.parse_qsl(call.request.body))
    assert body['channel'] == '#cyclebot'
    assert body['text'] == message


@responses.activate
@freeze_time('2018-04-13 14:15:00')
@patch('cyclebot.SLACK_API_TOKEN', 'fake-token')
def test_ingest():
    yesterday_url = 'https://statsapi.mlb.com/api/v1/schedule?sportId=1&date=2018-04-12'
    empty = Schedule()
    responses.add(
        responses.GET,
        yesterday_url,
        match_querystring=True,
        json=empty.serialized(),
    )

    game_key = 123456

    today_url = 'https://statsapi.mlb.com/api/v1/schedule?sportId=1&date=2018-04-13'
    schedule = Schedule([game_key, 'preview'])
    responses.add(
        responses.GET,
        today_url,
        match_querystring=True,
        json=schedule.serialized(),
    )

    poll()

    assert_calls(yesterday_url, today_url)

    responses.calls.reset()

    schedule = Schedule([game_key, 'live'])
    responses.replace(
        responses.GET,
        today_url,
        match_querystring=True,
        json=schedule.serialized(),
    )

    feed_url = f'https://statsapi.mlb.com/api/v1.1/game/{game_key}/feed/live'
    feed = Feed()
    responses.add(
        responses.GET,
        feed_url,
        json=feed.serialized(),
    )

    content_url = f'https://statsapi.mlb.com/api/v1/game/{game_key}/content'
    content = Content()
    responses.add(
        responses.GET,
        content_url,
        json=content.serialized(),
    )

    poll()

    assert_calls(yesterday_url, today_url, feed_url, content_url)

    responses.calls.reset()

    schedule = Schedule([game_key, 'final'])
    responses.replace(
        responses.GET,
        today_url,
        match_querystring=True,
        json=schedule.serialized(),
    )

    poll()

    assert_calls(yesterday_url, today_url)


@responses.activate
@freeze_time('2018-04-13 14:15:00')
@patch('cyclebot.PITCHING_ALERT_INNINGS', 7)
@patch('cyclebot.SLACK_API_TOKEN', 'fake-token')
def test_pitching_alerts():
    yesterday_url = 'https://statsapi.mlb.com/api/v1/schedule?sportId=1&date=2018-04-12'
    empty = Schedule()
    responses.add(
        responses.GET,
        yesterday_url,
        match_querystring=True,
        json=empty.serialized(),
    )

    game_key = 123456

    today_url = 'https://statsapi.mlb.com/api/v1/schedule?sportId=1&date=2018-04-13'
    schedule = Schedule([game_key, 'live'])
    responses.add(
        responses.GET,
        today_url,
        match_querystring=True,
        json=schedule.serialized(),
    )

    pitching = Pitching()
    stats = Stats(pitching=pitching)

    alice = Player(101010, 'alice', stats=stats)
    bob = Player(202020, 'bob')

    feed_url = f'https://statsapi.mlb.com/api/v1.1/game/{game_key}/feed/live'
    feed = Feed(away=[alice], home=[bob])
    responses.add(
        responses.GET,
        feed_url,
        json=feed.serialized(),
    )

    content_url = f'https://statsapi.mlb.com/api/v1/game/{game_key}/content'
    content = Content()
    responses.add(
        responses.GET,
        content_url,
        json=content.serialized(),
    )

    poll()

    assert_calls(yesterday_url, today_url, feed_url, content_url)

    responses.calls.reset()

    pitching.pitches_thrown = 86
    pitching.innings_pitched = '7.0'
    responses.replace(
        responses.GET,
        feed_url,
        json=feed.serialized(),
    )

    mock_slack()

    poll()

    # warm up cache
    assert_calls(yesterday_url, today_url, feed_url, SLACK_URL, content_url)
    assert_slack(
        responses.calls[3],
        'NO-HITTER ALERT: alice (New York Yankees) '
        'has thrown 86 pitches over 7.0 hitless innings'
    )

    responses.calls.reset()

    poll()

    # hit cache
    assert_calls(yesterday_url, today_url, feed_url, content_url)

    responses.calls.reset()

    pitching.pitches_thrown = 87
    responses.replace(
        responses.GET,
        feed_url,
        json=feed.serialized(),
    )

    poll()

    # hit cache
    assert_calls(yesterday_url, today_url, feed_url, content_url)

    responses.calls.reset()

    pitching.pitches_thrown = 88
    pitching.innings_pitched = '7.1'
    responses.replace(
        responses.GET,
        feed_url,
        json=feed.serialized(),
    )

    poll()

    assert_calls(yesterday_url, today_url, feed_url, SLACK_URL, content_url)
    assert_slack(
        responses.calls[3],
        'NO-HITTER ALERT: alice (New York Yankees) '
        'has thrown 88 pitches over 7.1 hitless innings'
    )

    responses.calls.reset()

    pitching.hits = 1
    pitching.pitches_thrown = 90
    responses.replace(
        responses.GET,
        feed_url,
        json=feed.serialized(),
    )

    poll()

    # warm up cache
    assert_calls(yesterday_url, today_url, feed_url, SLACK_URL, content_url)
    assert_slack(
        responses.calls[3],
        'CGSO ALERT: alice (New York Yankees) '
        'has thrown 90 pitches over 7.1 scoreless innings'
    )

    responses.calls.reset()

    poll()

    # hit cache
    assert_calls(yesterday_url, today_url, feed_url, content_url)

    responses.calls.reset()

    pitching.pitches_thrown = 92
    pitching.innings_pitched = '7.2'
    responses.replace(
        responses.GET,
        feed_url,
        json=feed.serialized(),
    )

    poll()

    assert_calls(yesterday_url, today_url, feed_url, SLACK_URL, content_url)
    assert_slack(
        responses.calls[3],
        'CGSO ALERT: alice (New York Yankees) '
        'has thrown 92 pitches over 7.2 scoreless innings'
    )

    responses.calls.reset()

    pitching.hits = 2
    pitching.runs = 1
    pitching.pitches_thrown = 95
    responses.replace(
        responses.GET,
        feed_url,
        json=feed.serialized(),
    )

    poll()

    assert_calls(yesterday_url, today_url, feed_url, content_url)

    responses.calls.reset()

    pitching.innings_pitched = '8.0'
    responses.replace(
        responses.GET,
        feed_url,
        json=feed.serialized(),
    )

    poll()

    assert_calls(yesterday_url, today_url, feed_url, content_url)


@responses.activate
@freeze_time('2018-04-13 14:15:00')
@patch('cyclebot.MIN_CAPTIVATING_INDEX', 75)
@patch('cyclebot.SLACK_API_TOKEN', 'fake-token')
@patch('cyclebot.STALE_PLAY_SECONDS', 1800)
def test_highlight_alert():
    yesterday_url = 'https://statsapi.mlb.com/api/v1/schedule?sportId=1&date=2018-04-12'
    empty = Schedule()
    responses.add(
        responses.GET,
        yesterday_url,
        match_querystring=True,
        json=empty.serialized(),
    )

    game_key = 123456

    today_url = 'https://statsapi.mlb.com/api/v1/schedule?sportId=1&date=2018-04-13'
    schedule = Schedule([game_key, 'live'])
    responses.add(
        responses.GET,
        today_url,
        match_querystring=True,
        json=schedule.serialized(),
    )

    season_batting = Batting(home_runs=14)
    season_stats = Stats(batting=season_batting)

    alice = Player(101010, 'alice')
    bob = Player(202020, 'bob', season_stats=season_stats)

    home_run = Play(bob.id, event='Home Run')

    feed_url = f'https://statsapi.mlb.com/api/v1.1/game/{game_key}/feed/live'
    feed = Feed(away=[alice, bob], home=[alice], plays=[home_run])
    responses.add(
        responses.GET,
        feed_url,
        json=feed.serialized(),
    )

    not_bob_id = bob.id - 1
    sv_id_highlight = Highlight('123', not_bob_id, sv_id=home_run.id)

    content_url = f'https://statsapi.mlb.com/api/v1/game/{game_key}/content'
    content = Content(highlights=[sv_id_highlight])
    responses.add(
        responses.GET,
        content_url,
        json=content.serialized(),
    )

    mock_slack()

    poll()

    # warm up cache
    assert_calls(yesterday_url, today_url, feed_url, content_url, SLACK_URL)
    assert_slack(responses.calls[4], '<https://www.example.com/123/2500K.mp4|something happened> (14 HR)')

    responses.calls.reset()

    poll()

    # hit cache
    assert_calls(yesterday_url, today_url, feed_url, content_url)

    responses.calls.reset()
    flush_redis()

    player_id_highlight = Highlight('123', bob.id)

    content = Content(highlights=[player_id_highlight])
    responses.replace(
        responses.GET,
        content_url,
        json=content.serialized(),
    )

    poll()

    # warm up cache
    assert_calls(yesterday_url, today_url, feed_url, content_url, SLACK_URL)
    assert_slack(responses.calls[4], '<https://www.example.com/123/2500K.mp4|something happened> (14 HR)')

    responses.calls.reset()

    poll()

    # hit cache
    assert_calls(yesterday_url, today_url, feed_url, content_url)

    responses.calls.reset()
    flush_redis()

    # make sure STALE_PLAY_SECONDS is respected
    with freeze_time('2018-04-13 15:15:00'):
        poll()

    assert_calls(yesterday_url, today_url, feed_url, content_url)

    responses.calls.reset()

    # TODO: test alerts for captivating index and favorite player
    boring_play = Play(bob.id, captivating_index=10)
    exciting_play = Play(bob.id, captivating_index=90)

    feed = Feed(away=[alice, bob], home=[alice], plays=[boring_play, exciting_play])
    responses.replace(
        responses.GET,
        feed_url,
        json=feed.serialized(),
    )

    boring_highlight = Highlight('123', bob.id, sv_id=boring_play.id)
    exciting_highlight = Highlight('456', bob.id, sv_id=exciting_play.id)

    content = Content(highlights=[boring_highlight, exciting_highlight])
    responses.replace(
        responses.GET,
        content_url,
        json=content.serialized(),
    )

    poll()

    assert_calls(yesterday_url, today_url, feed_url, content_url, SLACK_URL)
    assert_slack(responses.calls[4], '<https://www.example.com/456/2500K.mp4|something happened>')

    responses.calls.reset()

    content = Content(highlights=[boring_highlight])
    responses.replace(
        responses.GET,
        content_url,
        json=content.serialized(),
    )

    with patch('cyclebot.FAVORITE_PLAYER_IDS', [bob.id]):
        poll()

    assert_calls(yesterday_url, today_url, feed_url, content_url, SLACK_URL)
    assert_slack(responses.calls[4], '<https://www.example.com/123/2500K.mp4|something happened>')


@responses.activate
@freeze_time('2018-04-13 14:15:00')
@patch('cyclebot.CYCLE_ALERT_HITS', 3)
@patch('cyclebot.SLACK_API_TOKEN', 'fake-token')
def test_cycle_alert():
    yesterday_url = 'https://statsapi.mlb.com/api/v1/schedule?sportId=1&date=2018-04-12'
    empty = Schedule()
    responses.add(
        responses.GET,
        yesterday_url,
        match_querystring=True,
        json=empty.serialized(),
    )

    game_key = 123456

    today_url = 'https://statsapi.mlb.com/api/v1/schedule?sportId=1&date=2018-04-13'
    schedule = Schedule([game_key, 'live'])
    responses.add(
        responses.GET,
        today_url,
        match_querystring=True,
        json=schedule.serialized(),
    )

    batting = Batting()
    stats = Stats(batting=batting)

    alice = Player(101010, 'alice')
    bob = Player(202020, 'bob', stats=stats)

    plays = []
    single = Play(bob.id, event='Single')
    double = Play(bob.id, event='Double')
    triple = Play(bob.id, event='Triple')
    home_run = Play(bob.id, event='Home Run')
    strikeout = Play(bob.id, event='Strikeout')

    plays.append(single)

    feed_url = f'https://statsapi.mlb.com/api/v1.1/game/{game_key}/feed/live'
    feed = Feed(away=[alice, bob], home=[alice], plays=plays)
    responses.add(
        responses.GET,
        feed_url,
        json=feed.serialized(),
    )

    content_url = f'https://statsapi.mlb.com/api/v1/game/{game_key}/content'
    content = Content()
    responses.add(
        responses.GET,
        content_url,
        json=content.serialized(),
    )

    poll()

    assert_calls(yesterday_url, today_url, feed_url, content_url)

    responses.calls.reset()

    plays.append(double)

    responses.replace(
        responses.GET,
        feed_url,
        json=feed.serialized(),
    )

    poll()

    assert_calls(yesterday_url, today_url, feed_url, content_url)

    responses.calls.reset()

    plays.append(strikeout)

    responses.replace(
        responses.GET,
        feed_url,
        json=feed.serialized(),
    )

    poll()

    assert_calls(yesterday_url, today_url, feed_url, content_url)

    responses.calls.reset()

    plays.append(double)

    responses.replace(
        responses.GET,
        feed_url,
        json=feed.serialized(),
    )

    poll()

    assert_calls(yesterday_url, today_url, feed_url, content_url)

    responses.calls.reset()

    plays.append(triple)
    batting.hits = 4
    batting.at_bats = 5
    feed.inning_ordinal = '7th'

    responses.replace(
        responses.GET,
        feed_url,
        json=feed.serialized(),
    )

    mock_slack()

    poll()

    # warm up cache
    assert_calls(yesterday_url, today_url, feed_url, content_url, SLACK_URL)
    assert_slack(
        responses.calls[4],
        'CYCLE ALERT: bob (New York Yankees) '
        '4-5 with 1B, 2B, 3B in the 7th inning'
    )

    responses.calls.reset()

    poll()

    # hit cache
    assert len(responses.calls) == 4
    assert_calls(yesterday_url, today_url, feed_url, content_url)

    responses.calls.reset()

    plays.append(home_run)
    batting.hits = 5
    batting.at_bats = 6
    feed.inning_ordinal = '8th'

    responses.replace(
        responses.GET,
        feed_url,
        json=feed.serialized(),
    )

    poll()

    assert_calls(yesterday_url, today_url, feed_url, content_url, SLACK_URL)
    assert_slack(
        responses.calls[4],
        'CYCLE ALERT: bob (New York Yankees) '
        '5-6 has hit for the cycle!'
    )
