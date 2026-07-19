"""A deliberately divergent broker adapter, registered as the `fake` broker plugin."""

from keel_broker_fake.adapter import FakeAdapter

__all__ = ["FakeAdapter"]
