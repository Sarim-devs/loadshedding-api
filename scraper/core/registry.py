"""
core/registry.py

The piece that actually delivers on "if one provider fails, others still
work." It's deliberately tiny -- the real resilience work already
happened inside each provider's fetch() (catch-don't-raise, see
providers/base.py). This file's only extra job is defense in depth: even
if a provider breaks its contract and raises anyway (a bug, a library
throwing something unexpected), the registry still won't let it take
down the providers that haven't run yet.

C++ analogy
-----------
Think of this as the loop in a plugin host that calls each plugin's
entry point inside its own try/catch, the way a game engine wouldn't let
one broken mod crash the whole process -- you log the failure for that
one plugin and keep going.
"""

from __future__ import annotations

import logging

from core.models import FetchStatus, ProviderResult
from providers.base import ScheduleProvider

logger = logging.getLogger(__name__)


class ProviderRegistry:
    def __init__(self, providers: list[ScheduleProvider]):
        self._providers = providers

    def run_all(self) -> list[ProviderResult]:
        results: list[ProviderResult] = []
        for provider in self._providers:
            logger.info("Running provider: %s", provider.name)
            try:
                result = provider.fetch()
            except Exception as exc:  # noqa: BLE001 - last line of defense
                # A provider should never get here (it should catch its
                # own errors per the ScheduleProvider contract), but if it
                # does, we still don't want one bad provider to stop the
                # loop before the rest have had a chance to run.
                logger.exception(
                    "Provider %s raised an uncaught exception -- this is a "
                    "bug in that provider, it should have returned a FAILED "
                    "ProviderResult instead.",
                    provider.name,
                )
                result = ProviderResult(
                    source=provider.name,
                    status=FetchStatus.FAILED,
                    error=f"unhandled exception: {exc}",
                )

            self._log_result(result)
            results.append(result)
        return results

    @staticmethod
    def _log_result(result: ProviderResult) -> None:
        if result.status == FetchStatus.OK:
            logger.info(
                "%s: OK, %d feeders", result.source, len(result.feeders)
            )
        elif result.status == FetchStatus.PARTIAL:
            logger.warning(
                "%s: PARTIAL, %d feeders, %s",
                result.source, len(result.feeders), result.error,
            )
        else:
            logger.error("%s: FAILED, %s", result.source, result.error)
