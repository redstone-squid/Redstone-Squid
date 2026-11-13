"""Transport-test services that answer from memory without external infrastructure.

Service subclasses retain override signature checking. ``build_services`` constructs a
real ``ApiServices`` graph so required constructor fields remain checked. Unoverridden
methods still inherit production behavior; each test must supply the methods its routes use.
"""

import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import cast, override
from uuid import UUID

from litestar import Litestar

from squid.accounts.application import AccountService
from squid.accounts.domain import CreatorProfile
from squid.accounts.domain.profiles import PublicCreatorProfile
from squid.accounts.errors import MinecraftAccountNotFoundError
from squid.accounts.models import AccountIdentity, AccountProfile, CreatorAlias
from squid.api.app import create_api_app
from squid.auth.application.web import WebSessionService
from squid.builds.application import BuildQueryService, BuildService
from squid.builds.application.editing import BuildEditPatch
from squid.builds.application.queries import DEFAULT_BUILD_LIST_SORT, BuildListSort
from squid.builds.application.services import BuildEditor
from squid.builds.domain.models import Status
from squid.builds.errors import BuildNotFoundError
from squid.builds.models import Build
from squid.cli_auth.application import CliAuthorizationService
from squid.cli_auth.domain import CliIdentity, IssuedCliEnrollment, IssuedCliSession, IssuedCliSessionChallenge
from squid.cli_auth.errors import InvalidCliEnrollmentError
from squid.cli_auth.models import CliDevice, CliDeviceEnrollment
from squid.config import ApiProcessConfig
from squid.core.actors import Actor
from squid.core.errors import NotFoundError
from squid.core.pagination import FIRST_PAGE, Page, PageSelector
from squid.diagnostics.application import ErrorReportService
from squid.edits.application import EditRunner
from squid.edits.domain import EditCommand, EditResult
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
    PublicServerProfile,
)
from squid.minecraft_auth.errors import InvalidChallengeError, InvalidInstallationCredentialError
from squid.minecraft_auth.models import PaperInstallation, PlayerAuthorizationChallenge
from squid.notifications import NotificationPreferences, NotificationService
from squid.notifications.domain import (
    DEFAULT_INBOX_VISIBILITY,
    InboxVisibility,
)
from squid.notifications.models import InboxNotification, NotificationSubscription
from squid.permissions.application import PermissionService, SubjectRecords
from squid.permissions.application.epoch import PermissionEpochWatcher, WakeListener
from squid.permissions.domain import Pattern
from squid.records.application import PublicRecordQueryService, RecordService
from squid.records.application.models import PublicRecordDetail, PublishedTitle
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
from squid.submissions.application.recalculation import RecalculationProposalService
from squid.submissions.application.recalculation_values import RecalculationProposal
from squid.submissions.application.schematics import DraftSchematicService
from squid.submissions.domain.schematics import DraftSchematic
from squid.submissions.errors import DraftNotFoundError
from squid.suggestions.application import SuggestionRegistry, SuggestionService
from squid.tags.application import TagService
from squid.tags.models import TagDefinition
from squid.versions.application.services import VersionService
from squid.voting.application import VoteService
from squid.voting.domain.models import VoteSessionSnapshot
from tests.support.permissions import permission_uow_factory
from tests.support.settings import RuntimeSettingsStub


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
            # Grant the relay node explicitly; the bootstrap secret has no blanket permission.
            "secret_nodes": ["account.verify.relay"],
        },
        "cli_auth": {
            "pepper": "cli-authorization-pepper-for-tests-32-bytes",
            "verification_uri": "https://catalogue.example/cli/link",
        },
        # Configure device-flow routes so tests reach their handlers instead of the disabled-service 503.
        "minecraft_auth": {
            "pepper": "minecraft-authorization-pepper-for-tests",
            "verification_uri": "https://catalogue.example/minecraft/link",
        },
    }
)


class MockAccountManager(AccountService):
    def __init__(self) -> None:
        """Answer from nothing; no repository is attached."""

    @override
    async def get_creator_alias(self, name: str) -> CreatorAlias | None:
        return None

    @override
    async def get_creator_profile(self, public_id: UUID) -> CreatorProfile | None:
        return None

    @override
    async def get_public_profile(self, public_id: UUID) -> PublicCreatorProfile | None:
        return None

    @override
    async def get_profile(self, account_id: int) -> AccountProfile:
        return AccountProfile.empty(account_id)

    @override
    async def list_identities(self, account_id: int) -> tuple[AccountIdentity, ...]:
        return ()

    @override
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
    """Reject CLI authorization operations with a client-safe error.

    Explicit overrides keep method signatures checked against the production service.
    """

    def __init__(self) -> None:
        """Enroll nothing; no repository or clock is attached."""

    @override
    async def authenticate(self, token: str) -> CliIdentity:
        raise InvalidCliEnrollmentError

    @override
    async def start_enrollment(self, *, public_key: bytes, client_instance_id: UUID, label: str) -> IssuedCliEnrollment:
        raise InvalidCliEnrollmentError

    @override
    async def exchange_enrollment(self, *, device_code: str, signature: bytes) -> IssuedCliSession:
        raise InvalidCliEnrollmentError

    @override
    async def preview_enrollment(self, user_code: str) -> CliDeviceEnrollment:
        raise InvalidCliEnrollmentError

    @override
    async def approve_enrollment(self, *, user_code: str, account_id: int) -> CliDeviceEnrollment:
        raise InvalidCliEnrollmentError

    @override
    async def start_session_challenge(self, device_id: UUID) -> IssuedCliSessionChallenge:
        raise InvalidCliEnrollmentError

    @override
    async def exchange_session_challenge(
        self, *, device_id: UUID, challenge_id: UUID, nonce: str, signature: bytes
    ) -> IssuedCliSession:
        raise InvalidCliEnrollmentError

    @override
    async def list_devices(self, account_id: int) -> tuple[CliDevice, ...]:
        raise InvalidCliEnrollmentError

    @override
    async def revoke_device(self, *, device_id: UUID, account_id: int) -> bool:
        raise InvalidCliEnrollmentError

    @override
    async def revoke_current_session(self, identity: CliIdentity) -> bool:
        raise InvalidCliEnrollmentError


_EMPTY_PAGE: Page[object] = Page(items=(), total=0, next=None, prev=None)
"""Empty query result with the pagination metadata required by ``Page.from_domain``."""


class MockEditRunner(EditRunner):
    """No persisted targets exist in the default transport-only application."""

    @override
    async def execute(self, command: EditCommand, actor: Actor) -> EditResult:
        raise NotFoundError


class MockBuilds(BuildService):
    """Refuses every edit: no build exists to edit in a transport-only app."""

    def __init__(self) -> None:
        """Hold no repository; `apply_edit` never reaches one."""

    @override
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

    @override
    async def get(self, build_id: int) -> Build | None:
        return None

    @override
    async def get_public(self, build_id: int) -> Build:
        raise BuildNotFoundError(build_id)

    @override
    async def get_many(self, build_ids: Sequence[int]) -> list[Build]:
        return []

    @override
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

    @override
    async def search(self, request: SearchRequest) -> SearchPage:
        return SearchPage(hits=(), total=0, next=None, prev=None)

    @override
    async def suggest(self, query: str | SearchQuery, *, limit: int = 5) -> tuple[str, ...]:
        return ()

    @override
    async def fields(self):
        return DEFAULT_FIELD_REGISTRY


class MockTags(TagService):
    def __init__(self) -> None:
        """Publish no tags."""

    @override
    async def public_definitions(self) -> tuple[TagDefinition, ...]:
        return ()

    @override
    async def public_definition(self, tag_id: int) -> TagDefinition | None:
        return None


class MockVersions(VersionService):
    def __init__(self) -> None:
        """Know no game versions."""

    @override
    async def list_all(self):
        return []


class MockErrorReports(ErrorReportService):
    """Records what the exception handlers captured, without a database."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    @override
    async def record(self, error: BaseException, **kwargs: object) -> None:
        self.calls.append({"error": error, **kwargs})


class MockSchematics(SchematicService):
    """An empty schematic store: listings are empty and every content read misses."""

    def __init__(self) -> None:
        """Attach no analyzer or repository; nothing here reaches one."""

    @override
    async def list_public_page(
        self, build_id: int, *, selector: PageSelector = FIRST_PAGE, page_size: int = 50
    ) -> Page[StoredSchematic]:
        return cast(Page[StoredSchematic], _EMPTY_PAGE)

    @override
    async def public_download(self, build_id: int, schematic_id: int) -> PublicSchematicDownload:
        raise SchematicNotFoundError

    @override
    async def render_now(self, build_id: int, *, request: RenderRequest | None = None) -> RenderedSchematic:
        raise SchematicNotFoundError

    @override
    async def render_content(self, recipe_hash: str, *, max_bytes: int = 8 * 1024 * 1024) -> bytes:
        raise SchematicNotFoundError


class MockMinecraftInstallations(InstallationCredentialService):
    """Fail closed with a client-safe error for generated contract requests."""

    def __init__(self) -> None:
        """Register no installations; no repository or pepper is attached."""

    @override
    async def authenticate_headers(
        self, installation_id: str | None, installation_secret: str | None
    ) -> AuthenticatedPaperInstallation:
        raise InvalidInstallationCredentialError

    @override
    async def register(
        self, *, owner_account_id: int, label: str, profile: PublicServerProfile | None = None
    ) -> IssuedInstallationCredential:
        raise InvalidInstallationCredentialError

    @override
    async def list_owned(self, owner_account_id: int) -> tuple[PaperInstallation, ...]:
        raise InvalidInstallationCredentialError

    @override
    async def rotate(self, *, installation_id: UUID, owner_account_id: int) -> IssuedInstallationCredential:
        raise InvalidInstallationCredentialError

    @override
    async def update_profile(
        self, *, installation_id: UUID, owner_account_id: int, profile: PublicServerProfile
    ) -> PaperInstallation:
        raise InvalidInstallationCredentialError

    @override
    async def revoke(self, *, installation_id: UUID, owner_account_id: int) -> PaperInstallation:
        raise InvalidInstallationCredentialError


class MockMinecraftPlayerAuthorization(PlayerAuthorizationService):
    """Fail closed with a client-safe error for generated contract requests."""

    def __init__(self) -> None:
        """Issue no challenges; no repository or clock is attached."""

    @override
    async def authenticate_fabric_player(self, token: str) -> MinecraftPlayerContext:
        raise InvalidChallengeError

    @override
    async def authenticate_paper_player(
        self, token: str, installation: AuthenticatedPaperInstallation
    ) -> MinecraftPlayerContext:
        raise InvalidChallengeError

    @override
    async def start_paper_challenge(
        self, *, installation: AuthenticatedPaperInstallation, java_uuid: UUID
    ) -> IssuedPlayerChallenge:
        raise InvalidChallengeError

    @override
    async def exchange_paper(
        self, *, device_code: str, installation: AuthenticatedPaperInstallation
    ) -> IssuedPlayerGrant:
        raise InvalidChallengeError

    @override
    async def start_fabric_challenge(self, *, java_uuid: UUID, pkce_s256_challenge: str) -> IssuedPlayerChallenge:
        raise InvalidChallengeError

    @override
    async def exchange_fabric(self, *, device_code: str, pkce_verifier: str) -> IssuedPlayerGrant:
        raise InvalidChallengeError

    @override
    async def approve(self, *, user_code: str, account_id: int) -> PlayerAuthorizationChallenge:
        raise InvalidChallengeError

    @override
    async def revoke_grant(self, *, grant_id: UUID, account_id: int) -> bool:
        raise InvalidChallengeError


class MockMediaJobs(MediaNormalizationJobService):
    """An empty media store: every lookup misses, which the routes report as a 404."""

    def __init__(self) -> None:
        """Hold no queue; `submit_staged` answers without enqueuing anything."""

    @property
    @override
    def limits(self) -> MediaLimits:
        return MediaLimits()

    @override
    async def submit_staged(
        self,
        submission: StagedMediaUploadSubmission,
        *,
        authorization: MediaDraftUploadAuthorization | None = None,
    ) -> UUID:
        return submission.upload_id or uuid.uuid4()

    @override
    async def get(self, upload_id: UUID) -> MediaJobSnapshot | None:
        return None

    @override
    async def list_for_draft(self, draft_id: UUID) -> Sequence[MediaJobSnapshot]:
        return ()

    @override
    async def discard(self, draft_id: UUID, upload_id: UUID) -> bool:
        return False


class MockVotes(VoteService):
    def __init__(self) -> None:
        """Hold no vote sessions."""

    @override
    async def get_session(self, vote_session_id: int) -> VoteSessionSnapshot | None:
        return None


class MockRecords(RecordService):
    def __init__(self) -> None:
        """Publish no records."""

    @override
    async def get(self, result_id: int) -> PublishedTitle | None:
        return None

    @override
    async def list_page(
        self,
        *,
        selector: PageSelector = FIRST_PAGE,
        descending: bool = True,
        page_size: int = 20,
        resolved_only: bool = False,
    ) -> Page[PublishedTitle]:
        return cast(Page[PublishedTitle], _EMPTY_PAGE)


class MockPublicRecords(PublicRecordQueryService):
    def __init__(self) -> None:
        """Publish no records."""

    @override
    async def get(self, result_id: int) -> PublicRecordDetail | None:
        return None


class MockSubmissionForms(SubmissionFormService):
    def __init__(self) -> None:
        """Serve the built-in manifest without a taxonomy repository."""

    @override
    async def manifest(self, *, locale: str | None):
        return build_submission_manifest(locale)

    @override
    async def manifest_revision(self, schema_id: str, revision: int, *, locale: str | None):
        manifest = build_submission_manifest(locale)
        if manifest.schema_id == schema_id and manifest.revision == revision:
            return manifest
        return None

    @override
    async def options(self, source: str, category: str, *, locale: str | None) -> FormOptionSet:
        del locale
        return FormOptionSet(source, category, 1, ())


class MockIdempotency(IdempotencyService):
    """Reserves every key: no prior response is ever stored to replay."""

    def __init__(self) -> None:
        """Hold no store; reservations are invented per call."""

    @override
    async def reserve(
        self,
        *,
        caller: str,
        key: str,
        fingerprint: bytes,
        method: UnsafeHttpMethod,
        route: str,
        recoverable: bool = False,
    ) -> PendingRequest | StoredResponse:
        return PendingRequest(uuid.uuid4())

    @override
    async def complete(self, request: PendingRequest, response: StoredResponse) -> StoredResponse:
        return response


class MockNotifications(NotificationService):
    def __init__(self) -> None:
        """Hold no repository; preferences are computed per call."""

    @override
    async def preferences(self, account_id: int) -> NotificationPreferences:
        return NotificationPreferences(account_id=account_id, consent_pending=True)

    @override
    async def subscriptions(self, account_id: int) -> Sequence[NotificationSubscription]:
        return ()

    @override
    async def inbox(
        self,
        account_id: int,
        *,
        selector: PageSelector = FIRST_PAGE,
        page_size: int = 20,
        visibility: InboxVisibility = DEFAULT_INBOX_VISIBILITY,
    ) -> Page[InboxNotification]:
        return cast(Page[InboxNotification], _EMPTY_PAGE)


class EmptyPermissionStore:
    """No stored rules, and a fixed epoch."""

    async def load_for_subject(self, **_kwargs: object) -> SubjectRecords:
        return SubjectRecords(epoch=1)

    async def epoch(self) -> int:
        return 1


class MockPermissionEpoch(PermissionEpochWatcher):
    """A watcher with nothing to watch, so the lifespan's job is a no-op."""

    def __init__(self) -> None:
        """Watch no store and no cache."""

    @property
    @override
    def listener(self) -> WakeListener | None:
        return None

    @override
    async def refresh(self) -> None:
        return None


class MockSubmissionDrafts(SubmissionDraftService):
    """Owns no drafts: listings are empty and every addressed draft is missing."""

    def __init__(self) -> None:
        """Hold no repository; nothing here reaches one."""

    @override
    async def list_active(self, account_id: int, *, limit: int = 10) -> tuple[StoredDraft, ...]:
        return ()

    @override
    async def attention_inbox(
        self, actor: DraftActor, *, after: UUID | None = None, limit: int = 20
    ) -> tuple[StoredDraft, ...]:
        return ()

    @override
    async def get_accessible(self, draft_id: UUID, actor: DraftActor) -> StoredDraft:
        raise DraftNotFoundError(draft_id)

    @override
    async def delete(self, draft_id: UUID, account_id: int) -> None:
        raise DraftNotFoundError(draft_id)


class MockSubmissionFinalization(SubmissionFinalizationService):
    """No draft is finalizable, because no draft exists."""

    def __init__(self) -> None:
        """Hold no queue; nothing here enqueues an attempt."""

    @override
    async def status(self, draft_id: UUID, account_id: DraftActor) -> SubmissionRequestResult | None:
        raise DraftNotFoundError(draft_id)

    @override
    async def attempt(self, draft_id: UUID, account_id: DraftActor, attempt_id: UUID) -> FinalizationJobSnapshot | None:
        raise DraftNotFoundError(draft_id)

    @override
    async def attempts(
        self, draft_id: UUID, account_id: DraftActor, *, before: int | None = None, limit: int = 20
    ) -> tuple[FinalizationJobSnapshot, ...]:
        raise DraftNotFoundError(draft_id)


class MockSubmissionSchematics(DraftSchematicService):
    """Holds no private sources, so every draft-scoped read reports the draft missing."""

    def __init__(self) -> None:
        self.max_bytes = SCHEMATIC_FILE_SCHEMA_MAX_BYTES

    @override
    async def list(self, draft_id: UUID, actor: DraftActor) -> tuple[DraftSchematic, ...]:
        raise DraftNotFoundError(draft_id)

    @override
    async def reserve(self, draft_id: UUID, actor: DraftActor, *, filename: str, upload_id: UUID) -> DraftSchematic:
        raise DraftNotFoundError(draft_id)

    @override
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

    @override
    async def fail(self, draft_id: UUID, actor: DraftActor, upload_id: UUID) -> None:
        raise DraftNotFoundError(draft_id)

    @override
    async def select_primary(self, draft_id: UUID, actor: DraftActor, upload_id: UUID) -> None:
        raise DraftNotFoundError(draft_id)

    @override
    async def discard(self, draft_id: UUID, actor: DraftActor, upload_id: UUID) -> None:
        raise DraftNotFoundError(draft_id)

    @override
    async def attach(self, source_id: UUID, destination_id: UUID, actor: DraftActor, upload_id: UUID) -> None:
        raise DraftNotFoundError(destination_id)


class MockSubmissionRevisions(RecalculationProposalService):
    """Retains no proposals, so listings are empty and every proposal id is unknown."""

    def __init__(self) -> None:
        """Hold no repository; nothing here reaches one."""

    @override
    async def list_for_source(self, source_message_id: int, actor: Actor) -> tuple[RecalculationProposal, ...]:
        return ()

    @override
    async def get(self, proposal_id: UUID, actor: Actor) -> RecalculationProposal:
        raise NotFoundError

    @override
    async def match(
        self, proposal_id: UUID, build_id: int, actor: Actor, *, renew: bool = False
    ) -> RecalculationProposal:
        raise NotFoundError

    @override
    async def approve(self, proposal_id: UUID, actor: Actor) -> RecalculationProposal:
        raise NotFoundError


class MockSubmissionIntake(SubmissionAttachmentIntake):
    """Accepts no supplied attachments, because no draft exists to attach them to."""

    def __init__(self) -> None:
        """Hold no repository; nothing here reaches one."""

    @override
    async def list(self, draft_id: UUID, actor: DraftActor) -> tuple[SuppliedAttachment, ...]:
        raise DraftNotFoundError(draft_id)

    @override
    async def reserve(
        self,
        draft_id: UUID,
        actor: DraftActor,
        source_id: UUID,
        filename: str,
        content_type: str | None,
    ) -> SuppliedAttachment:
        raise DraftNotFoundError(draft_id)

    @override
    async def register_file(
        self,
        draft_id: UUID,
        actor: DraftActor,
        source_id: UUID,
        path: Path,
        content_type: str | None,
    ) -> None:
        raise DraftNotFoundError(draft_id)

    @override
    async def discard(self, draft_id: UUID, actor: DraftActor, source_id: UUID) -> None:
        raise DraftNotFoundError(draft_id)

    @override
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
    """Build the complete API graph with empty stores and injectable service replacements.

    The real permission service resolves catalogue defaults; the empty suggestion registry
    rejects unknown sources. Other services use the transport fakes above.
    """
    return ApiServices(
        settings=RuntimeSettingsStub(),
        edits=MockEditRunner(),
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
        # Empty stored rules exercise the permission catalogue defaults.
        permissions=PermissionService(uow_factory=permission_uow_factory(EmptyPermissionStore())),
        permission_epoch=MockPermissionEpoch(),
        search=MockSearch(),
        # An empty registry preserves the service's unknown-source error.
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
        submission_recalculations=MockSubmissionRevisions(),
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
) -> tuple[Litestar, MockDatabaseManager]:
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
