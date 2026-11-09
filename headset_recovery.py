"""Low-frequency recovery scheduling; never changes normal device polling."""
from dataclasses import dataclass


@dataclass
class Retry:
    failures: int
    due: float


class HeadsetRecovery:
    def __init__(self):
        self.pending = {}

    def record(self, rows, now, *, full=False, requested=()):
        received = {r.key for r in rows}
        for key in set(requested) - received:
            self.pending.pop(key, None)
        if full:
            active = {r.key for r in rows if r.present}
            self.pending = {key: retry for key, retry in self.pending.items() if key in active}
        for row in rows:
            if (row.protocol != "mchose-headset" or not row.present
                    or (row.level is not None and not row.stale and row.online is not False)):
                self.pending.pop(row.key, None)
                continue
            previous = self.pending.get(row.key)
            failures = min(3, previous.failures + 1) if previous else 1
            self.pending[row.key] = Retry(failures, now + (5 if failures < 3 else 15))

    def due_keys(self, now):
        return {key for key, retry in self.pending.items() if retry.due <= now}

    def next_due(self, normal_due):
        return min([normal_due] + [retry.due for retry in self.pending.values()])

    def defer(self, keys, now):
        # Unexpected worker failures must not create a zero-delay retry loop.
        for key in keys:
            if key in self.pending:
                self.pending[key] = Retry(3, now + 15)
