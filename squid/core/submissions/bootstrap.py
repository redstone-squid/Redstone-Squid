"""Assemble the submission services shared by application processes."""

from dataclasses import dataclass
from functools import cached_property

from squid.core.artifacts import ArtifactStore
from squid.core.builds.application import BuildInferenceService, BuildService
from squid.core.media.application.jobs import MediaProcessingJobService
from squid.core.media.domain import MediaLimits
from squid.core.minecraft_auth.application import InstallationCredentialService
from squid.core.permissions.application import PermissionService
from squid.core.persistence.unit_of_work import SqlAlchemyUnitOfWork
from squid.core.schematics.application import SchematicService
from squid.core.submissions.application import (
    CheckedInFormManifestRegistry,
    SubmissionDraftService,
    SubmissionFinalizationService,
    SubmissionFinalizationWorker,
    SubmissionFormService,
    SubmissionPreparation,
)
from squid.core.submissions.application.artifact_readiness import (
    AuthoritativeDraftArtifactReadiness,
)
from squid.core.submissions.application.attachment_uploads import DraftAttachmentUploads
from squid.core.submissions.application.build_drafts import BuildDraftEditor
from squid.core.submissions.application.build_target import SubmissionBuildPreparation
from squid.core.submissions.application.commit import BuildSubmissionExecutor
from squid.core.submissions.application.inference_runs import SubmissionInferenceRuns
from squid.core.submissions.application.intake import SubmissionAttachmentIntake
from squid.core.submissions.application.recalculation import RecalculationProposalService
from squid.core.submissions.application.schematic_inference import SchematicSubmissionInference
from squid.core.submissions.application.schematics import DraftSchematicService
from squid.core.submissions.infrastructure.inference_codec import PydanticInferenceRunCodec
from squid.core.submissions.infrastructure.sponsors import PaperSponsorResolver
from squid.core.submissions.infrastructure.suggestion_options import SuggestionFormOptionCatalog
from squid.core.suggestions.application import SuggestionService
from squid.core.tags.application import TagService
from squid.core.unit_of_work import UnitOfWorkFactory
from squid.core.versions.application.services import VersionService


@dataclass(frozen=True)
class SubmissionServices:
    """Share drafts, forms, and intake across the submission services a process uses."""

    uow_factory: UnitOfWorkFactory[SqlAlchemyUnitOfWork]
    permissions: PermissionService
    builds: BuildService
    build_inference: BuildInferenceService
    artifacts: ArtifactStore
    schematics: SchematicService
    media_metadata: MediaProcessingJobService
    media_jobs: MediaProcessingJobService | None
    suggestions: SuggestionService
    tags: TagService
    version_service: VersionService
    minecraft_installations: InstallationCredentialService | None
    inferred_capacity: int
    max_schematic_bytes: int

    @cached_property
    def manifests(self) -> CheckedInFormManifestRegistry:
        return CheckedInFormManifestRegistry()

    @cached_property
    def forms(self) -> SubmissionFormService:
        return SubmissionFormService(SuggestionFormOptionCatalog(self.suggestions), self.manifests)

    @cached_property
    def drafts(self) -> SubmissionDraftService:
        return SubmissionDraftService(
            self.manifests,
            inferred_capacity=self.inferred_capacity,
            permissions=self.permissions,
            uow_factory=self.uow_factory,
        )

    @cached_property
    def inference(self) -> SubmissionInferenceRuns:
        return SubmissionInferenceRuns(
            self.build_inference,
            self.artifacts,
            PydanticInferenceRunCodec(),
            uow_factory=self.uow_factory,
            capacity=self.inferred_capacity,
            permissions=self.permissions,
        )

    @cached_property
    def uploads(self) -> DraftAttachmentUploads:
        return DraftAttachmentUploads(self.drafts, self.uow_factory)

    @cached_property
    def intake(self) -> SubmissionAttachmentIntake:
        return SubmissionAttachmentIntake(
            self.drafts, self.draft_schematics, self.media_jobs, uow_factory=self.uow_factory
        )

    @cached_property
    def recalculations(self) -> RecalculationProposalService:
        return RecalculationProposalService(
            self.builds,
            self.permissions,
            uow_factory=self.uow_factory,
        )

    @cached_property
    def draft_schematics(self) -> DraftSchematicService:
        return DraftSchematicService(
            self.drafts,
            self.artifacts,
            uow_factory=self.uow_factory,
            ingestor=self.schematics,
            inference=SchematicSubmissionInference(self.forms, self.version_service),
            max_bytes=self.max_schematic_bytes,
        )

    @cached_property
    def finalization(self) -> SubmissionFinalizationService:
        readiness = AuthoritativeDraftArtifactReadiness(
            self.media_metadata,
            self.draft_schematics,
            intake=self.intake,
            media_limits=MediaLimits(),
        )
        sponsors = (
            PaperSponsorResolver(self.minecraft_installations) if self.minecraft_installations is not None else None
        )
        return SubmissionFinalizationService(
            self.drafts,
            SubmissionPreparation(readiness, sponsors),
            editor=BuildDraftEditor(self.drafts, self.builds, self.forms, self.permissions, self.uow_factory),
            uow_factory=self.uow_factory,
        )

    @cached_property
    def finalization_worker(self) -> SubmissionFinalizationWorker:
        return SubmissionFinalizationWorker(
            BuildSubmissionExecutor(
                SubmissionBuildPreparation(self.builds, self.tags, self.version_service),
                drafts=self.drafts,
                uow_factory=self.uow_factory,
            ),
            preparation=self.finalization,
            uow_factory=self.uow_factory,
        )
