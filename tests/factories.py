from uuid import uuid4


class Game:
    def __init__(self, id, state):
        self.id = id
        self.state = state

    def serialized(self):
        return {
            'gamePk': self.id,
            'gameDate': '2018-04-13T13:20:00Z',
            'status': {
                'abstractGameState': self.state,
                'detailedState': self.state,
            },
            'teams': {
                'away': {
                    'team': {
                        'name': 'New York Yankees',
                    },
                },
                'home': {
                    'team': {
                        'name': 'Boston Red Sox',
                    },
                },
            },
        }


class Schedule:
    def __init__(self, *game_data):
        self.game_data = game_data

    def serialized(self):
        if self.game_data:
            dates = [{
                'date': '2018-04-13',
                'games': [Game(*data).serialized() for data in self.game_data],
            }]
        else:
            dates = []

        return {
            'dates': dates,
        }


class Batting:
    def __init__(self, hits=0, at_bats=0, home_runs=0):
        self.hits = hits
        self.at_bats = at_bats
        self.home_runs = home_runs

    def serialized(self):
        return {
            'hits': self.hits,
            'atBats': self.at_bats,
            'homeRuns': self.home_runs,
        }


class Pitching:
    def __init__(self, hits=0, runs=0, pitches_thrown=0, innings_pitched='0.0'):
        self.hits = hits
        self.runs = runs
        self.pitches_thrown = pitches_thrown
        self.innings_pitched = innings_pitched

    def serialized(self):
        return {
            'hits': self.hits,
            'runs': self.runs,
            'pitchesThrown': self.pitches_thrown,
            'inningsPitched': self.innings_pitched,
        }


class Stats:
    def __init__(self, batting=None, pitching=None):
        self.batting = batting or Batting()
        self.pitching = pitching or Pitching()

    def serialized(self):
        return {
            'batting': self.batting.serialized(),
            'pitching': self.pitching.serialized(),
        }


class Player:
    def __init__(self, id, name, stats=None, season_stats=None):
        self.id = id
        self.name = name
        self.stats = stats or Stats()
        self.season_stats = season_stats or Stats()

    def serialized(self):
        return {
            'person': {
                'id': self.id,
                'fullName': self.name,
            },
            'stats': self.stats.serialized(),
            'seasonStats': self.season_stats.serialized(),
        }


class Play:
    def __init__(self, batter_id, id=None, event='Single', rbi=0, captivating_index=0):
        self.batter_id = batter_id
        self.id = id or str(uuid4())
        self.event = event
        self.rbi = rbi
        self.captivating_index = captivating_index

    def serialized(self):
        return {
            'result': {
                'event': self.event,
                'rbi': self.rbi,
            },
            'about': {
                'startTime': '2018-04-13T14:09:57.000Z',
                'endTime': '2018-04-13T14:11:10.000Z',
                'captivatingIndex': self.captivating_index,
            },
            'matchup': {
                'batter': {
                    'id': self.batter_id,
                },
            },
            'playEvents': [
                {
                    'playId': self.id,
                },
            ],
        }


class Feed:
    def __init__(self, away=None, home=None, plays=None, inning_ordinal=None):
        self.away = away or []
        self.home = home or []
        # plays are ordered least to most recent
        self.plays = plays or []
        self.inning_ordinal = inning_ordinal or '1st'

    def serialize_players(self, players):
        return {f'ID{player.id}': player.serialized() for player in players}

    def serialized(self):
        if self.away and self.home:
            # players listed first are assumed to be probable pitchers
            probable_pitchers = {
                'away': {
                    'id': self.away[0].id,
                },
                'home': {
                    'id': self.home[0].id,
                },
            }
        else:
            probable_pitchers = {}

        return {
            'gameData': {
                'probablePitchers': probable_pitchers,
            },
            'liveData': {
                'plays': {
                    'allPlays': [play.serialized() for play in self.plays],
                },
                'linescore': {
                    'currentInningOrdinal': self.inning_ordinal,
                },
                'boxscore': {
                    'teams': {
                        'away': {
                            'team': {
                                'name': 'New York Yankees',
                            },
                            'players': self.serialize_players(self.away),
                        },
                        'home': {
                            'team': {
                                'name': 'Boston Red Sox',
                            },
                            'players': self.serialize_players(self.home),
                        },
                    },
                },
            },
        }


class Content:
    def __init__(self, highlights=None):
        self.highlights = highlights or []

    def serialized(self):
        return {
            'highlights': {
                'live': {
                    # highlights are ordered most to least recent
                    'items': [highlight.serialized() for highlight in self.highlights],
                },
            },
        }


class Highlight:
    def __init__(self, id, player_id, sv_id=None):
        self.id = id
        self.player_id = player_id
        self.sv_id = sv_id

    def serialized(self):
        return {
            'id': self.id,
            'description': 'something happened',
            'keywordsAll': [
                {
                    'type': 'player_id',
                    'value': self.player_id,
                },
                {
                    'type': 'sv_id',
                    'value': self.sv_id,
                }
            ],
            'playbacks': [
                {
                    'url': f'https://www.example.com/{self.id}/1800K.mp4',
                },
                {
                    'url': f'https://www.example.com/{self.id}/2500K.mp4',
                },
            ]
        }
