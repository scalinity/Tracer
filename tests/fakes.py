"""A fake Anthropic client for the generator tests: synchronous replies and the Batch API."""

import json
from types import SimpleNamespace

from trails import render_trail


def message(trail):
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=json.dumps(trail))], stop_reason="end_turn", _request_id="req",
        usage=SimpleNamespace(input_tokens=300, output_tokens=600, cache_creation_input_tokens=0,
                              cache_read_input_tokens=4000))


def damaged(trail):
    trail = json.loads(json.dumps(trail))
    trail["outcome"]["fault"] = "wrong"
    return trail


class FakeClient:
    """Answers with the correct trail for each brief.

    broken: damaged on the first synchronous call only. always_broken: damaged every time, in the batch too.
    batch_broken: damaged in the batch only. errored / expired: the batch reports that outcome for the id.
    """

    def __init__(self, pool, broken=(), always_broken=(), batch_broken=(), errored=(), expired=()):
        self.by_id = {b["specimen_id"]: b for b in pool}
        self.broken, self.always_broken = set(broken), set(always_broken)
        self.batch_broken, self.errored, self.expired = set(batch_broken), set(errored), set(expired)
        self.seen: dict[str, int] = {}
        self.creates, self.counts, self.submitted = [], [], {}
        self.status = "ended"
        self.messages = SimpleNamespace(
            create=self.create, count_tokens=self.count_tokens,
            batches=SimpleNamespace(create=self.batch_create, retrieve=self.batch_retrieve, results=self.batch_results))

    def create(self, **kwargs):
        self.creates.append(kwargs)
        sid = json.loads(kwargs["messages"][0]["content"])["specimen_id"]
        self.seen[sid] = self.seen.get(sid, 0) + 1
        trail = render_trail(self.by_id[sid])
        if sid in self.always_broken or (sid in self.broken and self.seen[sid] == 1):
            trail = damaged(trail)
        return message(trail)

    def count_tokens(self, **kwargs):
        self.counts.append(kwargs)
        empty = json.loads(kwargs["messages"][0]["content"])["specimen_id"] == ""
        return SimpleNamespace(input_tokens=4000 if empty else 4240)

    def batch_create(self, requests):
        batch_id = f"msgbatch_{len(self.submitted) + 1}"
        self.submitted[batch_id] = requests
        return SimpleNamespace(id=batch_id)

    def batch_retrieve(self, batch_id):
        return SimpleNamespace(id=batch_id, processing_status=self.status,
                               request_counts=SimpleNamespace(processing=len(self.submitted[batch_id])))

    def batch_results(self, batch_id):
        for request in self.submitted[batch_id]:
            sid = request["custom_id"]
            if sid in self.errored:
                yield SimpleNamespace(custom_id=sid, result=SimpleNamespace(type="errored", error=None))
            elif sid in self.expired:
                yield SimpleNamespace(custom_id=sid, result=SimpleNamespace(type="expired"))
            else:
                trail = render_trail(self.by_id[sid])
                if sid in self.always_broken or sid in self.batch_broken:
                    trail = damaged(trail)
                yield SimpleNamespace(custom_id=sid, result=SimpleNamespace(type="succeeded", message=message(trail)))
