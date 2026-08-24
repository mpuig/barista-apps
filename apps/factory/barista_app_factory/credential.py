"""Keeping the coordinator's own credential alive.

A delegated grant is minted by the provider and injected into a session once, as
an environment variable that cannot be rewritten in a running process. The
reference provider's grants live fifteen minutes; a mission's default task
timeout is 3600 seconds. So a coordinator that does nothing authenticates for a
quarter of an hour of an hour-long mission and then holds a dead credential.

This module refreshes it while the mission runs, and *before* it lapses: a
lapsed grant cannot be refreshed (design D3), so letting one expire is a lockout
rather than a soft failure. When that happens anyway it raises ``LostAuthority``,
which the coordinator reports as lost authority rather than as failed work — one
is an operator problem, the other is a task problem, and reporting the wrong one
sends someone to the wrong place.
"""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from typing import Callable, Optional

from barista_app_sdk import BaristaClient, Grant
from barista_app_sdk.errors import AuthenticationError, HostAPIError

DELEGATED_CAPABILITY = "grants.delegated"

#: Refresh when less than this fraction of the observed lifetime remains.
MARGIN_FRACTION = 0.2
#: ...but never with less than this much wall-clock room.
MARGIN_FLOOR_SECONDS = 60.0
#: ...and never more than this fraction of the lifetime.
MARGIN_CEILING_FRACTION = 0.5


def refresh_margin_seconds(lifetime_seconds: float) -> float:
    """How long before expiry to refresh, and why this number.

    ``min(max(0.2 * lifetime, 60s), 0.5 * lifetime)``. For the reference
    provider's 900-second grant that is **180 seconds**.

    * **A fifth of the observed lifetime, not a constant.** The margin has to
      scale with whatever lifetime the provider chose; a number tuned for a
      fifteen-minute grant is wrong for a one-minute one, and hard-coding one
      would put an arbitrary number into an app that is supposed to be portable.
      At 180s the coordinator gets many chances: its check interval is a small
      fraction of that, so a failed attempt is followed by another well before
      the grant lapses.
    * **At least sixty seconds.** The coordinator decides "is it time yet?" by
      comparing *its own* clock against a timestamp the *provider* produced.
      Well-synchronised hosts differ by milliseconds; hosts that are not differ
      by tens of seconds, and refreshing at the last moment against a clock that
      is slightly ahead of yours is a lockout, not a near miss. A minute covers
      ordinary skew plus a full round trip.
    * **At most half the lifetime.** Without a ceiling the floor would exceed a
      deliberately short lifetime — a test provider handing out 30-second grants
      — and every check would rotate. That is a rotation storm, not a safety
      margin. Half the lifetime means at least one full grant's use per rotation.
    """
    return min(
        max(lifetime_seconds * MARGIN_FRACTION, MARGIN_FLOOR_SECONDS),
        lifetime_seconds * MARGIN_CEILING_FRACTION,
    )


class LostAuthority(RuntimeError):
    """The coordinator can no longer act: its credential lapsed, was revoked, or
    was refused.

    Deliberately not a task failure. Nothing has been learned about the work —
    the coordinator simply has no authority to carry it out. That is an operator
    problem (provision a new grant), and a mission that reported it as failed
    work would send someone to debug a task that never ran.
    """


class CredentialKeeper:
    """Refreshes the coordinator's delegated grant before it lapses.

    Inactive, and harmless, on a provider that does not advertise
    ``grants.delegated`` or with a credential that is not a refreshable grant (a
    tenant key, say): the mission then runs on exactly the credential it was
    given, as it did before this existed, and a lapse surfaces as lost authority
    instead of being silently retried into failure.
    """

    def __init__(
        self,
        client: BaristaClient,
        *,
        now: Callable[[], float] = time.time,
        margin_seconds: Optional[float] = None,
        check_interval_seconds: Optional[float] = None,
    ):
        self.client = client
        self._now = now
        self._margin_override = margin_seconds
        self._check_interval_override = check_interval_seconds
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

        self.active = False
        self.inactive_reason: Optional[str] = None
        self.lifetime_seconds: Optional[float] = None
        self.expires_at: Optional[float] = None
        self.refreshes = 0
        self.lost_authority: Optional[str] = None
        """Set by the background ticker when it can no longer keep the credential
        alive. The coordinator reads it at its next checkpoint — a background
        thread must not decide a mission's outcome."""

    # -- observation ------------------------------------------------------ #
    @property
    def margin_seconds(self) -> float:
        if self._margin_override is not None:
            return self._margin_override
        if not self.lifetime_seconds:
            return MARGIN_FLOOR_SECONDS
        return refresh_margin_seconds(self.lifetime_seconds)

    @property
    def check_interval_seconds(self) -> float:
        if self._check_interval_override is not None:
            return self._check_interval_override
        # Several checks inside one margin, capped so a long-lived grant does not
        # keep a thread busy: the margin is what protects the deadline, the
        # interval only decides how promptly it is noticed.
        return max(0.5, min(self.margin_seconds / 4.0, 30.0))

    def seconds_remaining(self) -> Optional[float]:
        if self.expires_at is None:
            return None
        return self.expires_at - self._now()

    def due(self) -> bool:
        remaining = self.seconds_remaining()
        if remaining is None:
            return False
        return remaining <= self.margin_seconds

    def lapsed(self) -> bool:
        remaining = self.seconds_remaining()
        return remaining is not None and remaining <= 0

    # -- lifecycle -------------------------------------------------------- #
    def establish(self) -> bool:
        """Learn when the credential expires, by refreshing it once.

        A grant arrives with no expiry attached, so the only way to find out how
        long it is good for — through the published contract — is to ask for a
        replacement and read the new one's. Two consequences, both intended:

        * The secret that was written into this process's environment (readable
          by anything that can read the environment) is replaced immediately by
          one held only in memory.
        * A restart cannot reuse the environment's copy, because it was rotated.
          That is inherent to rotation rather than new here — any refresh during
          a mission has the same effect — and it surfaces as lost authority,
          which is the honest report: an operator must provision a new grant.
        """
        with self._lock:
            try:
                if not self.client.supports(DELEGATED_CAPABILITY):
                    self.inactive_reason = (
                        f"the provider does not advertise {DELEGATED_CAPABILITY}, so this "
                        "credential cannot be refreshed and the mission is bounded by its "
                        "lifetime"
                    )
                    return False
            except HostAPIError as exc:
                self.inactive_reason = f"could not read discovery: {exc}"
                return False
            try:
                grant = self.client.refresh_grant()
            except HostAPIError as exc:
                self.inactive_reason = (
                    "the credential is not a refreshable delegated grant "
                    f"({exc.error_class or 'error'}/{exc.code or exc.status}): {exc}"
                )
                return False
            self._adopt(grant)
            self.active = True
            return True

    def _adopt(self, grant: Grant) -> None:
        expires_at = grant.expires_at_epoch()
        self.expires_at = expires_at
        if expires_at is not None:
            observed = expires_at - self._now()
            # The first observation defines the lifetime; later ones only move
            # the deadline. A refresh that lands late must not shrink the margin.
            if observed > 0 and (self.lifetime_seconds is None or observed > self.lifetime_seconds):
                self.lifetime_seconds = observed
        self.refreshes += 1

    def ensure_fresh(self) -> bool:
        """Refresh if the margin has been reached. True if it refreshed.

        Raises ``LostAuthority`` when the credential has already lapsed, or when
        the refresh is refused: both mean the coordinator can no longer act, and
        neither is a fact about the work.
        """
        with self._lock:
            if not self.active:
                return False
            if self.lapsed():
                self.active = False
                raise LostAuthority(
                    "the coordinator's delegated grant expired before it was refreshed "
                    f"(margin {self.margin_seconds:.0f}s of a {self.lifetime_seconds:.0f}s "
                    "lifetime); a lapsed grant cannot be refreshed, so a new one must be "
                    "provisioned"
                )
            if not self.due():
                return False
            try:
                grant = self.client.refresh_grant()
            except AuthenticationError as exc:
                self.active = False
                raise LostAuthority(
                    f"the provider no longer accepts the coordinator's credential: {exc}"
                ) from exc
            except HostAPIError as exc:
                self.active = False
                raise LostAuthority(
                    f"refreshing the coordinator's credential was refused: {exc}"
                ) from exc
            self._adopt(grant)
            return True

    # -- background ticker ------------------------------------------------ #
    @contextmanager
    def running(self):
        """Establish the credential and keep it fresh for the duration.

        A ticker is what covers a *single long call*: a task with an hour's
        timeout outlives a fifteen-minute grant without the coordinator reaching
        a task boundary, so freshness cannot be a per-task check alone.
        """
        established = self.establish()
        if established:
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._tick, name="factory-credential", daemon=True
            )
            self._thread.start()
        try:
            yield self
        finally:
            self._stop.set()
            if self._thread is not None:
                self._thread.join(timeout=5)
                self._thread = None

    def _tick(self) -> None:
        while not self._stop.is_set():
            try:
                self.ensure_fresh()
            except LostAuthority as exc:
                self.lost_authority = str(exc)
                return
            except Exception:  # noqa: BLE001 - a ticker never kills the mission
                pass
            self._stop.wait(self.check_interval_seconds)

    def status(self) -> dict:
        """What to record in a mission result, so the choice is auditable."""
        return {
            "active": self.active,
            "refreshes": self.refreshes,
            "lifetime_seconds": (
                round(self.lifetime_seconds, 1) if self.lifetime_seconds else None
            ),
            "margin_seconds": round(self.margin_seconds, 1) if self.active else None,
            "inactive_reason": self.inactive_reason,
        }
