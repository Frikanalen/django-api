import time
from datetime import UTC, datetime

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from fk import receiver
from fk.models import LiveSource, Organization, User

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 11, 13, 12, 0, 7, tzinfo=UTC)
END = int(datetime(2026, 11, 13, 12, 0, 0, tzinfo=UTC).timestamp())


@pytest.fixture
def member() -> User:
    return User.objects.create(email="member@example.test")


@pytest.fixture
def source(member) -> LiveSource:
    organization = Organization.objects.create(name="Contributor")
    organization.members.add(member)
    return LiveSource.objects.create(name="Studio", organization=organization)


def series(metric, points, end=END, **labels):
    """A Prometheus range series: `points` maps steps before `end` to values."""
    return {
        "metric": {"__name__": metric, **labels},
        "values": [[end - back * 30, str(value)] for back, value in sorted(points.items())],
    }


@pytest.fixture
def prometheus(monkeypatch):
    """Stands in for Prometheus: answers every range query with `result`."""
    state = {"result": [], "asked": []}

    def query_range(query, start, end, step):
        state["asked"].append((query, start, end, step))
        return state["result"]

    monkeypatch.setattr(receiver, "_prometheus_range", query_range)
    return state


def test_history_turns_counters_into_what_happened_each_step(source, prometheus):
    path = source.contribution_path
    megabit = 1_000_000 * 30 // 8  # bytes in a step at 1 Mbit/s
    prometheus["result"] = [
        series("paths", {4: 1, 3: 1, 2: 1, 1: 1, 0: 1}, name=path, state="ready"),
        series("paths", {4: 0, 3: 0, 2: 0, 1: 0, 0: 0}, name=path, state="notReady"),
        series(
            "paths_inbound_bytes",
            {4: 0, 3: 8 * megabit, 2: 16 * megabit, 1: 22 * megabit, 0: 30 * megabit},
            name=path,
        ),
        series("srt_conns_packets_received_loss", {4: 0, 3: 0, 2: 12, 1: 12, 0: 12}, path=path),
        series("srt_conns_packets_received_drop", {4: 0, 3: 0, 2: 3, 1: 3, 0: 3}, path=path),
    ]

    samples = receiver.history(source, 60, NOW)

    assert [s["at"] for s in samples][-1] == datetime.fromtimestamp(END, tz=UTC)
    assert [s["bitrate"] for s in samples] == [None, 8_000_000, 8_000_000, 6_000_000, 8_000_000]
    assert [s["lost"] for s in samples] == [0, 0, 12, 0, 0]
    assert [s["dropped"] for s in samples] == [0, 0, 3, 0, 0]
    assert not any(s["reconnected"] for s in samples)
    [(query, start, end, step)] = prometheus["asked"]
    assert path in query
    assert (start, end, step) == (END - 3600, END, 30)


def test_a_hole_in_the_signal_is_told_and_nothing_is_counted_across_it(source, prometheus):
    path = source.contribution_path
    prometheus["result"] = [
        series("paths", {5: 1, 4: 1, 3: 0, 1: 1, 0: 1}, name=path, state="ready"),
        series("paths_inbound_bytes", {5: 100, 4: 200, 1: 50, 0: 150}, name=path),
        series("srt_conns_packets_received_loss", {5: 0, 4: 0, 1: 40, 0: 40}, path=path),
    ]

    samples = receiver.history(source, 60, NOW)

    # Nothing before the first signal; the two steps without one after it.
    assert [(END - s["at"].timestamp()) // 30 for s in samples] == [5, 4, 3, 2, 1, 0]
    assert [s["receiving"] for s in samples] == [True, True, False, False, True, True]
    # The first step after the hole has nothing to be measured against.
    assert samples[4]["bitrate"] is None
    assert samples[4]["lost"] == 0
    assert samples[5]["bitrate"] == round(100 * 8 / 30)


def test_a_counter_starting_over_is_a_reconnect(source, prometheus):
    path = source.contribution_path
    prometheus["result"] = [
        series("paths", {2: 1, 1: 1, 0: 1}, name=path, state="ready"),
        series("paths_inbound_bytes", {2: 9000, 1: 100, 0: 9100}, name=path),
        series("srt_conns_packets_received_loss", {2: 50, 1: 0, 0: 0}, path=path),
    ]

    samples = receiver.history(source, 60, NOW)

    assert [s["reconnected"] for s in samples] == [False, True, False]
    assert samples[1]["bitrate"] is None
    assert samples[1]["lost"] == 0


def test_series_of_one_metric_are_added_up(source, prometheus):
    path = source.contribution_path
    prometheus["result"] = [
        series("paths", {1: 1, 0: 1}, name=path, state="ready"),
        series("paths_inbound_bytes", {1: 0, 0: 3750}, name=path),
        series("srt_conns_packets_received_loss", {1: 0, 0: 2}, path=path, id="a"),
        series("srt_conns_packets_received_loss", {1: 1, 0: 4}, path=path, id="b"),
    ]

    assert receiver.history(source, 60, NOW)[1]["lost"] == 5


def get_history(user, source, **params):
    client = APIClient()
    client.force_authenticate(user=user)
    return client.get(reverse("api-live-source-signal-history", args=[source.pk]), params)


def test_member_reads_history(member, source, prometheus):
    path = source.contribution_path
    end = int(time.time()) // 30 * 30
    prometheus["result"] = [
        series("paths", {1: 1, 0: 1}, end=end, name=path, state="ready"),
        series("paths_inbound_bytes", {1: 0, 0: 3_750_000}, end=end, name=path),
    ]

    response = get_history(member, source)

    assert response.status_code == 200
    data = response.json()
    assert data["available"] is True
    assert data["stepSeconds"] == 30
    assert len(data["samples"]) == 2
    assert data["samples"][1]["bitrate"] == 1_000_000
    assert data["samples"][1]["at"].startswith("2")


@pytest.mark.parametrize(
    ("asked", "minutes"),
    [
        ({}, 60),
        ({"minutes": 5}, 5),
        ({"minutes": 9999}, 180),
        ({"minutes": "x"}, 60),
        ({"minutes": -3}, 1),
    ],
)
def test_how_far_back_is_bounded(member, source, prometheus, asked, minutes):
    assert get_history(member, source, **asked).status_code == 200
    [(_, start, end, _)] = prometheus["asked"]
    assert end - start == minutes * 60


def test_without_prometheus_there_is_no_history_but_no_error(member, source, settings):
    settings.FK_PROMETHEUS_URL = ""
    data = get_history(member, source).json()
    assert data == {"available": False, "stepSeconds": 30, "samples": []}


def test_outsiders_get_no_history(source, prometheus):
    outsider = User.objects.create(email="outsider@example.test")
    assert get_history(outsider, source).status_code == 404
    assert prometheus["asked"] == []
