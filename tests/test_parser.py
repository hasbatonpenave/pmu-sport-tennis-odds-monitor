from feed.parser import (
    _odds_int_to_float,
    _parse_score,
    _extract_path,
    parse_match_list,
    parse_live_events,
    parse_betoffers,
    diff_odds,
)


class TestOddsConversion:
    def test_integer_to_float(self):
        assert _odds_int_to_float(6100) == 6.1
        assert _odds_int_to_float(1060) == 1.06
        assert _odds_int_to_float(2020) == 2.02
        assert _odds_int_to_float(1700) == 1.7

    def test_underdog(self):
        assert _odds_int_to_float(81000) == 81.0


class TestScoreParsing:
    def test_live_tennis_score(self):
        live_data = {
            "score": {"home": "15", "away": "30", "who": "UNKNOWN", "version": 1700000000000},
            "statistics": {
                "sets": {"home": [6, 4, -1], "away": [4, 6, -1], "homeServe": True},
                "version": 1700000000000,
            },
        }
        score = _parse_score(live_data)
        assert score is not None
        assert score.home == "15"
        assert score.away == "30"
        assert score.sets_home == [6, 4, -1]
        assert score.sets_away == [4, 6, -1]
        assert score.home_serve is True

    def test_empty_live_data(self):
        assert _parse_score({}) is None
        assert _parse_score(None) is None


class TestPathExtraction:
    def test_extract_path(self):
        event = {
            "path": [
                {"id": 1, "name": "Tennis", "englishName": "Tennis", "termKey": "tennis"},
                {"id": 2, "name": "ATP", "englishName": "ATP", "termKey": "atp"},
            ]
        }
        result = _extract_path(event)
        assert len(result) == 2
        assert result[0]["termKey"] == "tennis"


class TestParseMatchList:
    def test_parse_empty(self):
        meta, prices = parse_match_list({"events": []}, sport_name="TENNIS", tracked_markets=["Match Odds"])
        assert meta == {}
        assert prices == {}

    def test_parse_not_started_match(self):
        data = {
            "events": [{
                "event": {
                    "id": 12345,
                    "name": "Federer - Nadal",
                    "homeName": "Federer",
                    "awayName": "Nadal",
                    "group": "ATP Finals",
                    "start": "2026-05-07T14:00:00Z",
                    "state": "NOT_STARTED",
                    "sport": "TENNIS",
                    "tags": ["MATCH"],
                    "path": [{"id": 1, "name": "Tennis", "englishName": "Tennis", "termKey": "tennis"}],
                },
                "betOffers": [{
                    "id": 100,
                    "criterion": {"id": 1, "englishLabel": "Match Odds", "label": "Vainqueur du match"},
                    "betOfferType": {"id": 2, "name": "Match", "englishName": "Match"},
                    "outcomes": [
                        {"id": 1, "englishLabel": "Federer", "odds": 1850, "status": "OPEN", "type": "OT_ONE"},
                        {"id": 2, "englishLabel": "Nadal", "odds": 2100, "status": "OPEN", "type": "OT_TWO"},
                    ],
                    "tags": ["MAIN"],
                }],
            }]
        }
        meta, prices = parse_match_list(data, sport_name="TENNIS", tracked_markets=["Match Odds"])
        assert 12345 in meta
        assert meta[12345].player_a == "Federer"
        assert meta[12345].state == "NOT_STARTED"
        assert meta[12345].sport == "TENNIS"
        assert "Match Odds" in prices[12345]
        assert prices[12345]["Match Odds"]["Federer"] == 1.85
        assert prices[12345]["Match Odds"]["Nadal"] == 2.10


class TestParseLiveEvents:
    def test_parse_live_match(self):
        data = {
            "liveEvents": [{
                "event": {
                    "id": 99999,
                    "name": "Djokovic - Alcaraz",
                    "homeName": "Djokovic",
                    "awayName": "Alcaraz",
                    "group": "ATP Rome",
                    "start": "2026-05-07T12:00:00Z",
                    "state": "STARTED",
                    "sport": "TENNIS",
                    "tags": ["MATCH", "OPEN_FOR_LIVE"],
                    "path": [],
                },
                "mainBetOffer": {
                    "id": 200,
                    "criterion": {"id": 1, "englishLabel": "Match Odds"},
                    "betOfferType": {"id": 2, "name": "Match"},
                    "outcomes": [
                        {"id": 1, "englishLabel": "Djokovic", "odds": 1750, "status": "OPEN", "type": "OT_ONE"},
                        {"id": 2, "englishLabel": "Alcaraz", "odds": 2150, "status": "OPEN", "type": "OT_TWO"},
                    ],
                },
                "liveData": {
                    "score": {"home": "30", "away": "0", "who": "HOME", "version": 1},
                    "statistics": {"sets": {"home": [6, 2], "away": [4, 0]}, "homeServe": True, "version": 1},
                },
            }]
        }
        updates = parse_live_events(data, accepted_sports=["TENNIS"])
        assert len(updates) == 1
        u = updates[0]
        assert u.match_id == 99999
        assert u.live is True
        assert u.odds["Djokovic"] == 1.75
        assert u.meta is not None
        assert u.meta.sport == "TENNIS"
        assert u.score is not None
        assert u.score.sets_home == [6, 2]


class TestDiffOdds:
    def test_new_selection(self):
        changed, movements = diff_odds(1, "Match Odds", {"A": 1.5}, {})
        assert changed == {"A": 1.5}
        assert movements == {"A": "new"}

    def test_odds_up(self):
        changed, movements = diff_odds(1, "Match Odds", {"A": 2.0}, {"A": 1.8})
        assert changed == {"A": 2.0}
        assert movements == {"A": "up"}

    def test_odds_down(self):
        changed, movements = diff_odds(1, "Match Odds", {"A": 1.5}, {"A": 1.8})
        assert changed == {"A": 1.5}
        assert movements == {"A": "down"}

    def test_steady_excluded(self):
        changed, movements = diff_odds(1, "Match Odds", {"A": 1.85}, {"A": 1.85})
        assert "A" not in changed


class TestParseFootball:
    def test_parse_football_match(self):
        data = {
            "events": [{
                "event": {
                    "id": 55555,
                    "name": "River Plate - San Lorenzo",
                    "homeName": "River Plate",
                    "awayName": "San Lorenzo",
                    "group": "Primera Division",
                    "start": "2026-05-07T22:00:00Z",
                    "state": "STARTED",
                    "sport": "FOOTBALL",
                    "tags": ["MATCH"],
                    "path": [],
                },
                "betOffers": [{
                    "id": 300,
                    "criterion": {"id": 1, "englishLabel": "Full Time", "label": "Resultat"},
                    "betOfferType": {"id": 2, "name": "Match", "englishName": "Match"},
                    "outcomes": [
                        {"id": 1, "englishLabel": "River Plate", "odds": 2100, "status": "OPEN", "type": "OT_ONE"},
                        {"id": 2, "englishLabel": "Draw", "odds": 3200, "status": "OPEN", "type": "OT_TWO"},
                        {"id": 3, "englishLabel": "San Lorenzo", "odds": 3500, "status": "OPEN", "type": "OT_TWO"},
                    ],
                    "tags": ["MAIN"],
                }],
            }]
        }
        meta, prices = parse_match_list(
            data, sport_name="FOOTBALL",
            tracked_markets=["Full Time", "Total Goals", "Handicap"],
        )
        assert 55555 in meta
        assert meta[55555].sport == "FOOTBALL"
        assert meta[55555].player_a == "River Plate"
        assert "Full Time" in prices[55555]
        assert prices[55555]["Full Time"]["River Plate"] == 2.10
        assert prices[55555]["Full Time"]["Draw"] == 3.20

    def test_parse_betoffers_football(self):
        data = {
            "betOffers": [
                {
                    "criterion": {"id": 1, "englishLabel": "Full Time", "label": "Resultat"},
                    "outcomes": [
                        {"id": 1, "englishLabel": "Home", "odds": 1800, "status": "OPEN", "type": "OT_ONE"},
                    ],
                },
                {
                    "criterion": {"id": 2, "englishLabel": "Total Goals", "label": "Total Goals"},
                    "outcomes": [
                        {"id": 2, "englishLabel": "Over 2.5", "odds": 1900, "line": 2500, "status": "OPEN", "type": "OT_OVER"},
                    ],
                },
                {
                    "criterion": {"id": 3, "englishLabel": "Player to Score", "label": "Player to Score"},
                    "outcomes": [
                        {"id": 3, "englishLabel": "Messi", "odds": 3000, "status": "OPEN", "type": "OT_PLAYER"},
                    ],
                },
            ]
        }
        updates = parse_betoffers(
            data, match_id=55555,
            tracked_markets=["Full Time", "Total Goals"],
        )
        assert len(updates) == 2
        labels = {u.market for u in updates}
        assert "Full Time" in labels
        assert "Total Goals 2.5" in labels
        assert "Player to Score" not in labels
