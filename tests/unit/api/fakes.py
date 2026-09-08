"""Shared fakes and test configuration for HTTP API transport tests.

Every double here subclasses the service it stands in for, so the type checker rejects a
double whose method drifts from production. `build_app` then constructs a real
`ApiServices`, which makes a field added to the graph a type error here rather than an
`AttributeError` in whichever route reads it first.
"""

import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import cast
from uuid import UUID

from fastapi import FastAPI

from squid.accounts.application import AccountService
from squid.accounts.domain import AccountIdentity, AccountProfile, CreatorAlias, CreatorProfile
from squid.accounts.domain.profiles import PublicCreatorProfile
from squid.accounts.errors import MinecraftAccountNotFoundError
from squid.api.app import create_api_app
from squid.auth.application.web import WebSessionService
from squid.builds.application import BuildQueryService, BuildService
from squid.builds.application.editing import BuildEditPatch
from squid.builds.application.queries import DEFAULT_BUILD_LIST_SORT, BuildListSort
from squid.builds.application.services import BuildEditor
from squid.builds.domain import Build
from squid.builds.domain.models import Status
from squid.builds.errors import BuildNotFoundError
from squid.cli_auth.application import CliAuthorizationService
from squid.cli_auth.domain import (
    CliDevice,
    CliDeviceEnrollment,
    CliIdentity,
    IssuedCliEnrollment,
    IssuedCliSession,
    IssuedCliSessionChallenge,
)
from squid.cli_auth.errors import InvalidCliEnrollmentError
from squid.config import ApiProcessConfig
from squid.core.errors import NotFoundError
from squid.core.pagination import FIRST_PAGE, Page, PageSelector
from squid.diagnostics.application import ErrorReportService
from squid.idempotency import IdempotencyService, PendingRequest
from squid.idempotency.domain import StoredResponse, UnsafeHttpMethod
from squid.media.application.jobs import (
    MediaDraftUploadAuthorization,
    MediaJobSnapshot,
    MediaNormalizationJobService,
    StagedMediaUploadSubmission,
)
from squid.media.domain import MediaLimits
from squid.minecraft_auth.application import InstallationCredentialService, PlayerAuthorizationService
from squid.minecraft_auth.domain.models import (
    AuthenticatedPaperInstallation,
    IssuedInstallationCredential,
    IssuedPlayerChallenge,
    IssuedPlayerGrant,
    MinecraftPlayerContext,
    PaperInstallation,
    PlayerAuthorizationChallenge,
    PublicServerProfile,
)
from squid.minecraft_auth.errors import InvalidChallengeError, InvalidInstallationCredentialError
from squid.notifications import NotificationPreferences, NotificationService
from squid.notifications.domain import (
    DEFAULT_INBOX_VISIBILITY,
    InboxNotification,
    InboxVisibility,
    NotificationSubscription,
    RecordSubscriptionFilter,
    SubscriptionKind,
)
from squid.permissions.application import PermissionService, SubjectRecords
from squid.permissions.application.epoch import PermissionEpochWatcher
from squid.permissions.domain import Pattern
from squid.permissions.domain.resolution import Subject
from squid.records.application import PublicRecordQueryService, RecordService
from squid.records.application.models import PublicRecordDetail, PublishedRecord
from squid.runtime import ApiServices, ApplicationRuntime
from squid.schematics.application import SchematicService
from squid.schematics.application.attachments import PublicSchematicDownload, StoredSchematic
from squid.schematics.application.commands import RenderRequest
from squid.schematics.application.previews import RenderedSchematic
from squid.schematics.domain import SCHEMATIC_FILE_SCHEMA_MAX_BYTES
from squid.schematics.errors import SchematicNotFoundError
from squid.search.application import SearchService
from squid.search.application.fields import DEFAULT_FIELD_REGISTRY
from squid.search.domain import SearchPage, SearchRequest
from squid.search.domain.query import SearchQuery
from squid.submissions.application import FormOptionSet, SubmissionFormService, build_submission_manifest
from squid.submissions.application.drafts import DraftActor, StoredDraft, SubmissionDraftService
from squid.submissions.application.finalization import (
    FinalizationJobSnapshot,
    SubmissionFinalizationService,
    SubmissionRequestResult,
)
from squid.submissions.application.inference_runs import SubmissionInferenceRuns
from squid.submissions.application.intake import SubmissionAttachmentIntake, SuppliedAttachment
from squid.submissions.application.revision_values import RevisionProposal
from squid.submissions.application.revisions import RevisionProposalService
from squid.submissions.application.schematics import DraftSchematicService
from squid.submissions.domain.schematics import DraftSchematic
from squid.submissions.errors import DraftNotFoundError
from squid.suggestions.application import SuggestionRegistry, SuggestionService
from squid.tags.application import TagService
from squid.tags.domain.models import TagDefinition
from squid.versions.application.services import VersionService
from squid.voting.application import VoteService
from squid.voting.domain.models import VoteSessionSnapshot


def credential_nodes(*raw: str) -> frozenset[Pattern]:
    """Build the parsed patterns a credential carries, as the real boundaries do."""
    return frozenset(Pattern.parse(pattern) for pattern in raw)


TEST_UUID = UUID("11111111-1111-1111-1111-111111111111")
NONEXISTENT_UUID = UUID("00000000-0000-0000-0000-000000000000")
TEST_VERIFICATION_CODE = 123_456
TEST_SYNERGY_SECRET = "test-secret"
TEST_CONFIG = ApiProcessConfig.model_validate(
    {
        "database": {"url": "postgresql://user:password@database.example/squid"},
        "verification": {"code_pepper": "verification-pepper"},
        "api": {
            "secret": TEST_SYNERGY_SECRET,
            "key_pepper": "api-key-pepper-for-tests",
            "session_pepper": "session-pepper-for-tests",
            "idempotency_active_key_id": "test-v1",
            "idempotency_keys": {"test-v1": "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA="},
            # The bootstrap secret no longer carries every node by default, so a
            # deployment that still relays verifications with it has to say so.
            "secret_nodes": ["account.verify.relay"],
        },
        "cli_auth": {
            "pepper": "cli-authorization-pepper-for-tests-32-bytes",
            "verification_uri": "https://catalogue.example/cli/link",
        },
        # Configured so the device-flow routes reach their handlers: an unconfigured
        # verification URI answers 503, which is indistinguishable from a real fault.
        "minecraft_auth": {
            "pepper": "minecraft-authorization-pepper-for-tests",
            "verification_uri": "https://catalogue.example/minecraft/link",
        },
    }
)


class MockAccountManager(AccountService):
    def __init__(self) -> None:
        """Answer from nothing; no repository is attached."""

    async def get_creator_alias(self, name: str) -> CreatorAlias | None:
        return None

    async def get_creator_profile(self, public_id: UUID) -> CreatorProfile | None:
        return None

    async def get_public_profile(self, public_id: UUID) -> PublicCreatorProfile | None:
        return None

    async def get_profile(self, account_id: int) -> AccountProfile:
        return AccountProfile.empty(account_id)

    async def list_identities(self, account_id: int) -> tuple[AccountIdentity, ...]:
        return ()

    async def generate_verification_code(self, minecraft_uuid: UUID) -> int:
        if minecraft_uuid == TEST_UUID:
            return TEST_VERIFICATION_CODE
        raise MinecraftAccountNotFoundError(minecraft_uuid)


class MockDatabaseManager:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class MockCliAuthorization(CliAuthorizationService):
    """Fail closed with a client-safe error for generated contract requests.

    Every operation the API can reach is named here rather than answered by a
    `__getattr__` catch-all, so a route reaching a method this does not implement is a
    type error instead of a stub that silently absorbs it.
    """

    def __init__(self) -> None:
        """Enroll nothing; no repository or clock is attached."""

    async def authenticate(self, token: str) -> CliIdentity:
        raise InvalidCliEnrollmentError

    async def start_enrollment(self, *, public_key: bytes, client_instance_id: UUID, label: str) -> IssuedCliEnrollment:
        raise InvalidCliEnrollmentError

    async def exchange_enrollment(self, *, device_code: str, signature: bytes) -> IssuedCliSession:
        raise InvalidCliEnrollmentError

    async def preview_enrollment(self, user_code: str) -> CliDeviceEnrollment:
        raise InvalidCliEnrollmentError

    async def approve_enrollment(self, *, user_code: str, account_id: int) -> CliDeviceEnrollment:
        raise InvalidCliEnrollmentError

    async def start_session_challenge(self, device_id: UUID) -> IssuedCliSessionChallenge:
        raise InvalidCliEnrollmentError

    async def exchange_session_challenge(
        self, *, device_id: UUID, challenge_id: UUID, nonce: str, signature: bytes
    ) -> IssuedCliSession:
        raise InvalidCliEnrollmentError

    async def list_devices(self, account_id: int) -> tuple[CliDevice, ...]:
        raise InvalidCliEnrollmentError

    async def revoke_device(self, *, device_id: UUID, account_id: int) -> bool:
        raise InvalidCliEnrollmentError

    async def revoke_current_session(self, identity: CliIdentity) -> bool:
        raise InvalidCliEnrollmentError


_EMPTY_PAGE: Page[object] = Page(items=(), total=0, next=None, prev=None)
"""What a paginated query returns when nothing matches.

The routes hand this straight to `render_page`, so a bare `()` or `[]` here reads as an
empty result but reaches production code as the wrong type and 500s.
"""


class MockBuilds(BuildService):
    """Refuses every edit: no build exists to edit in a transport-only app."""

    def __init__(self) -> None:
        """Hold no repository; `apply_edit` never reaches one."""

    async def apply_edit(
        self,
        actor: BuildEditor,
        build_id: int,
        patch: BuildEditPatch,
        *,
        expected_revision: int | None = None,
    ) -> Build:
        raise BuildNotFoundError(build_id)


class MockBuildQueries(BuildQueryService):
    def __init__(self) -> None:
        """Answer every read as a miss; no repository is attached."""

    async def get(self, build_id: int) -> Build | None:
        return None

    async def get_public(self, build_id: int) -> Build:
        raise BuildNotFoundError(build_id)

    async def get_many(self, build_ids: Sequence[int]) -> list[Build]:
        return []

    async def list_page(
        self,
        *,
        statuses: frozenset[Status],
        submitter_account_id: int | None = None,
        sort: BuildListSort = DEFAULT_BUILD_LIST_SORT,
        selector: PageSelector = FIRST_PAGE,
        page_size: int = 20,
    ) -> Page[Build]:
        return cast(Page[Build], _EMPTY_PAGE)


class MockSearch(SearchService):
    def __init__(self) -> None:
        """Index nothing, so every query is an empty page."""

    async def search(self, request: SearchRequest) -> SearchPage:
        return SearchPage(hits=(), total=0, next=None, prev=None)

    async def suggest(self, query: str | SearchQuery, *, limit: int = 5) -> tuple[str, ...]:
        return ()

    async def fields(self):
        return DEFAULT_FIELD_REGISTRY


class MockTags(TagService):
    def __init__(self) -> None:
        """Publish no tags."""

    async def public_definitions(self) -> tuple[TagDefinition, ...]:
        return ()

    async def public_definition(self, tag_id: int) -> TagDefinition | None:
        return None


class MockVersions(VersionService):
    def __init__(self) -> None:
        """Know no game versions."""

    async def list_all(self):
        return []


class MockErrorReports(ErrorReportService):
    """Records what the exception handlers captured, without a database."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def record(self, error: BaseException, **kwargs: object) -> None:
        self.calls.append({"error": error, **kwargs})


class MockSchematics(SchematicService):
    """An empty schematic store: listings are empty and every content read misses."""

    def __init__(self) -> None:
        """Attach no analyzer or repository; nothing here reaches one."""

    async def list_public_page(
        self, build_id: int, *, selector: PageSelector = FIRST_PAGE, page_size: int = 50
    ) -> Page[StoredSchematic]:
        return cast(Page[StoredSchematic], _EMPTY_PAGE)

    async def public_download(self, build_id: int, schematic_id: int) -> PublicSchematicDownload:
        raise SchematicNotFoundError

    async def render_now(self, build_id: int, *, request: RenderRequest | None = None) -> RenderedSchematic:
        raise SchematicNotFoundError

    async def render_content(self, recipe_hash: str, *, max_bytes: int = 8 * 1024 * 1024) -> bytes:
        raise SchematicNotFoundError


class MockMinecraftInstallations(InstallationCredentialService):
    """Fail closed with a client-safe error for generated contract requests."""

    def __init__(self) -> None:
        """Register no installations; no repository or pepper is attached."""

    async def authenticate_headers(
        self, installation_id: str | None, installation_secret: str | None
    ) -> AuthenticatedPaperInstallation:
        raise InvalidInstallationCredentialError

    async def register(
        self, *, owner_account_id: int, label: str, profile: PublicServerProfile | None = None
    ) -> IssuedInstallationCredential:
        raise InvalidInstallationCredentialError

    async def list_owned(self, owner_account_id: int) -> tuple[PaperInstallation, ...]:
        raise InvalidInstallationCredentialError

    async def rotate(self, *, installation_id: UUID, owner_account_id: int) -> IssuedInstallationCredential:
        raise InvalidInstallationCredentialError

    async def update_profile(
        self, *, installation_id: UUID, owner_account_id: int, profile: PublicServerProfile
    ) -> PaperInstallation:
        raise InvalidInstallationCredentialError

    async def revoke(self, *, installation_id: UUID, owner_account_id: int) -> PaperInstallation:
        raise InvalidInstallationCredentialError


class MockMinecraftPlayerAuthorization(PlayerAuthorizationService):
    """Fail closed with a client-safe error for generated contract requests."""

    def __init__(self) -> None:
        """Issue no challenges; no repository or clock is attached."""

    async def authenticate_fabric_player(self, token: str) -> MinecraftPlayerContext:
        raise InvalidChallengeError

    async def authenticate_paper_player(
        self, token: str, installation: AuthenticatedPaperInstallation
    ) -> MinecraftPlayerContext:
        raise InvalidChallengeError

    async def start_paper_challenge(
        self, *, installation: AuthenticatedPaperInstallation, java_uuid: UUID
    ) -> IssuedPlayerChallenge:
        raise InvalidChallengeError

    async def exchange_paper(
        self, *, device_code: str, installation: AuthenticatedPaperInstallation
    ) -> IssuedPlayerGrant:
        raise InvalidChallengeError

    async def start_fabric_challenge(self, *, java_uuid: UUID, pkce_s256_challenge: str) -> IssuedPlayerChallenge:
        raise InvalidChallengeError

    async def exchange_fabric(self, *, device_code: str, pkce_verifier: str) -> IssuedPlayerGrant:
        raise InvalidChallengeError

    async def approve(self, *, user_code: str, account_id: int) -> PlayerAuthorizationChallenge:
        raise InvalidChallengeError

    async def revoke_grant(self, *, grant_id: UUID, account_id: int) -> bool:
        raise InvalidChallengeError


class MockMediaJobs(MediaNormalizationJobService):
    """An empty media store: every lookup misses, which the routes report as a 404."""

    limits = MediaLimits()

    def __init__(self) -> None:
        """Hold no queue; `submit_staged` answers without enqueuing anything."""

    async def submit_staged(
        self,
        submission: StagedMediaUploadSubmission,
        *,
        authorization: MediaDraftUploadAuthorization | None = None,
    ) -> UUID:
        return submission.upload_id or uuid.uuid4()

    async def get(self, upload_id: UUID) -> MediaJobSnapshot | None:
        return None

    async def list_for_draft(self, draft_id: UUID) -> Sequence[MediaJobSnapshot]:
        return ()

    async def discard(self, draft_id: UUID, upload_id: UUID) -> bool:
        return False


class MockVotes(VoteService):
    def __init__(self) -> None:
        """Hold no vote sessions."""

    async def get_session_by_id(self, vote_session_id: int) -> VoteSessionSnapshot | None:
        return None


class MockRecords(RecordService):
    def __init__(self) -> None:
        """Publish no records."""

    async def get(self, result_id: int) -> PublishedRecord | None:
        return None

    async def list_page(
        self,
        *,
        selector: PageSelector = FIRST_PAGE,
        descending: bool = True,
        page_size: int = 20,
    ) -> Page[PublishedRecord]:
        return cast(Page[PublishedRecord], _EMPTY_PAGE)


class MockPublicRecords(PublicRecordQueryService):
    def __init__(self) -> None:
        """Publish no records."""

    async def get(self, standing_id: int) -> PublicRecordDetail | None:
        return None


class MockSubmissionForms(SubmissionFormService):
    def __init__(self) -> None:
        """Serve the built-in manifest without a taxonomy repository."""

    async def manifest(self, *, locale: str | None):
        return build_submission_manifest(locale)

    async def manifest_revision(self, schema_id: str, revision: int, *, locale: str | None):
        manifest = build_submission_manifest(locale)
        if manifest.schema_id == schema_id and manifest.revision == revision:
            return manifest
        return None

    async def options(self, source: str, category: str, *, locale: str | None) -> FormOptionSet:
        del locale
        return FormOptionSet(source, category, 1, ())


class MockIdempotency(IdempotencyService):
    """Reserves every key: no prior response is ever stored to replay."""

    def __init__(self) -> None:
        """Hold no store; reservations are invented per call."""

    async def reserve(
        self,
        *,
        caller: str,
        key: str,
        fingerprint: bytes,
        method: UnsafeHttpMethod,
        route: str,
    ) -> PendingRequest | StoredResponse:
        return PendingRequest(uuid.uuid4())

    async def complete(self, request: PendingRequest, response: StoredResponse) -> None:
        return None


class MockNotifications(NotificationService):
    def __init__(self) -> None:
        """Hold no repository; preferences are computed per call."""

    async def preferences(self, account_id: int) -> NotificationPreferences:
        return NotificationPreferences(account_id=account_id, consent_pending=True)

    async def set_preferences(self, account_id: int, *, web_enabled: bool, dm_enabled: bool) -> NotificationPreferences:
        return NotificationPreferences(
            account_id=account_id,
            consent_pending=False,
            web_enabled=web_enabled,
            dm_enabled=dm_enabled,
        )

    async def subscriptions(self, account_id: int) -> Sequence[NotificationSubscription]:
        return ()

    async def subscribe(
        self,
        account_id: int,
        *,
        kind: SubscriptionKind,
        subject_id: UUID | None = None,
        record_filter: RecordSubscriptionFilter | None = None,
    ) -> NotificationSubscription:
        raise AssertionError("service callers cannot create notification subscriptions")

    async def unsubscribe(self, account_id: int, subscription_id: int) -> None:
        return None

    async def inbox(
        self,
        account_id: int,
        *,
        selector: PageSelector = FIRST_PAGE,
        page_size: int = 20,
        visibility: InboxVisibility = DEFAULT_INBOX_VISIBILITY,
    ) -> Page[InboxNotification]:
        return cast(Page[InboxNotification], _EMPTY_PAGE)

    async def mark_read(
        self, account_id: int, notification_id: int, *, visibility: InboxVisibility = DEFAULT_INBOX_VISIBILITY
    ) -> None:
        return None


class EmptyPermissionStore:
    """No stored rules, and a fixed epoch."""

    async def load_for_subject(self, **_kwargs: object) -> SubjectRecords:
        return SubjectRecords(epoch=1)

    async def epoch(self) -> int:
        return 1


class MockPermissionEpoch(PermissionEpochWatcher):
    """A watcher with nothing to watch, so the lifespan's job is a no-op."""

    listener = None

    def __init__(self) -> None:
        """Watch no store and no cache."""

    async def refresh(self) -> None:
        return None


class MockSubmissionDrafts(SubmissionDraftService):
    """Owns no drafts: listings are empty and every addressed draft is missing."""

    def __init__(self) -> None:
        """Hold no repository; nothing here reaches one."""

    async def list_active(self, account_id: int, *, limit: int = 10) -> tuple[StoredDraft, ...]:
        return ()

    async def attention_inbox(
        self, actor: DraftActor, *, after: UUID | None = None, limit: int = 20
    ) -> tuple[StoredDraft, ...]:
        return ()

    async def get_accessible(self, draft_id: UUID, actor: DraftActor) -> StoredDraft:
        raise DraftNotFoundError(draft_id)

    async def delete(self, draft_id: UUID, account_id: int) -> None:
        raise DraftNotFoundError(draft_id)


class MockSubmissionFinalization(SubmissionFinalizationService):
    """No draft is finalizable, because no draft exists."""

    def __init__(self) -> None:
        """Hold no queue; nothing here enqueues an attempt."""

    async def status(self, draft_id: UUID, account_id: DraftActor) -> SubmissionRequestResult | None:
        raise DraftNotFoundError(draft_id)

    async def attempt(self, draft_id: UUID, account_id: DraftActor, attempt_id: UUID) -> FinalizationJobSnapshot | None:
        raise DraftNotFoundError(draft_id)

    async def attempts(
        self, draft_id: UUID, account_id: DraftActor, *, before: int | None = None, limit: int = 20
    ) -> tuple[FinalizationJobSnapshot, ...]:
        raise DraftNotFoundError(draft_id)


class MockSubmissionSchematics(DraftSchematicService):
    """Holds no private sources, so every draft-scoped read reports the draft missing."""

    def __init__(self) -> None:
        self.max_bytes = SCHEMATIC_FILE_SCHEMA_MAX_BYTES

    async def list(self, draft_id: UUID, actor: DraftActor) -> tuple[DraftSchematic, ...]:
        raise DraftNotFoundError(draft_id)

    async def reserve(self, draft_id: UUID, actor: DraftActor, *, filename: str, upload_id: UUID) -> DraftSchematic:
        raise DraftNotFoundError(draft_id)

    async def upload(
        self,
        draft_id: UUID,
        actor: DraftActor,
        *,
        filename: str,
        data: bytes,
        upload_id: UUID | None = None,
    ) -> DraftSchematic:
        raise DraftNotFoundError(draft_id)

    async def fail(self, draft_id: UUID, actor: DraftActor, upload_id: UUID) -> None:
        raise DraftNotFoundError(draft_id)

    async def select_primary(self, draft_id: UUID, actor: DraftActor, upload_id: UUID) -> None:
        raise DraftNotFoundError(draft_id)

    async def discard(self, draft_id: UUID, actor: DraftActor, upload_id: UUID) -> None:
        raise DraftNotFoundError(draft_id)

    async def attach(self, source_id: UUID, destination_id: UUID, actor: DraftActor, upload_id: UUID) -> None:
        raise DraftNotFoundError(destination_id)


class MockSubmissionRevisions(RevisionProposalService):
    """Retains no proposals, so listings are empty and every proposal id is unknown."""

    def __init__(self) -> None:
        """Hold no repository; nothing here reaches one."""

    async def list_for_source(self, source_message_id: int, actor: Subject) -> tuple[RevisionProposal, ...]:
        return ()

    async def get(self, proposal_id: UUID, actor: Subject) -> RevisionProposal:
        raise NotFoundError

    async def match(self, proposal_id: UUID, build_id: int, actor: Subject, *, renew: bool = False) -> RevisionProposal:
        raise NotFoundError

    async def approve(self, proposal_id: UUID, actor: Subject) -> RevisionProposal:
        raise NotFoundError


class MockSubmissionIntake(SubmissionAttachmentIntake):
    """Accepts no supplied attachments, because no draft exists to attach them to."""

    def __init__(self) -> None:
        """Hold no repository; nothing here reaches one."""

    async def list(self, draft_id: UUID, actor: DraftActor) -> tuple[SuppliedAttachment, ...]:
        raise DraftNotFoundError(draft_id)

    async def reserve(
        self,
        draft_id: UUID,
        actor: DraftActor,
        source_id: UUID,
        filename: str,
        content_type: str | None,
    ) -> SuppliedAttachment:
        raise DraftNotFoundError(draft_id)

    async def register_file(
        self,
        draft_id: UUID,
        actor: DraftActor,
        source_id: UUID,
        path: Path,
        content_type: str | None,
    ) -> None:
        raise DraftNotFoundError(draft_id)

    async def discard(self, draft_id: UUID, actor: DraftActor, source_id: UUID) -> None:
        raise DraftNotFoundError(draft_id)

    async def fail(self, draft_id: UUID, actor: DraftActor, source_id: UUID) -> None:
        raise DraftNotFoundError(draft_id)


class MockSubmissionInference(SubmissionInferenceRuns):
    """Present only to complete the graph: no HTTP route reads inference runs."""

    def __init__(self) -> None:
        """Hold no repository; the API never calls this collaborator."""


async def keep_database_active() -> None:
    """Represent a healthy database keepalive without a mock call boundary."""


def build_services(
    *,
    web_auth: WebSessionService | None = None,
    cli_authorization: CliAuthorizationService | None = None,
    idempotency: IdempotencyService | None = None,
    accounts: AccountService | None = None,
    error_reports: ErrorReportService | None = None,
) -> ApiServices:
    """Build the full service graph from in-memory doubles.

    Constructing the real `ApiServices` is the point: a field added to the graph fails
    here at type-check time instead of surfacing as an `AttributeError` in whichever
    route reads it first.
    """
    return ApiServices(
        api_keys=None,
        web_auth=web_auth,
        cli_authorization=cli_authorization or MockCliAuthorization(),
        minecraft_installations=MockMinecraftInstallations(),
        minecraft_player_authorization=MockMinecraftPlayerAuthorization(),
        idempotency=idempotency or MockIdempotency(),
        notifications=MockNotifications(),
        builds=MockBuilds(),
        accounts=accounts or MockAccountManager(),
        build_queries=MockBuildQueries(),
        # The real service over an empty store, not a double: every node then falls to
        # its catalogue default, which is the answer production gives an unruled subject.
        permissions=PermissionService(EmptyPermissionStore()),
        permission_epoch=MockPermissionEpoch(),
        search=MockSearch(),
        # The real service over an empty registry, not a mock: every source id is
        # then unknown, which is the 404 the route promises rather than a 500.
        suggestions=SuggestionService(SuggestionRegistry.of(())),
        tags=MockTags(),
        versions=MockVersions(),
        schematics=MockSchematics(),
        votes=MockVotes(),
        vote_members=None,
        records=MockRecords(),
        public_records=MockPublicRecords(),
        submission_forms=MockSubmissionForms(),
        submission_drafts=MockSubmissionDrafts(),
        submission_finalization=MockSubmissionFinalization(),
        submission_schematics=MockSubmissionSchematics(),
        submission_revisions=MockSubmissionRevisions(),
        submission_intake=MockSubmissionIntake(),
        submission_inference=MockSubmissionInference(),
        media_jobs=MockMediaJobs(),
        error_reports=error_reports or MockErrorReports(),
    )


def build_app(
    *,
    web_auth: WebSessionService | None = None,
    cli_authorization: CliAuthorizationService | None = None,
    idempotency: IdempotencyService | None = None,
    accounts: AccountService | None = None,
    error_reports: ErrorReportService | None = None,
    config: ApiProcessConfig = TEST_CONFIG,
) -> tuple[FastAPI, MockDatabaseManager]:
    """Build the API app wired to in-memory fakes instead of real infrastructure."""
    database = MockDatabaseManager()
    services = build_services(
        web_auth=web_auth,
        cli_authorization=cli_authorization,
        idempotency=idempotency,
        accounts=accounts,
        error_reports=error_reports,
    )
    runtime = ApplicationRuntime(services, database.close, keep_database_active)
    return create_api_app(lambda _config: runtime, config=config), database
