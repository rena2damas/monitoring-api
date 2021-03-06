import abc
import os
import time
import typing

import requests
from flask import current_app
from urllib.parse import urlencode

from src.models.bright import HealthCheck, HealthCheckStatus

__all__ = ("BrightSvc",)


class BrightBase(abc.ABC):
    """Base class for Bright API."""

    default_headers = {"Content-Type": "application/json", "Accept": "application/json"}

    def __init__(
        self,
        url=None,
        session=None,
        basic_auth=(),
        cert_auth=(),
        verify=True,
        timeout=5,
    ):
        """
        :param session: an already existing session session.
        :param basic_auth: a tuple of username and password
               to use when establishing a session via HTTP BASIC
        authentication.
        :param cert_auth: a tuple of cert and key to use
               when establishing a session. The pair is used for both
        authentication and encryption.
        :param verify: check whether verify SSL connection
        :param timeout: how much time until connection is dropped, in seconds.
        """
        self.url = url
        if session is None:
            self._session = requests.Session()
        else:
            self._session = session
        if basic_auth:
            self._create_basic_session(basic_auth)
        elif cert_auth is not None:
            self._create_cert_session(cert_auth)
        self._session.headers.update(self.default_headers)
        self.verify = verify
        self.timeout = timeout

    def _create_basic_session(self, basic_auth):
        self._session.auth = basic_auth

    def _create_cert_session(self, cert_auth):
        self._session.cert = cert_auth

    @property
    def version(self):
        base = f"{self.url}/json"
        params = {
            "service": "cmmain",
            "call": "getVersion",
        }
        response = self._session.post(
            url=base, json=params, verify=self.verify, timeout=self.timeout
        ).json()
        return response.get("cmVersion")

    @abc.abstractmethod
    def measurable(self, name):
        pass

    @staticmethod
    @abc.abstractmethod
    def measurable_mapper(raw):
        pass


class Bright(BrightBase):
    """Generic Bright implementation."""

    def measurable(self, name):
        raise NotImplementedError("use a specific Bright version")

    @staticmethod
    def measurable_mapper(raw):
        raise NotImplementedError("use a specific Bright version")


class Bright7(BrightBase):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.base = f"{self.url}/json"

    def entity(self, name):
        params = {"service": "cmdevice", "call": "getDevice", "arg": name}
        return self._session.post(
            url=self.base, json=params, verify=self.verify, timeout=self.timeout
        ).json()

    def measurable(self, name):
        params = {"service": "cmmon", "call": "getHealthcheck", "arg": name}
        return self._session.post(
            url=self.base, json=params, verify=self.verify, timeout=self.timeout
        ).json()

    def latest_measurable_data(self, measurable, entity) -> list[dict]:
        measurable_id = self.measurable(measurable).get("uniqueKey")
        entity_id = self.entity(entity).get("uniqueKey")
        if not entity_id or not measurable_id:
            return []

        params = {
            "service": "cmmon",
            "call": "getLatestPickedRates",
            "args": [[entity_id], [{"metricId": measurable_id}]],
        }
        return [
            dict(**data, measurable=measurable, entity=entity)
            for data in self._session.post(
                self.url, json=params, verify=self.verify
            ).json()
        ]

    @staticmethod
    def measurable_mapper(raw) -> HealthCheck:
        return (
            HealthCheck(
                name=raw["measurable"],
                status=HealthCheckStatus(round(float(raw["rate"]))),
                node=raw["entity"],
                timestamp=raw["timeStamp"],
                seconds_ago=int(time.time() - raw["timeStamp"]),
                raw=raw,
            )
            if raw
            else None
        )


class Bright8(BrightBase):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.base = f"{self.url}/rest/v1"

    def measurable(self, name):
        base = f"{self.url}/json"
        params = {"service": "cmmon", "call": "getMonitoringMeasurable", "arg": name}
        return self._session.post(
            url=base, json=params, verify=self.verify, timeout=self.timeout
        ).json()

    def latest_measurable_data(self, measurable, entity=None) -> list[dict]:
        params = {
            "measurable": measurable,
            **({"entity": entity} if entity is not None else {}),
        }

        url = f"{self.base}/monitoring/latest?{urlencode(params)}"
        return (
            self._session.get(url=url, verify=self.verify, timeout=self.timeout)
            .json()
            .get("data", [])
        )

    @staticmethod
    def measurable_mapper(raw) -> HealthCheck:
        return (
            HealthCheck(
                name=raw["measurable"],
                status=HealthCheckStatus(raw["value"]),
                node=raw["entity"],
                timestamp=raw["time"],
                seconds_ago=int(raw["age"]),
                raw=raw,
            )
            if raw
            else None
        )


class BrightSvc:
    def __init__(
        self,
        host=None,
        port=443,
        protocol="https",
        basic_auth=(),
        cert_auth=(),
        version=None,
        **kwargs,
    ):
        host = host or current_app.config["BRIGHT_COMPUTING_HOST"]
        port = port or current_app.config["BRIGHT_COMPUTING_PORT"]
        url = f"{protocol}://{host}:{port}"

        if not basic_auth and not cert_auth:
            cert = current_app.config["BRIGHT_COMPUTING_CERT_PATH"]
            key = current_app.config["BRIGHT_COMPUTING_KEY_PATH"]

            # handle relative paths
            if not os.path.isabs(cert) and not os.path.isabs(key):
                instance_path = os.path.dirname(current_app.instance_path)
                cert = os.path.join(instance_path, cert)
                key = os.path.join(instance_path, key)
            cert_auth = (cert, key)

        self.version = version or Bright(url=url, **kwargs).version
        self.instance = self.factory(self.version)(
            url=url, basic_auth=basic_auth, cert_auth=cert_auth, **kwargs
        )

    @staticmethod
    def factory(version):
        major_version = int(float(version))
        if major_version not in (7, 8):
            raise ValueError("Unsupported version")
        elif major_version == 7:
            return Bright7
        elif major_version == 8:
            return Bright8

    @staticmethod
    def supported_measurables():
        return current_app.config["SUPPORTED_MEASURABLES"]

    def health_checks(self, node=None) -> list[HealthCheck]:
        checks = (
            self.health_check(key=measurable, node=node)
            for measurable in self.supported_measurables()
        )
        return [x for x in checks if x is not None]

    def health_check(self, key, node=None) -> typing.Optional[HealthCheck]:
        """Get translated measurable to a health check."""
        if key not in self.supported_measurables():
            return None

        data = self.latest_measurable_data(measurable=key, entity=node)
        measurable = next(iter(data), None)
        return self.measurable_mapper(raw=measurable)

    def __getattr__(self, name):
        return self.instance.__getattribute__(name)
