class FakeRunner:
    """Test double for ClaudeRunner. Records every call in `.calls`."""

    def __init__(self, json_result=None, stream_chunks=(), on_run=None, raise_error=None):
        self.json_result = json_result
        self.stream_chunks = stream_chunks
        self.on_run = on_run
        self.raise_error = raise_error
        self.calls: list[dict] = []

    def _begin(self, prompt, kwargs):
        self.calls.append({"prompt": prompt, **kwargs})
        if self.on_run:
            self.on_run(kwargs["cwd"])
        if self.raise_error:
            raise self.raise_error

    async def run_json(self, prompt, **kwargs):
        self._begin(prompt, kwargs)
        return self.json_result

    async def stream_text(self, prompt, **kwargs):
        self._begin(prompt, kwargs)
        for chunk in self.stream_chunks:
            yield chunk


class FakeRepo:
    """Test double for BrainRepo.push. Records remotes in `.pushes`; `results` scripts ok/fail."""

    def __init__(self, results: list[bool]):
        self.results = list(results)
        self.pushes: list[str] = []

    def push(self, remote_url: str) -> tuple[bool, str]:
        self.pushes.append(remote_url)
        ok = self.results.pop(0)
        return ok, "" if ok else "err"


class Clock:
    """Callable fake clock with a settable `.t`."""

    def __init__(self, t: float = 0):
        self.t = t

    def __call__(self) -> float:
        return self.t
