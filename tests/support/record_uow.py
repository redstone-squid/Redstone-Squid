"""Committed record state shared by application test scopes."""

from tests.support.unit_of_work import PublishingFakeUnitOfWork


class RecordQueryScope[RunsT](PublishingFakeUnitOfWork[RunsT]):
    @property
    def records(self) -> RunsT:
        return self.state


class RecordScope[CandidatesT, RunsT](RecordQueryScope[RunsT]):
    def __init__(self, candidates: CandidatesT, runs: RunsT) -> None:
        super().__init__(runs)
        self.record_candidates = candidates
