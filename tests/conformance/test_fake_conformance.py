from __future__ import annotations

from keel_broker_api.conformance.suite import BrokerConformanceTests
from keel_broker_fake import FakeAdapter


class TestFakeConformance(BrokerConformanceTests):
    def broker(self) -> FakeAdapter:
        return FakeAdapter()
