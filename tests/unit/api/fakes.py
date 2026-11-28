"""Transport-test services that answer from memory without external infrastructure.

Service subclasses retain override signature checking. ``build_services`` constructs a
real ``ApiServices`` graph so required constructor fields remain checked. Unoverridden
methods still inherit production behavior; each test must supply the methods its routes use.
"""

import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import cast, override
from unittest.mock import create_autospec
from uuid import UUID

from litestar import Litestar

from squid.api.app import create_api_app
from squid.config import ApiProcessConfig
from squid.core.accounts.application import AccountService
from squid.core.accounts.domain import CreatorProfile
from squid.core.accounts.domain.profiles import PublicCreatorProfile
from squid.core.accounts.errors import MinecraftAccountNotFoundError
from squid.core.accounts.models import AccountIdentity, AccountProfile, CreatorAlias
from squid.core.actors import ActingContext
from squid.core.auth.application.viewer import ViewerQueries
from squid.core.auth.application.web import WebSessionService
from squid.core.auth.impersonation import ImpersonationService
from squid.core.builds.application import BuildQueryService, BuildService
from squid.core.builds.application.editing import BuildEditPatch
from squid.core.builds.application.queries import BuildListSort
from squid.core.builds.domain import EMPTY_BUILD_FILTERS, BuildListFilters
from squid.core.builds.domain.models import BuildStatus
from squid.core.builds.errors import BuildNotFoundError
from squid.core.builds.models import Build
from squid.core.catalogue.application import CatalogueStats, CatalogueStatsService
from squid.core.cli_auth.application import CliAuthorizationService
from squid.core.cli_auth.domain import CliIdentity, IssuedCliEnrollment, IssuedCliSession, IssuedCliSessionChallenge
from squid.core.cli_auth.errors import InvalidCliEnrollmentError
from squid.core.cli_auth.models import CliDevice, CliDeviceEnrollment
from squid.core.diagnostics.application import ErrorReportService
from squid.core.edits.application import EditRunner
from squid.core.edits.domain import EditCommand, EditResult
from squid.core.errors import NotFoundError
from squid.core.idempotency import IdempotencyService, PendingRequest
from squid.core.idempotency.domain import StoredResponse, UnsafeHttpMethod
from squid.core.media.application.jobs import (
    MediaDraftUploadAuthorization,
    MediaJobSnapshot,
    MediaProcessingJobService,
    StagedMediaUploadSubmission,
)
from squid.core.media.domain import MediaLimits
from squid.core.minecraft_auth.application import InstallationCredentialService, PlayerAuthorizationService
from squid.core.minecraft_auth.domain.models import (
    AuthenticatedPaperInstallation,
    IssuedInstallationCredential,
    IssuedPlayerChallenge,
    IssuedPlayerGrant,
    MinecraftPlayerContext,
    PublicServerProfile,
    PublishedPaperServer,
)
from squid.core.minecraft_auth.errors import InvalidChallengeError, InvalidInstallationCredentialError
from squid.core.minecraft_auth.models import PaperInstallation, PlayerAuthorizationChallenge
from squid.core.notifications import NotificationPreferences, NotificationService
from squid.core.notifications.application import Inbox
from squid.core.notifications.models import NotificationSubscription
from squid.core.pagination import FIRST_PAGE, Page, PageSelector, offset_page
from squid.core.permissions.application import PermissionAdministrationService, PermissionService, SubjectRecords
from squid.core.permissions.application.epoch import PermissionEpochWatcher, WakeListener
from squid.core.permissions.application.ports import PermissionSubject
from squid.core.permissions.domain import Pattern
from squid.core.records.application import PublicRecordQueryService, RecordAdministrationService, RecordService
from squid.core.records.application.models import (
    ALL_RECORDS,
    PublicRecordDetail,
    PublicRecordSummary,
    PublishedRecordResult,
    RecordListFilters,
    RecordListSort,
)
from squid.core.records.application.range_publication import RangePublicationService
from squid.core.records.application.range_snapshots import RangePublication
from squid.core.records.domain import BuildKind
from squid.core.schematics.application import SchematicService
from squid.core.schematics.application.attachments import PublicSchematicDownload, StoredSchematic
from squid.core.schematics.application.commands import RenderRequest
from squid.core.schematics.application.previews import RenderedSchematic
from squid.core.schematics.domain import SCHEMATIC_FILE_SCHEMA_MAX_BYTES
from squid.core.schematics.errors import SchematicNotFoundError
from squid.core.search.application import SearchService
from squid.core.search.application.fields import DEFAULT_FIELD_REGISTRY
from squid.core.search.application.results import SearchPage
from squid.core.search.domain import SearchRequest
from squid.core.search.domain.query import SearchQuery
from squid.core.submissions.application import FormOptionSet, SubmissionFormService, build_submission_manifest
from squid.core.submissions.application.decisions import SubmissionReviews
from squid.core.submissions.application.drafts import StoredDraft, SubmissionDraftService
from squid.core.submissions.application.finalization import (
    FinalizationJobSnapshot,
    SubmissionFinalizationService,
    SubmissionRequestResult,
)
from squid.core.submissions.application.inference_runs import SubmissionInferenceRuns
from squid.core.submissions.application.intake import SubmissionAttachmentIntake, SuppliedAttachment
from squid.core.submissions.application.recalculation import RecalculationProposalService
from squid.core.submissions.application.recalculation_values import RecalculationProposal
from squid.core.submissions.application.schematics import DraftSchematicService
from squid.core.submissions.domain.schematics import DraftSchematic
from squid.core.submissions.errors import DraftNotFoundError
from squid.core.suggestions.application import SuggestionRegistry, SuggestionService
from squid.core.tags.application import TagService
from squid.core.tags.models import TagDefinition
from squid.core.versions.application.services import VersionService
from squid.core.voting.application import VoteService
from squid.core.voting.domain.models import VoteSessionSnapshot
from squid.runtime import ApiServices, ApplicationRuntime
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
    async def get_creator_alias(self, alias: int | str, *, context: ActingContext) -> CreatorAlias | None:
        return None

    @override
    async def get_creator_profile(self, public_id: UUID, *, context: ActingContext) -> CreatorProfile | None:
        return None

    @override
    async def get_public_profile(self, public_id: UUID, *, context: ActingContext) -> PublicCreatorProfile | None:
        return None

    @override
    async def get_profile(self, account_id: int, *, context: ActingContext) -> AccountProfile:
        return AccountProfile.empty(account_id)

    @override
    async def list_identities(self, *, context: ActingContext) -> tuple[AccountIdentity, ...]:
        return ()

    @override
    async def generate_verification_code(self, minecraft_uuid: UUID, *, context: ActingContext) -> int:
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
    async def preview_enrollment(self, user_code: str, *, context: ActingContext) -> CliDeviceEnrollment:
        raise InvalidCliEnrollmentError

    @override
    async def approve_enrollment(self, *, context: ActingContext, user_code: str) -> CliDeviceEnrollment:
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
    async def list_devices(self, *, context: ActingContext) -> tuple[CliDevice, ...]:
        raise InvalidCliEnrollmentError

    @override
    async def revoke_device(self, *, context: ActingContext, device_id: UUID) -> bool:
        raise InvalidCliEnrollmentError

    @override
    async def revoke_current_session(self, identity: CliIdentity, *, context: ActingContext) -> bool:
        raise InvalidCliEnrollmentError


_EMPTY_PAGE: Page[object] = Page(items=(), total=0, next=None, prev=None)
"""Empty query result with the pagination metadata required by ``Page.from_domain``."""


class MockEditRunner(EditRunner):
    """No persisted targets exist in the default transport-only application."""

    @override
    async def execute(self, command: EditCommand, *, context: ActingContext) -> EditResult:
        raise NotFoundError


class MockBuilds(BuildService):
    """Refuses every edit: no build exists to edit in a transport-only app."""

    def __init__(self) -> None:
        """Hold no repository; `apply_edit` never reaches one."""

    @override
    async def apply_edit(
        self,
        build_id: int,
        patch: BuildEditPatch,
        *,
        expected_revision: int | None = None,
        source: str = "build_edit",
        context: ActingContext,
    ) -> Build:
        raise BuildNotFoundError(build_id)


class MockBuildQueries(BuildQueryService):
    def __init__(self) -> None:
        """Answer every read as a miss; no repository is attached."""

    @override
    async def get(self, build_id: int, *, context: ActingContext) -> Build | None:
        return None

    @override
    async def get_public(self, build_id: int, *, context: ActingContext) -> Build:
        raise BuildNotFoundError(build_id)

    @override
    async def get_many(self, build_ids: Sequence[int], *, context: ActingContext) -> list[Build]:
        return []

    @override
    async def list_page(
        self,
        *,
        context: ActingContext,
        statuses: frozenset[BuildStatus],
        filters: BuildListFilters = EMPTY_BUILD_FILTERS,
        submitter_account_id: int | None = None,
        sort: BuildListSort = BuildListSort.ID,
        selector: PageSelector = FIRST_PAGE,
        page_size: int = 20,
    ) -> Page[Build]:
        return cast(Page[Build], _EMPTY_PAGE)


class MockSearch(SearchService):
    def __init__(self) -> None:
        """Index nothing, so every query is an empty page."""

    @override
    async def search(self, request: SearchRequest, *, context: ActingContext) -> SearchPage:
        return SearchPage(hits=(), total=0, next=None, prev=None)

    @override
    async def suggest(self, query: str | SearchQuery, *, limit: int = 5, context: ActingContext) -> tuple[str, ...]:
        return ()

    @override
    async def fields(self, *, context: ActingContext):
        return DEFAULT_FIELD_REGISTRY


class MockTags(TagService):
    def __init__(self) -> None:
        """Publish no tags."""

    @override
    async def public_definitions(self, *, context: ActingContext) -> tuple[TagDefinition, ...]:
        return ()

    @override
    async def public_definition(self, tag_id: int | str, *, context: ActingContext) -> TagDefinition | None:
        return None


class MockVersions(VersionService):
    def __init__(self) -> None:
        """Know no game versions."""

    @override
    async def list_all(self, *, context: ActingContext):
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
    async def list_public_for_build(self, build_id: int, *, context: ActingContext) -> list[StoredSchematic]:
        return []

    @override
    async def list_public_page(
        self, build_id: int, *, context: ActingContext, selector: PageSelector = FIRST_PAGE, page_size: int = 50
    ) -> Page[StoredSchematic]:
        return cast(Page[StoredSchematic], _EMPTY_PAGE)

    @override
    async def public_download(
        self, build_id: int, schematic_id: int, *, context: ActingContext
    ) -> PublicSchematicDownload:
        raise SchematicNotFoundError

    @override
    async def render_now(
        self, build_id: int, *, context: ActingContext, request: RenderRequest | None = None
    ) -> RenderedSchematic:
        raise SchematicNotFoundError

    @override
    async def render_content(
        self, recipe_hash: str, *, context: ActingContext, max_bytes: int = 8 * 1024 * 1024
    ) -> bytes:
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
        self, *, context: ActingContext, label: str, profile: PublicServerProfile | None = None
    ) -> IssuedInstallationCredential:
        raise InvalidInstallationCredentialError

    @override
    async def list_owned(self, *, context: ActingContext) -> tuple[PaperInstallation, ...]:
        raise InvalidInstallationCredentialError

    @override
    async def public_servers(self, *, context: ActingContext) -> tuple[PublishedPaperServer, ...]:
        return ()

    @override
    async def rotate(self, *, context: ActingContext, installation_id: UUID) -> IssuedInstallationCredential:
        raise InvalidInstallationCredentialError

    @override
    async def update_profile(
        self, *, context: ActingContext, installation_id: UUID, profile: PublicServerProfile
    ) -> PaperInstallation:
        raise InvalidInstallationCredentialError

    @override
    async def revoke(self, *, context: ActingContext, installation_id: UUID) -> PaperInstallation:
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
    async def approve(self, *, context: ActingContext, user_code: str) -> PlayerAuthorizationChallenge:
        raise InvalidChallengeError

    @override
    async def revoke_grant(self, *, context: ActingContext, grant_id: UUID) -> bool:
        raise InvalidChallengeError


class MockMediaJobs(MediaProcessingJobService):
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
        context: ActingContext,
        authorization: MediaDraftUploadAuthorization | None = None,
    ) -> UUID:
        return submission.upload_id or uuid.uuid4()

    @override
    async def get(self, upload_id: UUID, *, context: ActingContext) -> MediaJobSnapshot | None:
        return None

    @override
    async def list_for_draft(self, draft_id: UUID, *, context: ActingContext) -> Sequence[MediaJobSnapshot]:
        return ()

    @override
    async def discard(self, draft_id: UUID, upload_id: UUID, *, context: ActingContext) -> bool:
        return False


class MockVotes(VoteService):
    def __init__(self) -> None:
        """Hold no vote sessions."""

    @override
    async def get_session(self, vote_session_id: int, *, context: ActingContext) -> VoteSessionSnapshot | None:
        return None


class MockRangePublication(RangePublicationService):
    def __init__(self) -> None:
        pass

    @override
    async def get(self, query_id: int, *, context: ActingContext) -> RangePublication | None:
        return None


class MockRecords(RecordService):
    def __init__(self) -> None:
        """Publish no records."""

    @override
    async def get(self, result_id: int, *, context: ActingContext) -> PublishedRecordResult | None:
        return None


class MockRecordAdministration(RecordAdministrationService):
    def __init__(self) -> None:
        """Queue nothing."""

    @override
    async def request_backstop(self, *, kinds: Sequence[BuildKind], context: ActingContext) -> None:
        return None


class MockCatalogueStats(CatalogueStatsService):
    def __init__(self) -> None:
        """Count an empty catalogue."""

    @override
    async def stats(self, *, context: ActingContext) -> CatalogueStats:
        return CatalogueStats(builds=0, records=0, creators=0)


class MockPublicRecords(PublicRecordQueryService):
    def __init__(self) -> None:
        """Publish no records."""

    @override
    async def get(self, record_id: UUID, *, context: ActingContext) -> PublicRecordDetail | None:
        return None

    @override
    async def list_page(
        self,
        *,
        selector: PageSelector = FIRST_PAGE,
        sort: RecordListSort = RecordListSort.ID,
        filters: RecordListFilters = ALL_RECORDS,
        page_size: int = 20,
        context: ActingContext,
    ) -> Page[PublicRecordSummary]:
        return cast(Page[PublicRecordSummary], _EMPTY_PAGE)


class MockSubmissionForms(SubmissionFormService):
    def __init__(self) -> None:
        """Serve the built-in manifest without a tag repository."""

    @override
    async def manifest(self, *, locale: str | None, context: ActingContext):
        return build_submission_manifest(locale)

    @override
    async def manifest_revision(self, schema_id: str, revision: int, *, locale: str | None, context: ActingContext):
        manifest = build_submission_manifest(locale)
        if manifest.schema_id == schema_id and manifest.revision == revision:
            return manifest
        return None

    @override
    async def options(self, source: str, category: str, *, locale: str | None, context: ActingContext) -> FormOptionSet:
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
        context: ActingContext,
        key: str,
        fingerprint: bytes,
        method: UnsafeHttpMethod,
        route: str,
        recoverable: bool = False,
    ) -> PendingRequest | StoredResponse:
        return PendingRequest(uuid.uuid4())

    @override
    async def complete(
        self, request: PendingRequest, response: StoredResponse, *, context: ActingContext
    ) -> StoredResponse:
        return response


class MockNotifications(NotificationService):
    def __init__(self) -> None:
        """Hold no repository; preferences are computed per call."""

    @override
    async def preferences(self, *, context: ActingContext) -> NotificationPreferences:
        return NotificationPreferences(account_id=context.effective.require_account_id())

    @override
    async def subscriptions(self, *, context: ActingContext) -> Sequence[NotificationSubscription]:
        return ()

    @override
    async def inbox(
        self,
        *,
        context: ActingContext,
        selector: PageSelector = FIRST_PAGE,
        page_size: int = 20,
        unread_only: bool = False,
    ) -> Inbox:
        return Inbox(Page(items=(), total=0, next=None, prev=None), unread_count=0)


class EmptyPermissionStore:
    """No stored rules, and a fixed epoch."""

    async def load_for_subject(self, subject: PermissionSubject) -> SubjectRecords:
        return SubjectRecords(epoch=1, subject=subject)

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
    async def list_active(self, *, limit: int = 10, context: ActingContext) -> tuple[StoredDraft, ...]:
        return ()

    @override
    async def attention_inbox(
        self, *, context: ActingContext, selector: PageSelector = FIRST_PAGE, page_size: int = 20
    ) -> Page[StoredDraft]:
        return offset_page((), offset=selector.offset, page_size=page_size)

    @override
    async def get_accessible(self, draft_id: UUID, *, context: ActingContext) -> StoredDraft:
        raise DraftNotFoundError(draft_id)

    @override
    async def delete(self, draft_id: UUID, *, context: ActingContext) -> None:
        raise DraftNotFoundError(draft_id)


class MockSubmissionFinalization(SubmissionFinalizationService):
    """No draft is finalizable, because no draft exists."""

    def __init__(self) -> None:
        """Hold no queue; nothing here enqueues an attempt."""

    @override
    async def status(self, draft_id: UUID, *, context: ActingContext) -> SubmissionRequestResult | None:
        raise DraftNotFoundError(draft_id)

    @override
    async def attempt(
        self, draft_id: UUID, attempt_id: UUID, *, context: ActingContext
    ) -> FinalizationJobSnapshot | None:
        raise DraftNotFoundError(draft_id)

    @override
    async def attempts(
        self, draft_id: UUID, *, context: ActingContext, selector: PageSelector = FIRST_PAGE, page_size: int = 20
    ) -> Page[FinalizationJobSnapshot]:
        raise DraftNotFoundError(draft_id)


class MockSubmissionSchematics(DraftSchematicService):
    """Holds no private sources, so every draft-scoped read reports the draft missing."""

    def __init__(self) -> None:
        self.max_bytes = SCHEMATIC_FILE_SCHEMA_MAX_BYTES

    @override
    async def list(self, draft_id: UUID, *, context: ActingContext) -> tuple[DraftSchematic, ...]:
        raise DraftNotFoundError(draft_id)

    @override
    async def reserve(
        self, draft_id: UUID, *, context: ActingContext, filename: str, upload_id: UUID
    ) -> DraftSchematic:
        raise DraftNotFoundError(draft_id)

    @override
    async def upload(
        self,
        draft_id: UUID,
        *,
        context: ActingContext,
        filename: str,
        data: bytes,
        upload_id: UUID | None = None,
    ) -> DraftSchematic:
        raise DraftNotFoundError(draft_id)

    @override
    async def fail(self, draft_id: UUID, upload_id: UUID, *, context: ActingContext) -> None:
        raise DraftNotFoundError(draft_id)

    @override
    async def select_primary(self, draft_id: UUID, upload_id: UUID, *, context: ActingContext) -> None:
        raise DraftNotFoundError(draft_id)

    @override
    async def discard(self, draft_id: UUID, upload_id: UUID, *, context: ActingContext) -> None:
        raise DraftNotFoundError(draft_id)

    @override
    async def attach(self, source_id: UUID, destination_id: UUID, upload_id: UUID, *, context: ActingContext) -> None:
        raise DraftNotFoundError(destination_id)


class MockSubmissionRevisions(RecalculationProposalService):
    """Retains no proposals, so listings are empty and every proposal id is unknown."""

    def __init__(self) -> None:
        """Hold no repository; nothing here reaches one."""

    @override
    async def list_for_source(
        self, source_message_id: int, *, context: ActingContext
    ) -> tuple[RecalculationProposal, ...]:
        return ()

    @override
    async def get(self, proposal_id: UUID, *, context: ActingContext) -> RecalculationProposal:
        raise NotFoundError

    @override
    async def match(
        self, proposal_id: UUID, build_id: int, *, context: ActingContext, renew: bool = False
    ) -> RecalculationProposal:
        raise NotFoundError

    @override
    async def approve(self, proposal_id: UUID, *, context: ActingContext) -> RecalculationProposal:
        raise NotFoundError


class MockSubmissionIntake(SubmissionAttachmentIntake):
    """Accepts no supplied attachments, because no draft exists to attach them to."""

    def __init__(self) -> None:
        """Hold no repository; nothing here reaches one."""

    @override
    async def list(self, draft_id: UUID, *, context: ActingContext) -> tuple[SuppliedAttachment, ...]:
        raise DraftNotFoundError(draft_id)

    @override
    async def reserve(
        self, draft_id: UUID, source_id: UUID, filename: str, content_type: str | None, *, context: ActingContext
    ) -> SuppliedAttachment:
        raise DraftNotFoundError(draft_id)

    @override
    async def register_file(
        self, draft_id: UUID, source_id: UUID, path: Path, content_type: str | None, *, context: ActingContext
    ) -> None:
        raise DraftNotFoundError(draft_id)

    @override
    async def discard(self, draft_id: UUID, source_id: UUID, *, context: ActingContext) -> None:
        raise DraftNotFoundError(draft_id)

    @override
    async def fail(self, draft_id: UUID, source_id: UUID, *, context: ActingContext) -> None:
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
    range_publication: RangePublicationService | None = None,
    versions: VersionService | None = None,
    permission_admin: PermissionAdministrationService | None = None,
    catalogue_stats: CatalogueStatsService | None = None,
    minecraft_installations: InstallationCredentialService | None = None,
) -> ApiServices:
    """Build the complete API graph with empty stores and injectable service replacements.

    The real permission service resolves catalogue defaults; the empty suggestion registry
    rejects unknown sources. Other services use the transport fakes above.
    """
    return ApiServices(
        impersonation=create_autospec(ImpersonationService, instance=True),
        submission_reviews=create_autospec(SubmissionReviews, instance=True),
        viewer_queries=create_autospec(ViewerQueries, instance=True),
        settings=RuntimeSettingsStub(),
        edits=MockEditRunner(),
        api_keys=None,
        web_auth=web_auth,
        cli_authorization=cli_authorization or MockCliAuthorization(),
        minecraft_installations=minecraft_installations or MockMinecraftInstallations(),
        minecraft_player_authorization=MockMinecraftPlayerAuthorization(),
        idempotency=idempotency or MockIdempotency(),
        notifications=MockNotifications(),
        builds=MockBuilds(),
        accounts=accounts or MockAccountManager(),
        build_queries=MockBuildQueries(),
        # Empty stored rules exercise the permission catalogue defaults.
        permissions=PermissionService(uow_factory=permission_uow_factory(EmptyPermissionStore())),
        permission_admin=permission_admin or create_autospec(PermissionAdministrationService, instance=True),
        permission_epoch=MockPermissionEpoch(),
        search=MockSearch(),
        # An empty registry preserves the service's unknown-source error.
        suggestions=SuggestionService(SuggestionRegistry.of(())),
        tags=MockTags(),
        versions=versions or MockVersions(),
        schematics=MockSchematics(),
        votes=MockVotes(),
        records=MockRecords(),
        record_administration=MockRecordAdministration(),
        public_records=MockPublicRecords(),
        catalogue_stats=catalogue_stats or MockCatalogueStats(),
        range_publication=range_publication or MockRangePublication(),
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
    range_publication: RangePublicationService | None = None,
    versions: VersionService | None = None,
    permission_admin: PermissionAdministrationService | None = None,
    catalogue_stats: CatalogueStatsService | None = None,
    minecraft_installations: InstallationCredentialService | None = None,
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
        range_publication=range_publication,
        versions=versions,
        permission_admin=permission_admin,
        catalogue_stats=catalogue_stats,
        minecraft_installations=minecraft_installations,
    )
    runtime = ApplicationRuntime(services, database.close, keep_database_active)
    return create_api_app(lambda _config: runtime, config=config), database
