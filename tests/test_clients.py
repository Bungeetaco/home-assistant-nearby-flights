"""HTTP-client behavior with a stubbed session: adsbdb's cache-only-definitive
policy + LRU bound, and OpenSky's 429 backoff / 401 token recovery."""
import time

import pytest

import api.adsbdb as adsbdb_mod
from api.adsbdb import AdsbdbClient
from api.opensky import OpenSkyAuthError, OpenSkyClient, OpenSkyRateLimitError


class FakeResponse:
    def __init__(self, status_code=200, json_data=None, headers=None, text=""):
        self.status_code = status_code
        self._json = json_data
        self.headers = headers or {}
        self.text = text

    def json(self):
        if self._json is None:
            raise ValueError("no json")
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeSession:
    """Answers .get()/.post() from queues; records how many requests it saw."""

    def __init__(self):
        self.get_queue = []
        self.post_queue = []
        self.get_count = 0
        self.post_count = 0

    def get(self, *args, **kwargs):
        self.get_count += 1
        item = self.get_queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def post(self, *args, **kwargs):
        self.post_count += 1
        item = self.post_queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def close(self):
        pass


ROUTE_JSON = {"response": {"flightroute": {"airline": {"name": "Test Air"}}}}
TOKEN_JSON = {"access_token": "tok-1", "expires_in": 1800}


@pytest.fixture
def clock(monkeypatch):
    state = {"t": 1000.0}
    monkeypatch.setattr(time, "monotonic", lambda: state["t"])
    return state


class TestAdsbdbCache:
    def make(self):
        client = AdsbdbClient()
        session = FakeSession()
        client._session = session
        return client, session

    def test_definitive_200_is_cached(self):
        client, session = self.make()
        session.get_queue.append(FakeResponse(200, ROUTE_JSON))
        assert client.lookup_callsign("TST123")["airline"]["name"] == "Test Air"
        assert client.lookup_callsign("TST123")["airline"]["name"] == "Test Air"
        assert session.get_count == 1  # second hit came from cache

    def test_404_negative_is_cached(self):
        client, session = self.make()
        session.get_queue.append(FakeResponse(404))
        assert client.lookup_callsign("NOPE") is None
        assert client.lookup_callsign("NOPE") is None
        assert session.get_count == 1

    @pytest.mark.parametrize("failure", [FakeResponse(429), FakeResponse(500), RuntimeError("timeout")])
    def test_transient_failure_is_not_cached(self, failure):
        # Regression: one network blip used to permanently disable enrichment
        # for that callsign until HA restarted.
        client, session = self.make()
        session.get_queue.append(failure)
        assert client.lookup_callsign("TST123") is None
        session.get_queue.append(FakeResponse(200, ROUTE_JSON))
        assert client.lookup_callsign("TST123")["airline"]["name"] == "Test Air"
        assert session.get_count == 2  # retried, then cached

    def test_unexpected_200_shape_degrades_to_cached_none(self):
        client, session = self.make()
        session.get_queue.append(FakeResponse(200, {"response": "unknown callsign"}))
        assert client.lookup_callsign("TST123") is None
        assert client.lookup_callsign("TST123") is None
        assert session.get_count == 1

    def test_lru_bound(self, monkeypatch):
        monkeypatch.setattr(adsbdb_mod, "MAX_CACHE_ENTRIES", 3)
        client, session = self.make()
        for i in range(5):
            session.get_queue.append(FakeResponse(404))
            client.lookup_callsign(f"CS{i}")
        assert len(client._route_cache) == 3
        assert "CS0" not in client._route_cache  # oldest evicted
        assert "CS4" in client._route_cache


class TestOpenSkyClient:
    def make(self):
        client = OpenSkyClient("id", "secret")
        session = FakeSession()
        client._session = session
        return client, session

    def states_response(self):
        return FakeResponse(200, {"states": [[
            "abc123", "TST123  ", None, None, None, -75.1, 45.1, 3000.0,
            False, 200.0, 90.0, 0.0, None, 3100.0, "1200", False, 0,
        ]]})

    def test_happy_path_parses_states(self, clock):
        client, session = self.make()
        session.post_queue.append(FakeResponse(200, TOKEN_JSON))
        session.get_queue.append(self.states_response())
        states = client.get_states_bbox(44, -76, 46, -74)
        assert states[0]["icao24"] == "abc123"
        assert states[0]["callsign"] == "TST123"

    def test_429_sets_backoff_and_blocks_next_call(self, clock):
        client, session = self.make()
        session.post_queue.append(FakeResponse(200, TOKEN_JSON))
        session.get_queue.append(FakeResponse(429, headers={"X-Rate-Limit-Retry-After-Seconds": "120"}))
        with pytest.raises(OpenSkyRateLimitError):
            client.get_states_bbox(44, -76, 46, -74)
        # Next call inside the window must short-circuit without any HTTP.
        with pytest.raises(OpenSkyRateLimitError):
            client.get_states_bbox(44, -76, 46, -74)
        assert session.get_count == 1
        # After the announced window passes, requests flow again.
        clock["t"] += 121
        session.get_queue.append(self.states_response())
        assert client.get_states_bbox(44, -76, 46, -74)

    def test_401_on_states_refreshes_token_once(self, clock):
        client, session = self.make()
        session.post_queue.append(FakeResponse(200, TOKEN_JSON))
        session.get_queue.append(FakeResponse(401))
        session.post_queue.append(FakeResponse(200, {"access_token": "tok-2", "expires_in": 1800}))
        session.get_queue.append(self.states_response())
        assert client.get_states_bbox(44, -76, 46, -74)
        assert session.post_count == 2

    def test_credential_rejection_is_auth_error(self, clock):
        client, session = self.make()
        session.post_queue.append(FakeResponse(401, text="invalid_client"))
        with pytest.raises(OpenSkyAuthError):
            client.validate_credentials()

    def test_token_server_error_is_not_auth_error(self, clock):
        # A 503 on the token endpoint is transient - it must not masquerade as
        # "your credentials are wrong" (which would trigger reauth upstream).
        client, session = self.make()
        session.post_queue.append(FakeResponse(503))
        with pytest.raises(Exception) as excinfo:
            client.validate_credentials()
        assert not isinstance(excinfo.value, OpenSkyAuthError)

    def test_short_expires_in_does_not_go_negative(self, clock):
        client, session = self.make()
        session.post_queue.append(FakeResponse(200, {"access_token": "tok", "expires_in": 10}))
        client.validate_credentials()
        assert client._token_expires_at > clock["t"]
