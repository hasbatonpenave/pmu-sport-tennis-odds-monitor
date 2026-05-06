import os
import tempfile
import time

from api.models import MatchMeta, OddsUpdate
from storage.sqlite import SQLiteRepository


class TestSQLiteRepository:
    def setup_method(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.repo = SQLiteRepository(db_path=self.tmp.name)
        self.repo.start()

    def teardown_method(self):
        self.repo.stop()
        self.repo.join(timeout=5)
        os.unlink(self.tmp.name)

    def _make_update(self, match_id=123, market="Match Odds", odds=None, live=False):
        return OddsUpdate(
            match_id=match_id,
            market=market,
            odds=odds or {"Player A": 1.85, "Player B": 2.10},
            meta=MatchMeta(
                match_id=match_id,
                name="Player A - Player B",
                player_a="Player A",
                player_b="Player B",
                competition="ATP Test",
                start_time="2026-05-07T12:00:00Z",
                state="STARTED" if live else "NOT_STARTED",
            ),
            live=live,
        )

    def test_write_and_read(self):
        u = self._make_update(live=True)
        self.repo.enqueue(u)
        time.sleep(0.8)  # Allow batch flush

        points = self.repo.get_history(123, "Player A", "Match Odds", limit=10)
        assert len(points) == 1
        assert points[0].odd == 1.85
        assert points[0].match_id == 123

    def test_multiple_writes(self):
        for i in range(5):
            self.repo.enqueue(self._make_update(match_id=200 + i))
        time.sleep(0.8)

        for i in range(5):
            points = self.repo.get_history(200 + i, "Player A", "Match Odds", limit=10)
            assert len(points) == 1

    def test_history_limit(self):
        for _ in range(10):
            self.repo.enqueue(self._make_update(match_id=999))
        time.sleep(0.8)

        points = self.repo.get_history(999, "Player A", "Match Odds", limit=5)
        assert len(points) == 5

    def test_empty_history(self):
        points = self.repo.get_history(404, "Nobody", "Match Odds")
        assert points == []
