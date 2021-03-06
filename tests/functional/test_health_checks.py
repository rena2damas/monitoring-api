import re
import time
from collections import namedtuple

import pytest
from requests_mock import ANY

from src.api import health_checks
from src.models.bright import HealthCheck, HealthCheckStatus
from src.schemas.serializers.bright import HealthCheckSchema
from src.services.bright import BrightSvc


@pytest.fixture(scope="class", params=(7, 8))
def svc(request):
    return BrightSvc(
        host="localhost",
        port=80,
        protocol="http",
        basic_auth=("user", "pass"),
        version=request.param,
    )


def measurable_factory(version):
    if version == 7:
        return namedtuple("Measurable", ["name", "timestamp", "rate"])
    elif version == 8:
        return namedtuple("Measurable", ["measurable", "time", "value"])
    return None


def measurable_data(version):
    factory = measurable_factory(version)
    if version == 7:
        fixtures = [
            factory(name="foo", timestamp=0, rate=0.0),
            factory(name="bar", timestamp=0, rate=2.0),
            factory(name="unsupported", timestamp=0, rate=0.0),
        ]
        return [
            [
                {
                    "entity": f"{fixture.name}_node",
                    "measurable": fixture.name,
                    "timeStamp": fixture.timestamp,
                    "rate": fixture.rate,
                }
            ]
            for fixture in fixtures
        ]
    elif version == 8:
        fixtures = [
            factory(measurable="foo", time=0, value="PASS"),
            factory(measurable="bar", time=0, value="FAIL"),
            factory(measurable="unsupported", time=0, value="UNKNOWN"),
        ]
        return [
            [
                {
                    "age": 0,
                    "entity": f"{fixture.measurable}_node",
                    "measurable": fixture.measurable,
                    "time": fixture.time,
                    "value": fixture.value,
                }
            ]
            for fixture in fixtures
        ]


class TestHealthCheckAPI:
    def test_supported_measurables(self, client):
        response = client.get("/health-checks/supported-measurables")
        assert response.status_code == 200
        assert response.json == ["foo", "bar"]

    def test_no_health_checks(self, client, svc, mocker, requests_mock):
        """Ensure GET request retrieves health checks."""
        matcher = re.compile(rf"{svc.url}.*")
        requests_mock.register_uri(ANY, matcher, json={})
        mocker.patch.object(health_checks, "BrightSvc", return_value=svc)

        response = client.get("/health-checks")
        assert response.status_code == 200
        assert response.json == []

    def test_valid_health_checks(self, client, svc, mocker):
        """Ensure GET request retrieves valid health checks."""
        mocker.patch.object(health_checks, "BrightSvc", return_value=svc)
        mocker.patch.object(
            svc.instance,
            "latest_measurable_data",
            side_effect=measurable_data(svc.version),
        )
        mocker.patch.object(time, "time", return_value=0)
        expected = HealthCheckSchema(many=True).dump(
            [
                HealthCheck(
                    name="foo",
                    status=HealthCheckStatus.ONLINE,
                    node="foo_node",
                    timestamp=0,
                    seconds_ago=0,
                ),
                HealthCheck(
                    name="bar",
                    status=HealthCheckStatus.OFFLINE,
                    node="bar_node",
                    timestamp=0,
                    seconds_ago=0,
                ),
            ]
        )

        response = client.get("/health-checks")
        assert response.status_code == 200
        assert response.json == expected

    def test_get_health_check_by_id(self, client, svc, mocker):
        """Ensure GET request retrieves an health check by id."""
        mocker.patch.object(health_checks, "BrightSvc", return_value=svc)
        mocker.patch.object(
            svc.instance,
            "latest_measurable_data",
            side_effect=measurable_data(svc.version),
        )
        mocker.patch.object(time, "time", return_value=0)
        expected = HealthCheckSchema().dump(
            HealthCheck(
                name="foo",
                status=HealthCheckStatus.ONLINE,
                node="foo_node",
                timestamp=0,
                seconds_ago=0,
            )
        )

        response = client.get("/health-checks/foo")
        assert response.status_code == 200
        assert response.json == expected

    def test_get_missing_health_check(self, client, svc, mocker):
        """Ensure GET request cannot retrieve an health check by id."""
        mocker.patch.object(health_checks, "BrightSvc", return_value=svc)

        response = client.get("/health-checks/unsupported")
        assert response.status_code == 404
        assert response.json == {"code": 404, "reason": "Not Found"}
