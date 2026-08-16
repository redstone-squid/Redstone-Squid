//! Command parsing and dispatch for the `squid` executable.

use std::collections::BTreeMap;
use std::env;
use std::io::{self, BufRead, IsTerminal, Write};
use std::path::PathBuf;
use std::process::ExitCode;
use std::str::FromStr;
use std::thread;
use std::time::{Duration, Instant};

use clap::{CommandFactory, Parser, Subcommand, ValueEnum};
use clap_complete::{Shell, generate};
use serde::Serialize;
use serde_json::Value;
use squid_cli_core::auth::{
    AuthState, AuthStateError, CliAuthApi, CliAuthError, IssuedCliSession, load_auth_state,
    load_or_create_auth_state, save_auth_state,
};
use squid_cli_core::credential::{
    CredentialBackend, CredentialError, CredentialKind, CredentialVault, DeviceIdentity,
    SecretBytes, load_or_create_device_identity, load_or_create_draft_cache_key,
};
use squid_cli_core::diagnostics::{
    DiagnosticsApi, DiagnosticsContractError, ErrorReportDetail, ErrorReportPage,
    validate_reference,
};
use squid_cli_core::encrypted_state::{EncryptedStateError, EncryptedStateStore};
use squid_cli_core::exit::ExitStatus;
use squid_cli_core::form::{
    FormAnswer, FormCode, FormError, InteractionMode, PromptRenderer, RendererCapabilities,
};
use squid_cli_core::locale::{Locale, LocalizedMessage, MessageKey, format_message};
use squid_cli_core::media::{
    DraftMedia, DraftMediaList, MediaContractError, MediaKind, SubmissionMediaApi,
};
use squid_cli_core::origin::{ApiOrigin, OriginError};
use squid_cli_core::output::{FailureEnvelope, SuccessEnvelope, write_json};
use squid_cli_core::profile::{
    EditorPreference, Profile, ProfileConfig, ProfileError, ProfileName, ProfileStore,
};
use squid_cli_core::submission::{
    DraftChangeRequest, DraftList, DraftSummary, FieldOperation, FieldOperationKind, FormManifest,
    FormOptionSet, StoredDraft, SubmissionApi, SubmissionContractError, SubmissionFinalization,
};
use squid_cli_core::terminal::sanitize_terminal_text;
use squid_cli_core::transport::{ApiClient, TransportError, status_code_class};
use squid_cli_core::tui::read_answer_tui;
use squid_cli_core::version::VersionInfo;
use uuid::Uuid;

/// Parsed top-level command line.
#[derive(Debug, Parser)]
#[command(name = "squid", version, about = "Redstone Squid command-line client")]
pub struct Cli {
    /// Select the output contract used by this invocation.
    #[arg(long, global = true, value_enum, default_value_t = OutputFormat::Human)]
    output: OutputFormat,

    /// Select human-facing messages independently of machine field names.
    #[arg(long, global = true, value_name = "LOCALE")]
    locale: Option<String>,

    #[command(subcommand)]
    command: Command,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, ValueEnum)]
enum OutputFormat {
    Human,
    Json,
}

#[derive(Debug, Subcommand)]
enum Command {
    /// Sign in, sign out, and inspect this profile's CLI device session.
    Auth {
        #[command(subcommand)]
        command: AuthCommand,
    },
    /// Create, inspect, edit, and finalize synchronized submission drafts.
    Draft {
        #[command(subcommand)]
        command: DraftCommand,
    },
    /// Generate a shell completion script.
    Completion {
        #[command(subcommand)]
        command: CompletionCommand,
    },
    /// Read stored error reports by the reference a user was shown.
    Errors {
        #[command(subcommand)]
        command: ErrorsCommand,
    },
    /// Upload, inspect, wait for, and discard normalized draft media.
    Media {
        #[command(subcommand)]
        command: MediaCommand,
    },
    /// Manage isolated API origins and local preferences.
    Profile {
        #[command(subcommand)]
        command: ProfileCommand,
    },
    /// Create a draft, complete its server-authored form, and submit it.
    Submit {
        /// Stable build category such as door, extender, utility, entrance, or other.
        category: String,
        /// Wait for durable finalization to complete or require attention.
        #[arg(long)]
        wait: bool,
        /// Maximum wait for asynchronous finalization.
        #[arg(long, default_value_t = 300, value_parser = clap::value_parser!(u64).range(1..=3600))]
        timeout_seconds: u64,
    },
    /// Report CLI and submission-protocol compatibility.
    Version,
}

#[derive(Debug, Subcommand)]
enum ErrorsCommand {
    /// Show the stored failure behind a reference.
    Show {
        /// The short reference a user was shown, or a full correlation ID from a Request-Id header.
        reference: String,
    },
    /// List the most recent stored failures, newest first.
    List,
}

#[derive(Debug, Subcommand)]
enum MediaCommand {
    /// Stream one image or video into an owned synchronized draft.
    Upload {
        draft_id: Uuid,
        #[arg(value_enum)]
        kind: MediaKindArgument,
        path: PathBuf,
        /// Explicit image/* or video/* type when the file extension is unknown.
        #[arg(long)]
        content_type: Option<String>,
        /// Remove audio while normalizing a video.
        #[arg(long)]
        strip_audio: bool,
        /// Wait for normalization to complete or fail.
        #[arg(long)]
        wait: bool,
        #[arg(long, default_value_t = 300, value_parser = clap::value_parser!(u64).range(1..=3600))]
        timeout_seconds: u64,
    },
    /// List retained media and server-advertised limits for one draft.
    List { draft_id: Uuid },
    /// Read one durable media processing state.
    Status { draft_id: Uuid, upload_id: Uuid },
    /// Stop processing and withdraw one retained media upload.
    Discard {
        draft_id: Uuid,
        upload_id: Uuid,
        /// Skip the interactive discard confirmation.
        #[arg(long)]
        yes: bool,
    },
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, ValueEnum)]
enum MediaKindArgument {
    Image,
    Video,
}

impl From<MediaKindArgument> for MediaKind {
    fn from(value: MediaKindArgument) -> Self {
        match value {
            MediaKindArgument::Image => Self::Image,
            MediaKindArgument::Video => Self::Video,
        }
    }
}

#[derive(Debug, Subcommand)]
enum DraftCommand {
    /// List up to ten active synchronized drafts.
    List,
    /// Create an empty synchronized draft pinned to the current form.
    Create {
        /// Stable build category.
        category: String,
        /// Immediately complete unanswered fields interactively.
        #[arg(long)]
        edit: bool,
    },
    /// Show one full owned draft snapshot.
    Show { draft_id: Uuid },
    /// Set one stable field to an explicit JSON value.
    Set {
        draft_id: Uuid,
        field_id: String,
        /// JSON primitive, array, or object accepted by the pinned form.
        value: String,
    },
    /// Remove one stable field answer.
    Unset { draft_id: Uuid, field_id: String },
    /// Complete currently unanswered visible fields using the configured renderer.
    Edit { draft_id: Uuid },
    /// Delete one editable draft and its private pending media.
    Delete {
        draft_id: Uuid,
        /// Skip the interactive deletion confirmation.
        #[arg(long)]
        yes: bool,
    },
    /// Start durable finalization for a complete draft.
    Submit {
        draft_id: Uuid,
        /// Wait for completion or actionable attention.
        #[arg(long)]
        wait: bool,
        #[arg(long, default_value_t = 300, value_parser = clap::value_parser!(u64).range(1..=3600))]
        timeout_seconds: u64,
    },
    /// Read retained durable finalization status.
    Status { draft_id: Uuid },
}

#[derive(Debug, Subcommand)]
enum AuthCommand {
    /// Enroll this device in a browser, or renew its existing device session.
    Login {
        /// Human-readable label displayed before browser approval.
        #[arg(long, default_value = "Squid CLI")]
        label: String,
        /// Permit owner-readable credential files when native storage is unavailable.
        #[arg(long)]
        allow_file_fallback: bool,
        /// Maximum local wait for browser approval.
        #[arg(long, default_value_t = 600, value_parser = clap::value_parser!(u64).range(1..=900))]
        timeout_seconds: u64,
    },
    /// Revoke the current server session and clear its local bearer.
    Logout {
        /// Clear only local state when the server cannot or should not be contacted.
        #[arg(long)]
        local_only: bool,
    },
    /// Show whether encrypted local state contains a device and session.
    Status,
}

#[derive(Debug, Subcommand)]
enum CompletionCommand {
    /// Write a completion script to stdout.
    Generate {
        /// Shell whose completion syntax should be generated.
        #[arg(value_enum)]
        shell: Shell,
    },
}

#[derive(Debug, Subcommand)]
enum ProfileCommand {
    /// Add and explicitly trust one API origin.
    Add {
        /// Lowercase local profile name.
        name: String,
        /// Exact API origin; paths, queries, and credentials are rejected.
        #[arg(long)]
        origin: String,
        /// Confirm that this origin may receive profile-scoped credentials.
        #[arg(long)]
        trust: bool,
        /// PEM certificate used in addition to the system trust store.
        #[arg(long, value_name = "PATH")]
        ca_certificate: Option<PathBuf>,
        /// Default locale for connected commands using this profile.
        #[arg(long, value_name = "LOCALE")]
        default_locale: Option<String>,
        /// Preferred interactive form renderer.
        #[arg(long, value_enum, default_value_t = EditorArgument::Tui)]
        editor: EditorArgument,
        /// Disable notification-only release checks for this profile.
        #[arg(long)]
        no_update_checks: bool,
    },
    /// List configured profiles in deterministic name order.
    List,
    /// Show one profile, or the active profile when omitted.
    Show {
        /// Profile to show instead of the active profile.
        name: Option<String>,
    },
    /// Select the profile used by connected commands.
    Use {
        /// Existing profile name.
        name: String,
    },
    /// Remove local profile configuration.
    Remove {
        /// Existing profile name.
        name: String,
        /// Skip the interactive confirmation after reviewing the name.
        #[arg(long)]
        yes: bool,
    },
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, ValueEnum)]
enum EditorArgument {
    Tui,
    Prompt,
}

impl From<EditorArgument> for EditorPreference {
    fn from(value: EditorArgument) -> Self {
        match value {
            EditorArgument::Tui => Self::Tui,
            EditorArgument::Prompt => Self::Prompt,
        }
    }
}

#[derive(Debug)]
struct CommandFailure {
    status: ExitStatus,
    code: &'static str,
    message: LocalizedMessage,
    suggested_action: Option<LocalizedMessage>,
    field_errors: BTreeMap<String, String>,
}

impl CommandFailure {
    fn new(status: ExitStatus, code: &'static str, message: MessageKey) -> Self {
        Self {
            status,
            code,
            message: LocalizedMessage::new(message),
            suggested_action: None,
            field_errors: BTreeMap::new(),
        }
    }

    fn with_suggested_action(mut self, message: MessageKey) -> Self {
        self.suggested_action = Some(LocalizedMessage::new(message));
        self
    }

    fn with_message_value(mut self, key: impl Into<String>, value: impl Into<String>) -> Self {
        self.message = self.message.with(key, value);
        self
    }

    fn with_field_error(mut self, field: impl Into<String>, detail: impl Into<String>) -> Self {
        self.field_errors.insert(field.into(), detail.into());
        self
    }
}

/// Parse process arguments, run one command, and return its stable exit status.
#[must_use]
pub fn main_entry() -> ExitCode {
    let cli = Cli::parse();
    let format = cli.output;
    let command_name = command_name(&cli.command);
    let locale = match resolve_locale(cli.locale.as_deref()) {
        Ok(locale) => locale,
        Err(failure) => {
            let fallback = Locale::from_environment();
            let _ignored = write_failure(format, fallback, command_name, &failure, None);
            return failure.status.into();
        }
    };
    match run(cli, locale, &mut io::stdout()) {
        Ok(()) => ExitStatus::Success.into(),
        Err(RunError::Io(error)) if error.kind() == io::ErrorKind::BrokenPipe => {
            ExitStatus::Success.into()
        }
        Err(RunError::Io(error)) => {
            let failure = CommandFailure::new(
                ExitStatus::LocalState,
                "local_io_failed",
                MessageKey::LocalIoFailed,
            )
            .with_suggested_action(MessageKey::SuggestedCheckFilesystem);
            let _ignored = write_failure(format, locale, command_name, &failure, Some(&error));
            failure.status.into()
        }
        Err(RunError::Command(failure)) => {
            let _ignored = write_failure(format, locale, command_name, &failure, None);
            failure.status.into()
        }
    }
}

fn run(cli: Cli, locale: Locale, output: &mut impl Write) -> Result<(), RunError> {
    match cli.command {
        Command::Auth { command } => {
            let store = ProfileStore::discover().map_err(|error| profile_failure(error, None))?;
            run_auth(command, cli.output, locale, &store, output)
        }
        Command::Draft { command } => {
            let store = ProfileStore::discover().map_err(|error| profile_failure(error, None))?;
            let mut session = ConnectedSession::open(locale, &store)?;
            run_draft(command, cli.output, locale, &mut session, output)
        }
        Command::Errors { command } => {
            let store = ProfileStore::discover().map_err(|error| profile_failure(error, None))?;
            let mut session = ConnectedSession::open(locale, &store)?;
            run_errors(command, cli.output, locale, &mut session, output)
        }
        Command::Completion {
            command: CompletionCommand::Generate { shell },
        } => {
            if cli.output == OutputFormat::Json {
                return Err(CommandFailure::new(
                    ExitStatus::Usage,
                    "raw_output_required",
                    MessageKey::CompletionRequiresHumanOutput,
                )
                .into());
            }
            generate(shell, &mut Cli::command(), "squid", output);
            Ok(())
        }
        Command::Media { command } => {
            let store = ProfileStore::discover().map_err(|error| profile_failure(error, None))?;
            let mut session = ConnectedSession::open(locale, &store)?;
            run_media(command, cli.output, locale, &mut session, output)
        }
        Command::Profile { command } => {
            let store = ProfileStore::discover().map_err(|error| profile_failure(error, None))?;
            run_profile(command, cli.output, locale, &store, output)
        }
        Command::Submit {
            category,
            wait,
            timeout_seconds,
        } => {
            let store = ProfileStore::discover().map_err(|error| profile_failure(error, None))?;
            let mut session = ConnectedSession::open(locale, &store)?;
            run_guided_submission(
                &category,
                wait,
                timeout_seconds,
                cli.output,
                locale,
                &mut session,
                output,
            )
        }
        Command::Version => write_version(cli.output, locale, output).map_err(RunError::Io),
    }
}

struct ConnectedSession {
    profile: Profile,
    backend: CredentialBackend,
    identity: DeviceIdentity,
    state_key: SecretBytes,
    state_store: EncryptedStateStore,
    state: AuthState,
    client: ApiClient,
}

impl ConnectedSession {
    fn open(locale: Locale, store: &ProfileStore) -> Result<Self, RunError> {
        let config = store.load().map_err(|error| profile_failure(error, None))?;
        let (_profile_name, profile) = config
            .resolve(None)
            .map_err(|error| profile_failure(error, None))?;
        let profile = profile.clone();
        let vault = CredentialVault::system(store.paths(), &profile.origin);
        let backend = vault.backend().map_err(credential_failure)?;
        let signing_key = vault
            .get(CredentialKind::DeviceSigningKey)
            .map_err(credential_failure)?
            .ok_or_else(authentication_required)?;
        let identity = DeviceIdentity::from_secret(&signing_key).map_err(credential_failure)?;
        let state_key = vault
            .get(CredentialKind::DraftCacheKey)
            .map_err(credential_failure)?
            .ok_or_else(authentication_required)?;
        let state_store = EncryptedStateStore::new(store.paths(), &profile.origin);
        let state = load_auth_state(&state_store, &state_key)
            .map_err(auth_state_failure)?
            .ok_or_else(authentication_required)?;
        let capabilities = match profile.editor {
            EditorPreference::Tui => RendererCapabilities::tui(),
            EditorPreference::Prompt => RendererCapabilities::prompt(false),
        };
        let client =
            ApiClient::for_profile(&profile, locale, state.client_instance_id(), &capabilities)
                .map_err(transport_failure)?;
        let mut session = Self {
            profile,
            backend,
            identity,
            state_key,
            state_store,
            state,
            client,
        };
        if session.state.session_token().is_none() {
            session.refresh()?;
        }
        Ok(session)
    }

    fn request<T>(
        &mut self,
        mut operation: impl FnMut(&ApiClient, &SecretBytes) -> Result<T, TransportError>,
    ) -> Result<T, RunError> {
        let mut refreshed = false;
        let mut network_retried = false;
        loop {
            let token = self
                .state
                .session_token()
                .ok_or_else(authentication_required)?;
            match operation(&self.client, &token) {
                Ok(value) => return Ok(value),
                Err(error) if is_transport_network_failure(&error) && !network_retried => {
                    network_retried = true;
                }
                Err(error) if is_transport_unauthorized(&error) && !refreshed => {
                    self.refresh()?;
                    refreshed = true;
                    network_retried = false;
                }
                Err(error) => return Err(transport_failure(error).into()),
            }
        }
    }

    fn refresh(&mut self) -> Result<(), RunError> {
        let device_id = self.state.device_id().ok_or_else(authentication_required)?;
        let issued = renew_cli_session(&CliAuthApi::new(&self.client), &self.identity, device_id)
            .map_err(auth_api_failure)?;
        self.state.set_session(issued);
        save_auth_state(&self.state_store, &self.state_key, &self.state)
            .map_err(auth_state_failure)?;
        Ok(())
    }

    fn renderer_capabilities(&self) -> RendererCapabilities {
        match self.profile.editor {
            EditorPreference::Tui => RendererCapabilities::tui(),
            EditorPreference::Prompt => RendererCapabilities::prompt(false),
        }
    }
}

fn run_draft(
    command: DraftCommand,
    format: OutputFormat,
    locale: Locale,
    session: &mut ConnectedSession,
    output: &mut impl Write,
) -> Result<(), RunError> {
    let result = match command {
        DraftCommand::List => {
            let response =
                session.request(|client, token| SubmissionApi::new(client).list_drafts(token))?;
            response
                .data
                .validate()
                .map_err(submission_contract_failure)?;
            write_draft_list(format, locale, &response.data, response.request_id, output)
        }
        DraftCommand::Create { category, edit } => {
            let (draft, request_id) = create_connected_draft(session, &category)?;
            if edit {
                let draft = edit_draft_interactively(session, draft.id, locale)
                    .map_err(|error| attach_preserved_draft(error, draft.id))?;
                write_connected_result(
                    "draft.create",
                    format,
                    locale,
                    LocalizedMessage::new(MessageKey::DraftChanged)
                        .with("draft_id", draft.id.hyphenated().to_string())
                        .with("revision", draft.revision.to_string()),
                    &draft,
                    None,
                    output,
                )
            } else {
                write_connected_result(
                    "draft.create",
                    format,
                    locale,
                    LocalizedMessage::new(MessageKey::DraftCreated)
                        .with("draft_id", draft.id.hyphenated().to_string())
                        .with("category", sanitize_terminal_text(&draft.category)),
                    &draft,
                    request_id,
                    output,
                )
            }
        }
        DraftCommand::Show { draft_id } => {
            let response = session
                .request(|client, token| SubmissionApi::new(client).get_draft(draft_id, token))?;
            write_draft_snapshot(format, &response.data, response.request_id, output)
        }
        DraftCommand::Set {
            draft_id,
            field_id,
            value,
        } => {
            FormCode::parse(&field_id).map_err(form_failure)?;
            let value = serde_json::from_str::<Value>(&value).map_err(|error| {
                CommandFailure::new(
                    ExitStatus::Usage,
                    "invalid_json_value",
                    MessageKey::InvalidJsonValue,
                )
                .with_field_error("value", sanitize_terminal_text(&error.to_string()))
            })?;
            let (draft, request_id) = change_draft_field(
                session,
                draft_id,
                field_id,
                FieldOperationKind::Set,
                Some(value),
            )?;
            write_changed_draft(format, locale, &draft, request_id, output)
        }
        DraftCommand::Unset { draft_id, field_id } => {
            FormCode::parse(&field_id).map_err(form_failure)?;
            let (draft, request_id) =
                change_draft_field(session, draft_id, field_id, FieldOperationKind::Unset, None)?;
            write_changed_draft(format, locale, &draft, request_id, output)
        }
        DraftCommand::Edit { draft_id } => {
            let draft = edit_draft_interactively(session, draft_id, locale)?;
            write_changed_draft(format, locale, &draft, None, output)
        }
        DraftCommand::Delete { draft_id, yes } => {
            if !yes && !confirm_draft_deletion(locale, draft_id)? {
                write_connected_result(
                    "draft.delete",
                    format,
                    locale,
                    LocalizedMessage::new(MessageKey::DraftDeletionCancelled)
                        .with("draft_id", draft_id.hyphenated().to_string()),
                    &DraftDeletionView {
                        draft_id,
                        deleted: false,
                    },
                    None,
                    output,
                )
            } else {
                let idempotency_key = Uuid::new_v4();
                let response = session.request(|client, token| {
                    SubmissionApi::new(client).delete_draft(draft_id, token, idempotency_key)
                })?;
                write_connected_result(
                    "draft.delete",
                    format,
                    locale,
                    LocalizedMessage::new(MessageKey::DraftDeleted)
                        .with("draft_id", draft_id.hyphenated().to_string()),
                    &DraftDeletionView {
                        draft_id,
                        deleted: true,
                    },
                    response.request_id,
                    output,
                )
            }
        }
        DraftCommand::Submit {
            draft_id,
            wait,
            timeout_seconds,
        } => {
            let finalization = submit_connected_draft(session, draft_id, wait, timeout_seconds)?;
            write_finalization_result("draft.submit", format, locale, &finalization, output)
        }
        DraftCommand::Status { draft_id } => {
            let response = session.request(|client, token| {
                SubmissionApi::new(client).submission_status(draft_id, token)
            })?;
            validate_finalization(&response.data, draft_id)?;
            write_finalization_result_with_request(
                "draft.status",
                format,
                locale,
                &response.data,
                response.request_id,
                output,
            )
        }
    };
    result?;
    write_credential_warning(format, locale, session.backend, output)?;
    Ok(())
}

fn run_media(
    command: MediaCommand,
    format: OutputFormat,
    locale: Locale,
    session: &mut ConnectedSession,
    output: &mut impl Write,
) -> Result<(), RunError> {
    let result = match command {
        MediaCommand::Upload {
            draft_id,
            kind,
            path,
            content_type,
            strip_audio,
            wait,
            timeout_seconds,
        } => {
            let kind = MediaKind::from(kind);
            if kind == MediaKind::Image && strip_audio {
                return Err(invalid_media_input("strip_audio_requires_video").into());
            }
            let content_type = resolve_media_content_type(kind, &path, content_type.as_deref())?;
            let current = session
                .request(|client, token| SubmissionMediaApi::new(client).list(draft_id, token))?;
            current
                .data
                .validate(draft_id)
                .map_err(media_contract_failure)?;
            let maximum_bytes = current.data.limits.max_upload_bytes;
            let upload_id = Uuid::new_v4();
            let response = session.request(|client, token| {
                SubmissionMediaApi::new(client).upload(
                    draft_id,
                    kind,
                    &path,
                    &content_type,
                    strip_audio,
                    upload_id,
                    maximum_bytes,
                    token,
                )
            })?;
            response
                .data
                .validate(draft_id)
                .map_err(media_contract_failure)?;
            if response.data.id != upload_id {
                return Err(
                    media_contract_failure(MediaContractError::MismatchedIdentifier).into(),
                );
            }
            let media = if wait && !response.data.is_terminal() {
                wait_for_media(session, draft_id, upload_id, timeout_seconds)?
            } else {
                response.data
            };
            write_media_result(
                "media.upload",
                format,
                locale,
                &media,
                response.request_id,
                output,
            )
        }
        MediaCommand::List { draft_id } => {
            let response = session
                .request(|client, token| SubmissionMediaApi::new(client).list(draft_id, token))?;
            response
                .data
                .validate(draft_id)
                .map_err(media_contract_failure)?;
            write_media_list(format, locale, &response.data, response.request_id, output)
        }
        MediaCommand::Status {
            draft_id,
            upload_id,
        } => {
            let response = session.request(|client, token| {
                SubmissionMediaApi::new(client).get(draft_id, upload_id, token)
            })?;
            response
                .data
                .validate(draft_id)
                .map_err(media_contract_failure)?;
            if response.data.id != upload_id {
                return Err(
                    media_contract_failure(MediaContractError::MismatchedIdentifier).into(),
                );
            }
            write_media_result(
                "media.status",
                format,
                locale,
                &response.data,
                response.request_id,
                output,
            )
        }
        MediaCommand::Discard {
            draft_id,
            upload_id,
            yes,
        } => {
            if !yes && !confirm_media_discard(locale, upload_id)? {
                write_connected_result(
                    "media.discard",
                    format,
                    locale,
                    LocalizedMessage::new(MessageKey::MediaDiscardCancelled)
                        .with("upload_id", upload_id.hyphenated().to_string()),
                    &MediaDiscardView {
                        draft_id,
                        upload_id,
                        discarded: false,
                    },
                    None,
                    output,
                )
            } else {
                let idempotency_key = Uuid::new_v4();
                let response = session.request(|client, token| {
                    SubmissionMediaApi::new(client).discard(
                        draft_id,
                        upload_id,
                        token,
                        idempotency_key,
                    )
                })?;
                write_connected_result(
                    "media.discard",
                    format,
                    locale,
                    LocalizedMessage::new(MessageKey::MediaDiscarded)
                        .with("upload_id", upload_id.hyphenated().to_string()),
                    &MediaDiscardView {
                        draft_id,
                        upload_id,
                        discarded: true,
                    },
                    response.request_id,
                    output,
                )
            }
        }
    };
    result?;
    write_credential_warning(format, locale, session.backend, output)?;
    Ok(())
}

fn resolve_media_content_type(
    kind: MediaKind,
    path: &std::path::Path,
    explicit: Option<&str>,
) -> Result<String, RunError> {
    let content_type = explicit.map_or_else(
        || {
            let extension = path
                .extension()
                .and_then(|value| value.to_str())
                .unwrap_or_default()
                .to_ascii_lowercase();
            match (kind, extension.as_str()) {
                (MediaKind::Image, "png") => Some("image/png"),
                (MediaKind::Image, "jpg" | "jpeg") => Some("image/jpeg"),
                (MediaKind::Image, "webp") => Some("image/webp"),
                (MediaKind::Image, "gif") => Some("image/gif"),
                (MediaKind::Image, "avif") => Some("image/avif"),
                (MediaKind::Video, "mp4") => Some("video/mp4"),
                (MediaKind::Video, "webm") => Some("video/webm"),
                (MediaKind::Video, "mov") => Some("video/quicktime"),
                (MediaKind::Video, "mkv") => Some("video/x-matroska"),
                (MediaKind::Video, "m4v") => Some("video/x-m4v"),
                _ => None,
            }
            .map(String::from)
        },
        |value| Some(value.trim().to_ascii_lowercase()),
    );
    let Some(content_type) = content_type else {
        return Err(invalid_media_input("content_type_required").into());
    };
    if !content_type.starts_with(&format!("{}/", kind.as_str())) {
        return Err(invalid_media_input("content_type_kind_mismatch").into());
    }
    Ok(content_type)
}

fn wait_for_media(
    session: &mut ConnectedSession,
    draft_id: Uuid,
    upload_id: Uuid,
    timeout_seconds: u64,
) -> Result<DraftMedia, RunError> {
    let timeout = Duration::from_secs(timeout_seconds);
    let started = Instant::now();
    loop {
        let elapsed = started.elapsed();
        if elapsed >= timeout {
            return Err(CommandFailure::new(
                ExitStatus::WaitTimeout,
                "media_wait_timeout",
                MessageKey::MediaWaitTimedOut,
            )
            .with_suggested_action(MessageKey::SuggestedCheckMediaStatus)
            .with_field_error("draft_id", draft_id.hyphenated().to_string())
            .with_field_error("upload_id", upload_id.hyphenated().to_string())
            .into());
        }
        thread::sleep(Duration::from_secs(2).min(timeout - elapsed));
        let response = session.request(|client, token| {
            SubmissionMediaApi::new(client).get(draft_id, upload_id, token)
        })?;
        response
            .data
            .validate(draft_id)
            .map_err(media_contract_failure)?;
        if response.data.id != upload_id {
            return Err(media_contract_failure(MediaContractError::MismatchedIdentifier).into());
        }
        if response.data.is_terminal() {
            return Ok(response.data);
        }
    }
}

fn media_contract_failure(error: MediaContractError) -> CommandFailure {
    CommandFailure::new(
        ExitStatus::Security,
        "invalid_media_contract",
        MessageKey::MediaContractInvalid,
    )
    .with_field_error("media", sanitize_terminal_text(&error.to_string()))
}

fn invalid_media_input(detail: &str) -> CommandFailure {
    CommandFailure::new(
        ExitStatus::Usage,
        "invalid_media_input",
        MessageKey::MediaInputInvalid,
    )
    .with_field_error("media", sanitize_terminal_text(detail))
}

#[derive(Debug, Serialize)]
struct MediaDiscardView {
    draft_id: Uuid,
    upload_id: Uuid,
    discarded: bool,
}

fn write_media_list(
    format: OutputFormat,
    locale: Locale,
    list: &DraftMediaList,
    request_id: Option<String>,
    output: &mut impl Write,
) -> Result<(), RunError> {
    match format {
        OutputFormat::Json => {
            let mut envelope = SuccessEnvelope::new("media.list", list);
            envelope.request_id = request_id;
            write_json(&envelope, output)?;
        }
        OutputFormat::Human if list.media.is_empty() => {
            writeln!(output, "{}", locale.message(MessageKey::MediaListEmpty))?;
        }
        OutputFormat::Human => {
            for media in &list.media {
                writeln!(
                    output,
                    "{}  {}  {}  {}",
                    media.id,
                    sanitize_terminal_text(&media.kind),
                    sanitize_terminal_text(&media.status),
                    sanitize_terminal_text(&media.source_content_type),
                )?;
            }
        }
    }
    Ok(())
}

fn write_media_result(
    command: &'static str,
    format: OutputFormat,
    locale: Locale,
    media: &DraftMedia,
    request_id: Option<String>,
    output: &mut impl Write,
) -> Result<(), RunError> {
    match format {
        OutputFormat::Json => {
            let mut envelope = SuccessEnvelope::new(command, media);
            envelope.request_id = request_id;
            write_json(&envelope, output)?;
        }
        OutputFormat::Human => {
            writeln!(
                output,
                "{}",
                LocalizedMessage::new(MessageKey::MediaUploaded)
                    .with("upload_id", media.id.hyphenated().to_string())
                    .with("draft_id", media.draft_id.hyphenated().to_string())
                    .with("status", sanitize_terminal_text(&media.status))
                    .render(locale),
            )?;
            for artifact in &media.artifacts {
                writeln!(
                    output,
                    "  {}: {} ({}×{})",
                    sanitize_terminal_text(&artifact.role),
                    sanitize_terminal_text(&artifact.content_type),
                    artifact.width,
                    artifact.height,
                )?;
            }
        }
    }
    Ok(())
}

fn confirm_media_discard(locale: Locale, upload_id: Uuid) -> Result<bool, RunError> {
    let stdin = io::stdin();
    if !stdin.is_terminal() {
        return Err(CommandFailure::new(
            ExitStatus::Usage,
            "confirmation_required",
            MessageKey::MediaConfirmationRequired,
        )
        .with_suggested_action(MessageKey::SuggestedUseMediaYes)
        .into());
    }
    let upload_id = upload_id.hyphenated().to_string();
    let prompt = LocalizedMessage::new(MessageKey::ConfirmMediaDiscard)
        .with("upload_id", &upload_id)
        .render(locale);
    let mut stderr = io::stderr().lock();
    write!(stderr, "{prompt}")?;
    stderr.flush()?;
    let mut response = String::new();
    stdin.lock().read_line(&mut response)?;
    Ok(response.trim().eq_ignore_ascii_case(&upload_id))
}

#[allow(clippy::too_many_arguments)]
fn run_guided_submission(
    category: &str,
    wait: bool,
    timeout_seconds: u64,
    format: OutputFormat,
    locale: Locale,
    session: &mut ConnectedSession,
    output: &mut impl Write,
) -> Result<(), RunError> {
    let (draft, _request_id) = create_connected_draft(session, category)?;
    if let Err(error) = edit_draft_interactively(session, draft.id, locale) {
        return Err(attach_preserved_draft(error, draft.id));
    }
    let finalization = submit_connected_draft(session, draft.id, wait, timeout_seconds)?;
    write_finalization_result("submit", format, locale, &finalization, output)?;
    write_credential_warning(format, locale, session.backend, output)?;
    Ok(())
}

fn create_connected_draft(
    session: &mut ConnectedSession,
    category: &str,
) -> Result<(StoredDraft, Option<String>), RunError> {
    FormCode::parse(category).map_err(form_failure)?;
    let form_response =
        session.request(|client, token| SubmissionApi::new(client).current_form(Some(token)))?;
    form_response
        .data
        .validate_for_category(category)
        .map_err(submission_contract_failure)?;
    require_renderer_capabilities(session, &form_response.data, category, &BTreeMap::new())?;
    let capabilities = session.renderer_capabilities().submission_capabilities();
    let idempotency_key = Uuid::new_v4();
    let response = session.request(|client, token| {
        SubmissionApi::new(client).create_draft(category, &capabilities, token, idempotency_key)
    })?;
    if response.data.category != category
        || response.data.schema_id != form_response.data.schema_id
        || response.data.schema_revision != form_response.data.revision
    {
        return Err(invalid_form_contract("created_draft").into());
    }
    Ok((response.data, response.request_id))
}

fn change_draft_field(
    session: &mut ConnectedSession,
    draft_id: Uuid,
    field_id: String,
    kind: FieldOperationKind,
    value: Option<Value>,
) -> Result<(StoredDraft, Option<String>), RunError> {
    let current = session
        .request(|client, token| SubmissionApi::new(client).get_draft(draft_id, token))?
        .data;
    let idempotency_key = Uuid::new_v4();
    let change = DraftChangeRequest {
        base_revision: current.revision,
        client_instance_id: session.state.client_instance_id().as_string(),
        idempotency_key: idempotency_key.hyphenated().to_string(),
        operations: vec![FieldOperation {
            operation_id: Uuid::new_v4(),
            field_id,
            kind,
            value,
        }],
    };
    let response = session.request(|client, token| {
        SubmissionApi::new(client).change_draft(draft_id, &change, token, idempotency_key)
    })?;
    if response.data.draft.id != draft_id
        || response.data.draft.revision < current.revision.saturating_add(1)
    {
        return Err(invalid_form_contract("changed_draft").into());
    }
    Ok((response.data.draft, response.request_id))
}

fn edit_draft_interactively(
    session: &mut ConnectedSession,
    draft_id: Uuid,
    locale: Locale,
) -> Result<StoredDraft, RunError> {
    if !io::stdin().is_terminal()
        || (session.profile.editor == EditorPreference::Tui && !io::stderr().is_terminal())
    {
        return Err(CommandFailure::new(
            ExitStatus::Usage,
            "form_interaction_required",
            MessageKey::FormInteractionRequired,
        )
        .with_suggested_action(MessageKey::SuggestedContinueOnWeb)
        .into());
    }
    let mut draft = session
        .request(|client, token| SubmissionApi::new(client).get_draft(draft_id, token))?
        .data;
    let schema_id = draft.schema_id.clone();
    let schema_revision = draft.schema_revision;
    let form_response = session.request(|client, token| {
        SubmissionApi::new(client).pinned_form(&schema_id, schema_revision, Some(token))
    })?;
    let manifest = form_response.data;
    if manifest.schema_id != draft.schema_id || manifest.revision != draft.schema_revision {
        return Err(invalid_form_contract("pinned_manifest").into());
    }
    manifest
        .validate_for_category(&draft.category)
        .map_err(submission_contract_failure)?;
    require_renderer_capabilities(session, &manifest, &draft.category, &draft.answers)?;

    for field in manifest
        .fields_for(&draft.category)
        .map_err(submission_contract_failure)?
    {
        if !field.is_visible(&draft.answers)
            || draft.answers.contains_key(&field.id)
            || !field.default.is_null()
        {
            continue;
        }
        let option_set = if let Some(source) = field.option_source.as_deref() {
            let response = session.request(|client, token| {
                SubmissionApi::new(client).form_options(source, &draft.category, Some(token))
            })?;
            validate_option_set(&response.data, source, &draft.category)?;
            Some(response.data)
        } else {
            None
        };
        let adapted = match field.adapt(
            option_set
                .as_ref()
                .map(|options| options.options.as_slice()),
        ) {
            Ok(adapted) => adapted,
            Err(SubmissionContractError::UnsupportedControl) if !field.required => continue,
            Err(error) => return Err(submission_contract_failure(error).into()),
        };
        let answer = loop {
            let answer = read_form_answer(session.profile.editor, &adapted.field, locale)?;
            if answer.as_ref().is_none_or(|answer| {
                field.constraints.must_equal.is_null()
                    || answer.to_json() == field.constraints.must_equal
            }) {
                break answer;
            }
            let mut stderr = io::stderr().lock();
            writeln!(stderr, "{}", locale.message(MessageKey::FormAnswerInvalid))?;
        };
        if let Some(answer) = answer {
            let (updated, _request_id) = change_draft_field(
                session,
                draft.id,
                field.id.clone(),
                FieldOperationKind::Set,
                Some(answer.to_json()),
            )?;
            draft = updated;
            require_renderer_capabilities(session, &manifest, &draft.category, &draft.answers)?;
        }
    }
    Ok(draft)
}

fn read_form_answer(
    editor: EditorPreference,
    field: &squid_cli_core::form::FormField,
    locale: Locale,
) -> Result<Option<FormAnswer>, RunError> {
    match editor {
        EditorPreference::Tui => {
            read_answer_tui(field, locale).map_err(|error| form_failure(error).into())
        }
        EditorPreference::Prompt => {
            let stdin = io::stdin();
            let mut input = stdin.lock();
            let stderr = io::stderr();
            let mut output = stderr.lock();
            let renderer = PromptRenderer::new(InteractionMode::Interactive).with_locale(locale);
            loop {
                match renderer.read_answer(field, &mut input, &mut output) {
                    Ok(answer) => return Ok(answer),
                    Err(FormError::InvalidAnswer) => {
                        writeln!(output, "{}", locale.message(MessageKey::FormAnswerInvalid))?;
                    }
                    Err(error) => return Err(form_failure(error).into()),
                }
            }
        }
    }
}

fn require_renderer_capabilities(
    session: &ConnectedSession,
    manifest: &FormManifest,
    category: &str,
    answers: &BTreeMap<String, Value>,
) -> Result<(), RunError> {
    let assessment = manifest
        .assess_capabilities(category, answers, &session.renderer_capabilities())
        .map_err(submission_contract_failure)?;
    if assessment.web_continuation_required {
        return Err(CommandFailure::new(
            ExitStatus::ServerRejection,
            "form_requires_web_continuation",
            MessageKey::FormRequiresWeb,
        )
        .with_suggested_action(MessageKey::SuggestedContinueOnWeb)
        .with_field_error(
            "missing_capabilities",
            assessment.missing_required.join(","),
        )
        .into());
    }
    Ok(())
}

fn validate_option_set(
    option_set: &FormOptionSet,
    expected_source: &str,
    expected_category: &str,
) -> Result<(), RunError> {
    if option_set.source != expected_source
        || option_set.category != expected_category
        || option_set.revision == 0
        || option_set.options.len() > 500
    {
        return Err(invalid_form_contract("option_source").into());
    }
    Ok(())
}

fn submit_connected_draft(
    session: &mut ConnectedSession,
    draft_id: Uuid,
    wait: bool,
    timeout_seconds: u64,
) -> Result<SubmissionFinalization, RunError> {
    let idempotency_key = Uuid::new_v4();
    let response = session.request(|client, token| {
        SubmissionApi::new(client).submit_draft(draft_id, token, idempotency_key)
    })?;
    validate_finalization(&response.data, draft_id)?;
    if wait && !finalization_is_terminal(&response.data) {
        wait_for_finalization(session, draft_id, timeout_seconds)
    } else {
        Ok(response.data)
    }
}

fn wait_for_finalization(
    session: &mut ConnectedSession,
    draft_id: Uuid,
    timeout_seconds: u64,
) -> Result<SubmissionFinalization, RunError> {
    let timeout = Duration::from_secs(timeout_seconds);
    let started = Instant::now();
    loop {
        let elapsed = started.elapsed();
        if elapsed >= timeout {
            return Err(CommandFailure::new(
                ExitStatus::WaitTimeout,
                "finalization_wait_timeout",
                MessageKey::FinalizationWaitTimedOut,
            )
            .with_suggested_action(MessageKey::SuggestedCheckStatus)
            .with_field_error("draft_id", draft_id.hyphenated().to_string())
            .into());
        }
        thread::sleep(Duration::from_secs(2).min(timeout - elapsed));
        let response = session.request(|client, token| {
            SubmissionApi::new(client).submission_status(draft_id, token)
        })?;
        validate_finalization(&response.data, draft_id)?;
        if finalization_is_terminal(&response.data) {
            return Ok(response.data);
        }
    }
}

fn validate_finalization(
    finalization: &SubmissionFinalization,
    expected_draft_id: Uuid,
) -> Result<(), RunError> {
    if finalization.draft_id != expected_draft_id
        || !matches!(
            finalization.status.as_str(),
            "pending" | "claimed" | "needs_attention" | "completed" | "dead"
        )
    {
        return Err(invalid_form_contract("submission_finalization").into());
    }
    Ok(())
}

fn finalization_is_terminal(finalization: &SubmissionFinalization) -> bool {
    matches!(
        finalization.status.as_str(),
        "needs_attention" | "completed" | "dead"
    )
}

fn attach_preserved_draft(error: RunError, draft_id: Uuid) -> RunError {
    match error {
        RunError::Command(mut failure) => {
            failure.field_errors.insert(
                String::from("preserved_draft_id"),
                draft_id.hyphenated().to_string(),
            );
            RunError::Command(failure)
        }
        error => error,
    }
}

fn run_errors(
    command: ErrorsCommand,
    format: OutputFormat,
    locale: Locale,
    session: &mut ConnectedSession,
    output: &mut impl Write,
) -> Result<(), RunError> {
    match command {
        ErrorsCommand::List => {
            let response =
                session.request(|client, token| DiagnosticsApi::new(client).list_errors(token))?;
            response
                .data
                .validate()
                .map_err(diagnostics_contract_failure)?;
            write_error_list(format, locale, &response.data, response.request_id, output)
        }
        ErrorsCommand::Show { reference } => {
            validate_reference(&reference).map_err(diagnostics_contract_failure)?;
            let response = session.request(|client, token| {
                DiagnosticsApi::new(client).get_error(&reference, token)
            })?;
            write_error_detail(format, locale, &response.data, response.request_id, output)
        }
    }
}

fn write_error_list(
    format: OutputFormat,
    locale: Locale,
    page: &ErrorReportPage,
    request_id: Option<String>,
    output: &mut impl Write,
) -> Result<(), RunError> {
    match format {
        OutputFormat::Json => {
            let mut envelope = SuccessEnvelope::new("errors.list", page);
            envelope.request_id = request_id;
            write_json(&envelope, output)?;
        }
        OutputFormat::Human if page.items.is_empty() => {
            writeln!(output, "{}", locale.message(MessageKey::ErrorListEmpty))?;
        }
        OutputFormat::Human => {
            for report in &page.items {
                writeln!(
                    output,
                    "{}  {}  {}  {}",
                    report.reference,
                    sanitize_terminal_text(&report.occurred_at),
                    sanitize_terminal_text(&report.exception_type),
                    sanitize_terminal_text(report.origin.as_deref().unwrap_or(&report.surface)),
                )?;
            }
        }
    }
    Ok(())
}

fn write_error_detail(
    format: OutputFormat,
    locale: Locale,
    report: &ErrorReportDetail,
    request_id: Option<String>,
    output: &mut impl Write,
) -> Result<(), RunError> {
    match format {
        OutputFormat::Json => {
            let mut envelope = SuccessEnvelope::new("errors.show", report);
            envelope.request_id = request_id;
            write_json(&envelope, output)?;
        }
        OutputFormat::Human => {
            if report.matching_references > 1 {
                writeln!(
                    output,
                    "{}",
                    locale.message(MessageKey::ErrorReferenceAmbiguous)
                )?;
            }
            writeln!(output, "reference:      {}", report.reference)?;
            writeln!(output, "correlation_id: {}", report.correlation_id)?;
            writeln!(
                output,
                "occurred_at:    {}",
                sanitize_terminal_text(&report.occurred_at)
            )?;
            writeln!(
                output,
                "surface:        {}",
                sanitize_terminal_text(&report.surface)
            )?;
            writeln!(
                output,
                "origin:         {}",
                sanitize_terminal_text(report.origin.as_deref().unwrap_or("-"))
            )?;
            writeln!(
                output,
                "exception:      {}",
                sanitize_terminal_text(&report.exception_type)
            )?;
            writeln!(
                output,
                "message:        {}",
                sanitize_terminal_text(&report.message)
            )?;
            // Sanitized like every other server string: a traceback is attacker-influenced text
            // (an exception message can carry user input) heading for a terminal.
            writeln!(output, "\n{}", sanitize_terminal_text(&report.traceback))?;
            if !report.log_tail.is_empty() {
                writeln!(
                    output,
                    "{}",
                    locale.message(MessageKey::ErrorLogTailHeading)
                )?;
                for line in &report.log_tail {
                    writeln!(output, "  {}", sanitize_terminal_text(line))?;
                }
            }
        }
    }
    Ok(())
}

fn diagnostics_contract_failure(error: DiagnosticsContractError) -> CommandFailure {
    let status = match &error {
        DiagnosticsContractError::InvalidReference => ExitStatus::Usage,
        DiagnosticsContractError::TooManyReports => ExitStatus::Security,
    };
    CommandFailure::new(
        status,
        "invalid_diagnostics_contract",
        MessageKey::InvalidErrorReference,
    )
    .with_field_error("reference", sanitize_terminal_text(&error.to_string()))
}

fn submission_contract_failure(error: SubmissionContractError) -> CommandFailure {
    let status = match &error {
        SubmissionContractError::UnknownCategory => ExitStatus::Usage,
        SubmissionContractError::IncompatibleProtocol
        | SubmissionContractError::UnsupportedControl => ExitStatus::ServerRejection,
        SubmissionContractError::DuplicateIdentifier
        | SubmissionContractError::InvalidField
        | SubmissionContractError::TooManyDrafts
        | SubmissionContractError::Form(_) => ExitStatus::Security,
    };
    let mut failure = CommandFailure::new(
        status,
        "invalid_submission_contract",
        MessageKey::InvalidFormContract,
    )
    .with_field_error("form", sanitize_terminal_text(&error.to_string()));
    if matches!(status, ExitStatus::ServerRejection) {
        failure = failure.with_suggested_action(MessageKey::SuggestedContinueOnWeb);
    }
    failure
}

fn form_failure(error: FormError) -> CommandFailure {
    match error {
        FormError::Cancelled | FormError::EndOfInput => CommandFailure::new(
            ExitStatus::Interrupted,
            "form_editing_cancelled",
            MessageKey::FormEditingCancelled,
        )
        .with_suggested_action(MessageKey::SuggestedContinueOnWeb),
        FormError::InteractionRequired => CommandFailure::new(
            ExitStatus::Usage,
            "form_interaction_required",
            MessageKey::FormInteractionRequired,
        )
        .with_suggested_action(MessageKey::SuggestedContinueOnWeb),
        FormError::UnsupportedControl => CommandFailure::new(
            ExitStatus::ServerRejection,
            "form_requires_web_continuation",
            MessageKey::FormRequiresWeb,
        )
        .with_suggested_action(MessageKey::SuggestedContinueOnWeb),
        FormError::InvalidAnswer | FormError::InputTooLarge => CommandFailure::new(
            ExitStatus::Usage,
            "invalid_form_answer",
            MessageKey::FormAnswerInvalid,
        ),
        error => invalid_form_contract(&sanitize_terminal_text(&error.to_string())),
    }
}

fn invalid_form_contract(detail: &str) -> CommandFailure {
    CommandFailure::new(
        ExitStatus::Security,
        "invalid_submission_contract",
        MessageKey::InvalidFormContract,
    )
    .with_field_error("form", sanitize_terminal_text(detail))
}

#[derive(Debug, Serialize)]
struct DraftDeletionView {
    draft_id: Uuid,
    deleted: bool,
}

fn write_draft_list(
    format: OutputFormat,
    locale: Locale,
    list: &DraftList,
    request_id: Option<String>,
    output: &mut impl Write,
) -> Result<(), RunError> {
    match format {
        OutputFormat::Json => {
            let mut envelope = SuccessEnvelope::new("draft.list", list);
            envelope.request_id = request_id;
            write_json(&envelope, output)?;
        }
        OutputFormat::Human if list.drafts.is_empty() => {
            writeln!(output, "{}", locale.message(MessageKey::DraftListEmpty))?;
        }
        OutputFormat::Human => {
            for draft in &list.drafts {
                write_draft_summary(draft, output)?;
            }
        }
    }
    Ok(())
}

fn write_draft_summary(draft: &DraftSummary, output: &mut impl Write) -> io::Result<()> {
    let display_name = draft
        .display_name
        .as_deref()
        .map(sanitize_terminal_text)
        .map(|name| format!("  {name}"))
        .unwrap_or_default();
    writeln!(
        output,
        "{}  {}  {}  r{}{}",
        draft.id,
        sanitize_terminal_text(&draft.category),
        sanitize_terminal_text(&draft.status),
        draft.revision,
        display_name,
    )
}

fn write_draft_snapshot(
    format: OutputFormat,
    draft: &StoredDraft,
    request_id: Option<String>,
    output: &mut impl Write,
) -> Result<(), RunError> {
    match format {
        OutputFormat::Json => {
            let mut envelope = SuccessEnvelope::new("draft.show", draft);
            envelope.request_id = request_id;
            write_json(&envelope, output)?;
        }
        OutputFormat::Human => {
            let rendered = serde_json::to_string_pretty(draft).map_err(io::Error::other)?;
            writeln!(output, "{}", sanitize_terminal_text(&rendered))?;
        }
    }
    Ok(())
}

fn write_changed_draft(
    format: OutputFormat,
    locale: Locale,
    draft: &StoredDraft,
    request_id: Option<String>,
    output: &mut impl Write,
) -> Result<(), RunError> {
    write_connected_result(
        "draft.change",
        format,
        locale,
        LocalizedMessage::new(MessageKey::DraftChanged)
            .with("draft_id", draft.id.hyphenated().to_string())
            .with("revision", draft.revision.to_string()),
        draft,
        request_id,
        output,
    )
}

fn write_finalization_result(
    command: &'static str,
    format: OutputFormat,
    locale: Locale,
    finalization: &SubmissionFinalization,
    output: &mut impl Write,
) -> Result<(), RunError> {
    write_finalization_result_with_request(command, format, locale, finalization, None, output)
}

fn write_finalization_result_with_request(
    command: &'static str,
    format: OutputFormat,
    locale: Locale,
    finalization: &SubmissionFinalization,
    request_id: Option<String>,
    output: &mut impl Write,
) -> Result<(), RunError> {
    match format {
        OutputFormat::Json => {
            let mut envelope = SuccessEnvelope::new(command, finalization);
            envelope.request_id = request_id;
            write_json(&envelope, output)?;
        }
        OutputFormat::Human => {
            writeln!(
                output,
                "{}",
                finalization_message(finalization).render(locale)
            )?;
            for issue in &finalization.issues {
                writeln!(
                    output,
                    "  {}: {}",
                    sanitize_terminal_text(&issue.field_id),
                    sanitize_terminal_text(&issue.reason),
                )?;
            }
            if let Some(build_id) = finalization.build_id {
                writeln!(output, "  build: {build_id}")?;
            }
        }
    }
    Ok(())
}

fn finalization_message(finalization: &SubmissionFinalization) -> LocalizedMessage {
    LocalizedMessage::new(MessageKey::DraftSubmitted)
        .with("draft_id", finalization.draft_id.hyphenated().to_string())
        .with("status", sanitize_terminal_text(&finalization.status))
}

fn write_connected_result<T: Serialize>(
    command: &'static str,
    format: OutputFormat,
    locale: Locale,
    message: LocalizedMessage,
    data: &T,
    request_id: Option<String>,
    output: &mut impl Write,
) -> Result<(), RunError> {
    match format {
        OutputFormat::Human => writeln!(output, "{}", message.render(locale))?,
        OutputFormat::Json => {
            let mut envelope = SuccessEnvelope::new(command, data);
            envelope.request_id = request_id;
            write_json(&envelope, output)?;
        }
    }
    Ok(())
}

fn confirm_draft_deletion(locale: Locale, draft_id: Uuid) -> Result<bool, RunError> {
    let stdin = io::stdin();
    if !stdin.is_terminal() {
        return Err(CommandFailure::new(
            ExitStatus::Usage,
            "confirmation_required",
            MessageKey::DraftConfirmationRequired,
        )
        .with_suggested_action(MessageKey::SuggestedUseDraftYes)
        .into());
    }
    let draft_id = draft_id.hyphenated().to_string();
    let prompt = LocalizedMessage::new(MessageKey::ConfirmDraftDeletion)
        .with("draft_id", &draft_id)
        .render(locale);
    let mut stderr = io::stderr().lock();
    write!(stderr, "{prompt}")?;
    stderr.flush()?;
    let mut response = String::new();
    stdin.lock().read_line(&mut response)?;
    Ok(response.trim().eq_ignore_ascii_case(&draft_id))
}

fn authentication_required() -> CommandFailure {
    CommandFailure::new(
        ExitStatus::Authentication,
        "cli_authentication_required",
        MessageKey::AuthLoginRequired,
    )
    .with_suggested_action(MessageKey::SuggestedLogin)
}

fn is_transport_unauthorized(error: &TransportError) -> bool {
    matches!(error, TransportError::Http { status: 401, .. })
}

fn is_transport_network_failure(error: &TransportError) -> bool {
    matches!(error, TransportError::Request(_))
}

fn run_auth(
    command: AuthCommand,
    format: OutputFormat,
    locale: Locale,
    store: &ProfileStore,
    output: &mut impl Write,
) -> Result<(), RunError> {
    let config = store.load().map_err(|error| profile_failure(error, None))?;
    let (profile_name, profile) = config
        .resolve(None)
        .map_err(|error| profile_failure(error, None))?;
    let profile_name = String::from(profile_name);
    let profile = profile.clone();
    let vault = CredentialVault::system(store.paths(), &profile.origin);

    match command {
        AuthCommand::Login {
            label,
            allow_file_fallback,
            timeout_seconds,
        } => {
            let (identity, _created, _backend) =
                load_or_create_device_identity(&vault, allow_file_fallback)
                    .map_err(credential_failure)?;
            let (state_key, _created, backend) =
                load_or_create_draft_cache_key(&vault, allow_file_fallback)
                    .map_err(credential_failure)?;
            let state_store = EncryptedStateStore::new(store.paths(), &profile.origin);
            let mut state =
                load_or_create_auth_state(&state_store, &state_key).map_err(auth_state_failure)?;
            let client = ApiClient::for_profile(
                &profile,
                locale,
                state.client_instance_id(),
                &RendererCapabilities::prompt(false),
            )
            .map_err(transport_failure)?;
            let api = CliAuthApi::new(&client);

            let (issued, renewed) = if let Some(device_id) = state.device_id() {
                match renew_cli_session(&api, &identity, device_id) {
                    Ok(issued) => (issued, true),
                    Err(error) if is_device_unavailable(&error) => {
                        state.clear_device();
                        save_auth_state(&state_store, &state_key, &state)
                            .map_err(auth_state_failure)?;
                        (
                            enroll_cli_device(
                                &api,
                                &identity,
                                &state,
                                &label,
                                timeout_seconds,
                                format,
                                locale,
                                output,
                            )?,
                            false,
                        )
                    }
                    Err(error) => return Err(auth_api_failure(error).into()),
                }
            } else {
                (
                    enroll_cli_device(
                        &api,
                        &identity,
                        &state,
                        &label,
                        timeout_seconds,
                        format,
                        locale,
                        output,
                    )?,
                    false,
                )
            };
            let fingerprint = issued.device.public_key_fingerprint.clone();
            state.set_session(issued);
            save_auth_state(&state_store, &state_key, &state).map_err(auth_state_failure)?;
            let view = AuthStatusView::from_state(
                &profile_name,
                profile.origin.as_str(),
                backend,
                &state,
                Some(fingerprint),
            );
            let key = if renewed {
                MessageKey::AuthSessionRenewed
            } else {
                MessageKey::AuthLoginSucceeded
            };
            let message = LocalizedMessage::new(key)
                .with("device_id", view.device_id.as_deref().unwrap_or("unknown"))
                .with(
                    "expires_at",
                    view.expires_at.as_deref().unwrap_or("unknown"),
                );
            write_auth_result("auth.login", format, locale, message, &view, output)?;
            write_credential_warning(format, locale, backend, output)?;
            Ok(())
        }
        AuthCommand::Logout { local_only } => {
            let backend = vault.backend().map_err(credential_failure)?;
            let Some(state_key) = vault
                .get(CredentialKind::DraftCacheKey)
                .map_err(credential_failure)?
            else {
                return write_auth_signed_out(
                    "auth.logout",
                    format,
                    locale,
                    &profile_name,
                    profile.origin.as_str(),
                    backend,
                    MessageKey::AuthAlreadyLoggedOut,
                    output,
                );
            };
            let state_store = EncryptedStateStore::new(store.paths(), &profile.origin);
            let Some(mut state) =
                load_auth_state(&state_store, &state_key).map_err(auth_state_failure)?
            else {
                return write_auth_signed_out(
                    "auth.logout",
                    format,
                    locale,
                    &profile_name,
                    profile.origin.as_str(),
                    backend,
                    MessageKey::AuthAlreadyLoggedOut,
                    output,
                );
            };
            let Some(token) = state.session_token() else {
                return write_auth_signed_out(
                    "auth.logout",
                    format,
                    locale,
                    &profile_name,
                    profile.origin.as_str(),
                    backend,
                    MessageKey::AuthAlreadyLoggedOut,
                    output,
                );
            };
            if !local_only {
                let client = ApiClient::for_profile(
                    &profile,
                    locale,
                    state.client_instance_id(),
                    &RendererCapabilities::prompt(false),
                )
                .map_err(transport_failure)?;
                let idempotency_key = Uuid::new_v4();
                if let Err(error) = retry_cli_network_once(|| {
                    CliAuthApi::new(&client).revoke_current_session(&token, idempotency_key)
                }) {
                    if !is_invalid_session(&error) {
                        return Err(auth_api_failure(error).into());
                    }
                }
            }
            state.clear_session();
            save_auth_state(&state_store, &state_key, &state).map_err(auth_state_failure)?;
            let view = AuthStatusView::from_state(
                &profile_name,
                profile.origin.as_str(),
                backend,
                &state,
                None,
            );
            write_auth_result(
                "auth.logout",
                format,
                locale,
                LocalizedMessage::new(MessageKey::AuthLoggedOut),
                &view,
                output,
            )?;
            write_credential_warning(format, locale, backend, output)?;
            Ok(())
        }
        AuthCommand::Status => {
            let backend = vault.backend().map_err(credential_failure)?;
            let state_key = vault
                .get(CredentialKind::DraftCacheKey)
                .map_err(credential_failure)?;
            let state_store = EncryptedStateStore::new(store.paths(), &profile.origin);
            let state = state_key
                .as_ref()
                .map(|key| load_auth_state(&state_store, key))
                .transpose()
                .map_err(auth_state_failure)?
                .flatten();
            let view = state.as_ref().map_or_else(
                || AuthStatusView::signed_out(&profile_name, profile.origin.as_str(), backend),
                |state| {
                    AuthStatusView::from_state(
                        &profile_name,
                        profile.origin.as_str(),
                        backend,
                        state,
                        None,
                    )
                },
            );
            let message = if view.signed_in {
                LocalizedMessage::new(MessageKey::AuthStatusSignedIn)
                    .with("device_id", view.device_id.as_deref().unwrap_or("unknown"))
                    .with(
                        "expires_at",
                        view.expires_at.as_deref().unwrap_or("unknown"),
                    )
            } else {
                LocalizedMessage::new(MessageKey::AuthStatusSignedOut)
            };
            write_auth_result("auth.status", format, locale, message, &view, output)?;
            write_credential_warning(format, locale, backend, output)?;
            Ok(())
        }
    }
}

fn renew_cli_session(
    api: &CliAuthApi<'_>,
    identity: &DeviceIdentity,
    device_id: Uuid,
) -> Result<IssuedCliSession, CliAuthError> {
    let challenge_key = Uuid::new_v4();
    let challenge =
        retry_cli_network_once(|| api.start_session_challenge(device_id, challenge_key))?.data;
    let exchange_key = Uuid::new_v4();
    Ok(retry_cli_network_once(|| {
        api.exchange_session_challenge(identity, &challenge, exchange_key)
    })?
    .data)
}

#[allow(clippy::too_many_arguments)]
fn enroll_cli_device(
    api: &CliAuthApi<'_>,
    identity: &DeviceIdentity,
    state: &AuthState,
    label: &str,
    timeout_seconds: u64,
    format: OutputFormat,
    locale: Locale,
    output: &mut impl Write,
) -> Result<IssuedCliSession, RunError> {
    let enrollment_key = Uuid::new_v4();
    let enrollment = retry_cli_network_once(|| {
        api.start_enrollment(identity, state.client_instance_id(), label, enrollment_key)
    })
    .map_err(auth_api_failure)?
    .data;
    let instructions = LocalizedMessage::new(MessageKey::AuthApprovalInstructions)
        .with(
            "url",
            sanitize_terminal_text(&enrollment.verification_uri_complete),
        )
        .with("code", sanitize_terminal_text(&enrollment.user_code))
        .with("fingerprint", identity.public_key_fingerprint())
        .render(locale);
    match format {
        OutputFormat::Human => {
            writeln!(output, "{instructions}")?;
            output.flush()?;
        }
        OutputFormat::Json => {
            let mut stderr = io::stderr().lock();
            writeln!(stderr, "{instructions}")?;
            stderr.flush()?;
        }
    }

    let timeout = Duration::from_secs(timeout_seconds);
    let started = Instant::now();
    let polling_interval = Duration::from_secs(enrollment.polling_interval_seconds.clamp(1, 30));
    loop {
        let idempotency_key = Uuid::new_v4();
        match retry_cli_network_once(|| {
            api.exchange_enrollment(identity, &enrollment, idempotency_key)
        }) {
            Ok(response) => return Ok(response.data),
            Err(error) if is_authorization_pending(&error) => {
                let elapsed = started.elapsed();
                if elapsed >= timeout {
                    return Err(CommandFailure::new(
                        ExitStatus::WaitTimeout,
                        "cli_authorization_timeout",
                        MessageKey::AuthWaitTimedOut,
                    )
                    .with_suggested_action(MessageKey::SuggestedApproveDevice)
                    .into());
                }
                thread::sleep(polling_interval.min(timeout - elapsed));
            }
            Err(error) => return Err(auth_api_failure(error).into()),
        }
    }
}

fn retry_cli_network_once<T>(
    mut operation: impl FnMut() -> Result<T, CliAuthError>,
) -> Result<T, CliAuthError> {
    match operation() {
        Err(error) if is_network_failure(&error) => operation(),
        result => result,
    }
}

#[derive(Debug, Serialize)]
struct AuthStatusView {
    profile: String,
    origin: String,
    credential_backend: &'static str,
    device_enrolled: bool,
    signed_in: bool,
    device_id: Option<String>,
    session_id: Option<String>,
    expires_at: Option<String>,
    public_key_fingerprint: Option<String>,
}

impl AuthStatusView {
    fn signed_out(profile: &str, origin: &str, credential_backend: CredentialBackend) -> Self {
        Self {
            profile: String::from(profile),
            origin: String::from(origin),
            credential_backend: credential_backend_name(credential_backend),
            device_enrolled: false,
            signed_in: false,
            device_id: None,
            session_id: None,
            expires_at: None,
            public_key_fingerprint: None,
        }
    }

    fn from_state(
        profile: &str,
        origin: &str,
        credential_backend: CredentialBackend,
        state: &AuthState,
        public_key_fingerprint: Option<String>,
    ) -> Self {
        Self {
            profile: String::from(profile),
            origin: String::from(origin),
            credential_backend: credential_backend_name(credential_backend),
            device_enrolled: state.device_id().is_some(),
            signed_in: state.session_id().is_some(),
            device_id: state
                .device_id()
                .map(|value| value.hyphenated().to_string()),
            session_id: state
                .session_id()
                .map(|value| value.hyphenated().to_string()),
            expires_at: state.expires_at().map(String::from),
            public_key_fingerprint,
        }
    }
}

#[allow(clippy::too_many_arguments)]
fn write_auth_signed_out(
    command: &'static str,
    format: OutputFormat,
    locale: Locale,
    profile: &str,
    origin: &str,
    backend: CredentialBackend,
    message: MessageKey,
    output: &mut impl Write,
) -> Result<(), RunError> {
    let view = AuthStatusView::signed_out(profile, origin, backend);
    write_auth_result(
        command,
        format,
        locale,
        LocalizedMessage::new(message),
        &view,
        output,
    )?;
    write_credential_warning(format, locale, backend, output)?;
    Ok(())
}

fn write_auth_result(
    command: &'static str,
    format: OutputFormat,
    locale: Locale,
    message: LocalizedMessage,
    view: &AuthStatusView,
    output: &mut impl Write,
) -> io::Result<()> {
    match format {
        OutputFormat::Human => writeln!(output, "{}", message.render(locale)),
        OutputFormat::Json => write_json(&SuccessEnvelope::new(command, view), output),
    }
}

fn write_credential_warning(
    format: OutputFormat,
    locale: Locale,
    backend: CredentialBackend,
    output: &mut impl Write,
) -> io::Result<()> {
    if format == OutputFormat::Human && backend == CredentialBackend::OwnerFile {
        writeln!(
            output,
            "{}",
            locale.message(MessageKey::AuthFallbackWarning)
        )?;
    }
    Ok(())
}

fn run_profile(
    command: ProfileCommand,
    format: OutputFormat,
    locale: Locale,
    store: &ProfileStore,
    output: &mut impl Write,
) -> Result<(), RunError> {
    match command {
        ProfileCommand::Add {
            name,
            origin,
            trust,
            ca_certificate,
            default_locale,
            editor,
            no_update_checks,
        } => {
            let name = parse_profile_name(&name)?;
            if !trust {
                return Err(CommandFailure::new(
                    ExitStatus::Usage,
                    "profile_trust_required",
                    MessageKey::ProfileTrustRequired,
                )
                .into());
            }
            let origin = ApiOrigin::parse(&origin).map_err(origin_failure)?;
            let default_locale = default_locale
                .map(|value| {
                    Locale::from_str(&value)
                        .map(|parsed| String::from(parsed.code()))
                        .map_err(|_error| {
                            CommandFailure::new(
                                ExitStatus::Usage,
                                "invalid_profile_locale",
                                MessageKey::ProfileLocaleInvalid,
                            )
                            .with_field_error("default_locale", value)
                        })
                })
                .transpose()?;
            let mut profile = Profile::new(origin);
            profile.ca_certificate = ca_certificate;
            profile.locale = default_locale;
            profile.editor = editor.into();
            profile.update_checks = !no_update_checks;
            let config = store
                .add(&name, profile)
                .map_err(|error| profile_failure(error, Some(name.as_str())))?;
            let (_stored_name, stored) = config
                .resolve(Some(&name))
                .map_err(|error| profile_failure(error, Some(name.as_str())))?;
            let view = ProfileView::from_profile(
                name.as_str(),
                config.active_profile.as_deref() == Some(name.as_str()),
                stored,
            );
            write_profile_result(
                "profile.add",
                format,
                locale,
                LocalizedMessage::new(MessageKey::ProfileAdded)
                    .with("name", name.as_str())
                    .with("origin", stored.origin.as_str()),
                view,
                output,
            )?;
            Ok(())
        }
        ProfileCommand::List => {
            let config = store.load().map_err(|error| profile_failure(error, None))?;
            write_profile_list(format, locale, &config, output)?;
            Ok(())
        }
        ProfileCommand::Show { name } => {
            let parsed_name = name.as_deref().map(parse_profile_name).transpose()?;
            let config = store
                .load()
                .map_err(|error| profile_failure(error, name.as_deref()))?;
            let (stored_name, profile) = config
                .resolve(parsed_name.as_ref())
                .map_err(|error| profile_failure(error, name.as_deref()))?;
            let view = ProfileView::from_profile(
                stored_name,
                config.active_profile.as_deref() == Some(stored_name),
                profile,
            );
            write_profile_view("profile.show", format, locale, &view, output)?;
            Ok(())
        }
        ProfileCommand::Use { name } => {
            let name = parse_profile_name(&name)?;
            store
                .select(&name)
                .map_err(|error| profile_failure(error, Some(name.as_str())))?;
            write_profile_result(
                "profile.use",
                format,
                locale,
                LocalizedMessage::new(MessageKey::ProfileSelected).with("name", name.as_str()),
                ProfileMutation {
                    name: String::from(name.as_str()),
                    active_profile: Some(String::from(name.as_str())),
                    removed: false,
                    purged_credential_backend: None,
                },
                output,
            )?;
            Ok(())
        }
        ProfileCommand::Remove { name, yes } => {
            let name = parse_profile_name(&name)?;
            let current = store
                .load()
                .map_err(|error| profile_failure(error, Some(name.as_str())))?;
            let (_stored_name, profile) = current
                .resolve(Some(&name))
                .map_err(|error| profile_failure(error, Some(name.as_str())))?;
            let origin = profile.origin.clone();
            let should_remove = if yes {
                true
            } else {
                confirm_profile_removal(locale, name.as_str())?
            };
            if !should_remove {
                write_profile_result(
                    "profile.remove",
                    format,
                    locale,
                    LocalizedMessage::new(MessageKey::ProfileRemovalCancelled)
                        .with("name", name.as_str()),
                    ProfileMutation {
                        name: String::from(name.as_str()),
                        active_profile: current.active_profile,
                        removed: false,
                        purged_credential_backend: None,
                    },
                    output,
                )?;
                return Ok(());
            }
            let encrypted_state = EncryptedStateStore::new(store.paths(), &origin);
            encrypted_state.purge().map_err(encrypted_state_failure)?;
            let credentials = CredentialVault::system(store.paths(), &origin);
            let purged_backend = credentials.purge().map_err(credential_failure)?;
            let config = store
                .remove(&name)
                .map_err(|error| profile_failure(error, Some(name.as_str())))?;
            write_profile_result(
                "profile.remove",
                format,
                locale,
                LocalizedMessage::new(MessageKey::ProfileRemoved).with("name", name.as_str()),
                ProfileMutation {
                    name: String::from(name.as_str()),
                    active_profile: config.active_profile,
                    removed: true,
                    purged_credential_backend: Some(credential_backend_name(purged_backend)),
                },
                output,
            )?;
            Ok(())
        }
    }
}

#[derive(Debug, Serialize)]
struct ProfileView {
    name: String,
    active: bool,
    origin: String,
    trusted: bool,
    ca_certificate: Option<String>,
    locale: Option<String>,
    editor: &'static str,
    update_checks: bool,
}

impl ProfileView {
    fn from_profile(name: &str, active: bool, profile: &Profile) -> Self {
        Self {
            name: String::from(name),
            active,
            origin: String::from(profile.origin.as_str()),
            trusted: profile.trusted,
            ca_certificate: profile
                .ca_certificate
                .as_ref()
                .map(|path| path.to_string_lossy().into_owned()),
            locale: profile.locale.clone(),
            editor: match profile.editor {
                EditorPreference::Tui => "tui",
                EditorPreference::Prompt => "prompt",
            },
            update_checks: profile.update_checks,
        }
    }
}

#[derive(Debug, Serialize)]
struct ProfileList {
    active_profile: Option<String>,
    profiles: Vec<ProfileView>,
}

#[derive(Debug, Serialize)]
struct ProfileMutation {
    name: String,
    active_profile: Option<String>,
    removed: bool,
    purged_credential_backend: Option<&'static str>,
}

fn write_profile_result(
    command: &'static str,
    format: OutputFormat,
    locale: Locale,
    message: LocalizedMessage,
    data: impl Serialize,
    output: &mut impl Write,
) -> io::Result<()> {
    match format {
        OutputFormat::Human => writeln!(output, "{}", message.render(locale)),
        OutputFormat::Json => write_json(&SuccessEnvelope::new(command, data), output),
    }
}

fn write_profile_list(
    format: OutputFormat,
    locale: Locale,
    config: &ProfileConfig,
    output: &mut impl Write,
) -> io::Result<()> {
    let profiles = config
        .profiles
        .iter()
        .map(|(name, profile)| {
            ProfileView::from_profile(
                name,
                config.active_profile.as_deref() == Some(name.as_str()),
                profile,
            )
        })
        .collect::<Vec<_>>();
    if format == OutputFormat::Human {
        if profiles.is_empty() {
            return writeln!(output, "{}", locale.message(MessageKey::ProfileListEmpty));
        }
        for profile in &profiles {
            write_human_profile(locale, profile, output)?;
        }
        return Ok(());
    }
    write_json(
        &SuccessEnvelope::new(
            "profile.list",
            ProfileList {
                active_profile: config.active_profile.clone(),
                profiles,
            },
        ),
        output,
    )
}

fn write_profile_view(
    command: &'static str,
    format: OutputFormat,
    locale: Locale,
    profile: &ProfileView,
    output: &mut impl Write,
) -> io::Result<()> {
    match format {
        OutputFormat::Human => write_human_profile(locale, profile, output),
        OutputFormat::Json => write_json(&SuccessEnvelope::new(command, profile), output),
    }
}

fn write_human_profile(
    locale: Locale,
    profile: &ProfileView,
    output: &mut impl Write,
) -> io::Result<()> {
    let active = if profile.active {
        MessageKey::ProfileActiveMarker
    } else {
        MessageKey::ProfileInactiveMarker
    };
    let profile_locale = profile.locale.as_deref().map_or_else(
        || locale.message(MessageKey::ProfileDefaultLocale),
        |value| value,
    );
    let editor = match profile.editor {
        "tui" => locale.message(MessageKey::EditorTui),
        _ => locale.message(MessageKey::EditorPrompt),
    };
    let update_checks = if profile.update_checks {
        MessageKey::Enabled
    } else {
        MessageKey::Disabled
    };
    let ca_certificate = profile.ca_certificate.as_deref().map_or_else(
        || String::from(locale.message(MessageKey::ProfileNoCustomCa)),
        sanitize_terminal_text,
    );
    writeln!(
        output,
        "{}",
        LocalizedMessage::new(MessageKey::ProfileDetails)
            .with("active", locale.message(active))
            .with("name", sanitize_terminal_text(&profile.name))
            .with("origin", sanitize_terminal_text(&profile.origin))
            .with("locale", sanitize_terminal_text(profile_locale))
            .with("editor", editor)
            .with("update_checks", locale.message(update_checks))
            .with("ca_certificate", ca_certificate)
            .render(locale),
    )
}

fn confirm_profile_removal(locale: Locale, name: &str) -> Result<bool, RunError> {
    let stdin = io::stdin();
    if !stdin.is_terminal() {
        return Err(CommandFailure::new(
            ExitStatus::Usage,
            "confirmation_required",
            MessageKey::ProfileConfirmationRequired,
        )
        .with_suggested_action(MessageKey::SuggestedUseYes)
        .into());
    }
    let prompt = LocalizedMessage::new(MessageKey::ConfirmProfileRemoval)
        .with("name", sanitize_terminal_text(name))
        .render(locale);
    let mut stderr = io::stderr().lock();
    write!(stderr, "{prompt}")?;
    stderr.flush()?;
    let mut response = String::new();
    stdin.lock().read_line(&mut response)?;
    Ok(response.trim().eq_ignore_ascii_case("yes"))
}

fn parse_profile_name(value: &str) -> Result<ProfileName, CommandFailure> {
    ProfileName::parse(value).map_err(|_error| {
        CommandFailure::new(
            ExitStatus::Usage,
            "invalid_profile_name",
            MessageKey::InvalidProfileName,
        )
        .with_field_error("name", sanitize_terminal_text(value))
    })
}

fn origin_failure(error: OriginError) -> CommandFailure {
    let message = match error {
        OriginError::InvalidUrl => MessageKey::OriginInvalidUrl,
        OriginError::UnsupportedScheme => MessageKey::OriginSchemeInvalid,
        OriginError::CredentialsNotAllowed => MessageKey::OriginCredentialsRejected,
        OriginError::OriginOnly => MessageKey::OriginComponentsRejected,
        OriginError::MissingHost => MessageKey::OriginHostMissing,
        OriginError::MissingPort => MessageKey::OriginPortMissing,
        OriginError::HttpsRequired => MessageKey::OriginHttpsRequired,
    };
    CommandFailure::new(ExitStatus::Usage, "invalid_api_origin", message)
}

fn profile_failure(error: ProfileError, name: Option<&str>) -> CommandFailure {
    match error {
        ProfileError::InvalidName => CommandFailure::new(
            ExitStatus::Usage,
            "invalid_profile_name",
            MessageKey::InvalidProfileName,
        ),
        ProfileError::AlreadyExists => CommandFailure::new(
            ExitStatus::LocalState,
            "profile_already_exists",
            MessageKey::ProfileAlreadyExists,
        )
        .with_message_value("name", name.unwrap_or("")),
        ProfileError::NotFound => CommandFailure::new(
            ExitStatus::LocalState,
            "profile_not_found",
            MessageKey::ProfileNotFound,
        )
        .with_message_value("name", name.unwrap_or("")),
        ProfileError::NoActiveProfile => CommandFailure::new(
            ExitStatus::LocalState,
            "no_active_profile",
            MessageKey::NoActiveProfile,
        ),
        ProfileError::TrustRequired => CommandFailure::new(
            ExitStatus::Usage,
            "profile_trust_required",
            MessageKey::ProfileTrustRequired,
        ),
        ProfileError::UnsupportedLocale => CommandFailure::new(
            ExitStatus::Usage,
            "invalid_profile_locale",
            MessageKey::ProfileLocaleInvalid,
        ),
        ProfileError::InvalidCaCertificate => CommandFailure::new(
            ExitStatus::Usage,
            "invalid_ca_certificate",
            MessageKey::ProfileCaInvalid,
        ),
        ProfileError::SymlinkNotAllowed => CommandFailure::new(
            ExitStatus::Security,
            "profile_symlink_rejected",
            MessageKey::ProfileSymlinkRejected,
        ),
        ProfileError::Origin(error) => origin_failure(error),
        ProfileError::Io(error) => CommandFailure::new(
            ExitStatus::LocalState,
            "profile_storage_failed",
            MessageKey::ProfileStorageFailed,
        )
        .with_suggested_action(MessageKey::SuggestedCheckFilesystem)
        .with_field_error("profile_store", sanitize_terminal_text(&error.to_string())),
        other => CommandFailure::new(
            ExitStatus::LocalState,
            "invalid_profile_config",
            MessageKey::ProfileConfigInvalid,
        )
        .with_field_error("profile_store", sanitize_terminal_text(&other.to_string())),
    }
}

const fn credential_backend_name(backend: CredentialBackend) -> &'static str {
    match backend {
        CredentialBackend::System => "system",
        CredentialBackend::OwnerFile => "owner_file",
    }
}

fn credential_failure(error: CredentialError) -> CommandFailure {
    let status = if matches!(
        error,
        CredentialError::SymlinkNotAllowed
            | CredentialError::InvalidBackendMarker
            | CredentialError::InvalidDeviceKey
            | CredentialError::InvalidDraftCacheKey
    ) {
        ExitStatus::Security
    } else {
        ExitStatus::LocalState
    };
    CommandFailure::new(
        status,
        "local_security_state_failed",
        MessageKey::LocalSecurityStateFailed,
    )
    .with_suggested_action(MessageKey::SuggestedCheckFilesystem)
    .with_field_error("credentials", sanitize_terminal_text(&error.to_string()))
}

fn auth_state_failure(error: AuthStateError) -> CommandFailure {
    match error {
        AuthStateError::Encrypted(error) => encrypted_state_failure(error),
        error @ (AuthStateError::UnsupportedSchema(_) | AuthStateError::InvalidState) => {
            CommandFailure::new(
                ExitStatus::Security,
                "invalid_cli_auth_state",
                MessageKey::AuthStateFailed,
            )
            .with_suggested_action(MessageKey::SuggestedCheckFilesystem)
            .with_field_error("auth_state", sanitize_terminal_text(&error.to_string()))
        }
    }
}

fn auth_api_failure(error: CliAuthError) -> CommandFailure {
    match error {
        CliAuthError::Transport(error) => transport_failure(error),
        CliAuthError::ProofValueTooLong => CommandFailure::new(
            ExitStatus::Security,
            "invalid_cli_auth_proof",
            MessageKey::ApiRequestFailed,
        )
        .with_field_error("device_proof", "proof value exceeded the protocol limit"),
    }
}

fn transport_failure(error: TransportError) -> CommandFailure {
    match error {
        error @ (TransportError::InvalidUploadFile
        | TransportError::EmptyUploadFile
        | TransportError::UploadFileTooLarge
        | TransportError::InvalidUploadContentType) => CommandFailure::new(
            ExitStatus::Usage,
            "invalid_media_input",
            MessageKey::MediaInputInvalid,
        )
        .with_field_error("media", sanitize_terminal_text(&error.to_string())),
        TransportError::Http {
            status,
            problem,
            request_id,
            retry_after,
        } => {
            let mut failure = CommandFailure::new(
                status_code_class(status),
                "api_request_failed",
                MessageKey::ApiRequestFailed,
            );
            if let Some(problem) = problem {
                failure = failure
                    .with_field_error("server", sanitize_terminal_text(&problem.message))
                    .with_field_error("api_code", sanitize_terminal_text(&problem.code));
                if let Some(code) = problem.application_code("cli_auth_code") {
                    failure =
                        failure.with_field_error("cli_auth_code", sanitize_terminal_text(code));
                }
            }
            if let Some(request_id) = request_id {
                failure =
                    failure.with_field_error("request_id", sanitize_terminal_text(&request_id));
            }
            if let Some(retry_after) = retry_after {
                failure =
                    failure.with_field_error("retry_after", sanitize_terminal_text(&retry_after));
            }
            if failure.status == ExitStatus::Authentication {
                failure.with_suggested_action(MessageKey::SuggestedLogin)
            } else {
                failure.with_suggested_action(MessageKey::SuggestedRetry)
            }
        }
        error => {
            let status = if matches!(
                &error,
                TransportError::CrossOriginResponse
                    | TransportError::InvalidBearerToken
                    | TransportError::InvalidContentType
            ) {
                ExitStatus::Security
            } else if matches!(
                &error,
                TransportError::Request(_)
                    | TransportError::BuildClient(_)
                    | TransportError::ResponseTooLarge
            ) {
                ExitStatus::Unavailable
            } else {
                ExitStatus::LocalState
            };
            CommandFailure::new(status, "api_transport_failed", MessageKey::ApiRequestFailed)
                .with_suggested_action(MessageKey::SuggestedRetry)
                .with_field_error("transport", sanitize_terminal_text(&error.to_string()))
        }
    }
}

fn cli_auth_code(error: &CliAuthError) -> Option<&str> {
    match error {
        CliAuthError::Transport(TransportError::Http {
            problem: Some(problem),
            ..
        }) => problem.application_code("cli_auth_code"),
        _ => None,
    }
}

fn is_authorization_pending(error: &CliAuthError) -> bool {
    cli_auth_code(error) == Some("cli_authorization_pending")
}

fn is_device_unavailable(error: &CliAuthError) -> bool {
    cli_auth_code(error) == Some("cli_device_unavailable")
}

fn is_invalid_session(error: &CliAuthError) -> bool {
    cli_auth_code(error) == Some("invalid_cli_session")
        || matches!(
            error,
            CliAuthError::Transport(TransportError::Http { status: 401, .. })
        )
}

fn is_network_failure(error: &CliAuthError) -> bool {
    matches!(error, CliAuthError::Transport(TransportError::Request(_)))
}

fn encrypted_state_failure(error: EncryptedStateError) -> CommandFailure {
    let status = if matches!(
        error,
        EncryptedStateError::AuthenticationFailed
            | EncryptedStateError::InvalidEnvelope
            | EncryptedStateError::UnsupportedEnvelope(_)
            | EncryptedStateError::SymlinkNotAllowed
    ) {
        ExitStatus::Security
    } else {
        ExitStatus::LocalState
    };
    CommandFailure::new(
        status,
        "local_security_state_failed",
        MessageKey::LocalSecurityStateFailed,
    )
    .with_suggested_action(MessageKey::SuggestedCheckFilesystem)
    .with_field_error(
        "encrypted_state",
        sanitize_terminal_text(&error.to_string()),
    )
}

fn write_version(format: OutputFormat, locale: Locale, output: &mut impl Write) -> io::Result<()> {
    let version = VersionInfo::current();
    match format {
        OutputFormat::Human => {
            let minimum = version.minimum_protocol.to_string();
            let maximum = version.maximum_protocol.to_string();
            writeln!(
                output,
                "{}",
                format_message(
                    locale.message(MessageKey::VersionLine),
                    &[
                        ("version", version.cli_version),
                        ("target", version.target),
                        ("minimum", &minimum),
                        ("maximum", &maximum),
                    ],
                ),
            )
        }
        OutputFormat::Json => write_json(&SuccessEnvelope::new("version", version), output),
    }
}

fn write_failure(
    format: OutputFormat,
    locale: Locale,
    command: &str,
    failure: &CommandFailure,
    source: Option<&io::Error>,
) -> io::Result<()> {
    let message = failure.message.render(locale);
    match format {
        OutputFormat::Human => {
            let mut stderr = io::stderr().lock();
            writeln!(stderr, "squid: {message}")?;
            if let Some(source) = source {
                writeln!(
                    stderr,
                    "  {}",
                    squid_cli_core::terminal::sanitize_terminal_text(&source.to_string()),
                )?;
            }
            for detail in failure.field_errors.values() {
                writeln!(stderr, "  {}", sanitize_terminal_text(detail))?;
            }
            if let Some(action) = &failure.suggested_action {
                writeln!(stderr, "  {}", action.render(locale))?;
            }
            Ok(())
        }
        OutputFormat::Json => {
            let mut envelope = FailureEnvelope::new(command, failure.code, message);
            envelope.error.suggested_action = failure
                .suggested_action
                .as_ref()
                .map(|action| action.render(locale));
            envelope.error.field_errors = failure.field_errors.clone();
            if let Some(source) = source {
                envelope.error.field_errors.insert(
                    String::from("local_io"),
                    squid_cli_core::terminal::sanitize_terminal_text(&source.to_string()),
                );
            }
            write_json(&envelope, &mut io::stdout().lock())
        }
    }
}

fn resolve_locale(explicit: Option<&str>) -> Result<Locale, CommandFailure> {
    if let Some(value) = explicit {
        return Locale::from_str(value).map_err(|_error| {
            CommandFailure::new(
                ExitStatus::Usage,
                "unsupported_locale",
                MessageKey::UnsupportedLocale,
            )
        });
    }
    if let Ok(value) = env::var("SQUID_LOCALE") {
        return Locale::from_str(&value).map_err(|_error| {
            CommandFailure::new(
                ExitStatus::LocalState,
                "unsupported_locale",
                MessageKey::UnsupportedLocale,
            )
        });
    }
    Ok(Locale::from_environment())
}

const fn command_name(command: &Command) -> &'static str {
    match command {
        Command::Auth { command } => match command {
            AuthCommand::Login { .. } => "auth.login",
            AuthCommand::Logout { .. } => "auth.logout",
            AuthCommand::Status => "auth.status",
        },
        Command::Draft { command } => match command {
            DraftCommand::List => "draft.list",
            DraftCommand::Create { .. } => "draft.create",
            DraftCommand::Show { .. } => "draft.show",
            DraftCommand::Set { .. } | DraftCommand::Unset { .. } | DraftCommand::Edit { .. } => {
                "draft.change"
            }
            DraftCommand::Delete { .. } => "draft.delete",
            DraftCommand::Submit { .. } => "draft.submit",
            DraftCommand::Status { .. } => "draft.status",
        },
        Command::Completion { .. } => "completion.generate",
        Command::Media { command } => match command {
            MediaCommand::Upload { .. } => "media.upload",
            MediaCommand::List { .. } => "media.list",
            MediaCommand::Status { .. } => "media.status",
            MediaCommand::Discard { .. } => "media.discard",
        },
        Command::Profile { command } => match command {
            ProfileCommand::Add { .. } => "profile.add",
            ProfileCommand::List => "profile.list",
            ProfileCommand::Show { .. } => "profile.show",
            ProfileCommand::Use { .. } => "profile.use",
            ProfileCommand::Remove { .. } => "profile.remove",
        },
        Command::Errors { command } => match command {
            ErrorsCommand::Show { .. } => "errors.show",
            ErrorsCommand::List => "errors.list",
        },
        Command::Submit { .. } => "submit",
        Command::Version => "version",
    }
}

#[derive(Debug)]
enum RunError {
    Io(io::Error),
    Command(CommandFailure),
}

impl From<CommandFailure> for RunError {
    fn from(value: CommandFailure) -> Self {
        Self::Command(value)
    }
}

impl From<io::Error> for RunError {
    fn from(value: io::Error) -> Self {
        Self::Io(value)
    }
}

#[cfg(test)]
mod tests {
    use clap::Parser;
    use tempfile::tempdir;

    use super::{
        AuthCommand, Cli, Command, DraftCommand, Locale, MediaCommand, MediaKindArgument,
        OutputFormat, resolve_locale, run, run_profile,
    };
    use squid_cli_core::profile::{ProfilePaths, ProfileStore};

    #[test]
    fn version_has_human_output() {
        let cli = Cli::try_parse_from(["squid", "version"]);
        assert!(cli.is_ok(), "command did not parse: {cli:?}");
        if let Ok(cli) = cli {
            let mut output = Vec::new();
            let result = run(cli, Locale::En, &mut output);
            assert!(result.is_ok(), "version command failed: {result:?}");
            let rendered = String::from_utf8(output);
            assert!(
                rendered.is_ok(),
                "version output was not UTF-8: {rendered:?}"
            );
            if let Ok(rendered) = rendered {
                assert!(rendered.starts_with("squid "));
                assert!(rendered.contains("submission protocol"));
            }
        }
    }

    #[test]
    fn version_has_json_envelope() {
        let cli = Cli::try_parse_from(["squid", "--output", "json", "version"]);
        assert!(cli.is_ok(), "command did not parse: {cli:?}");
        if let Ok(cli) = cli {
            let mut output = Vec::new();
            let result = run(cli, Locale::ZhCn, &mut output);
            assert!(result.is_ok(), "version command failed: {result:?}");
            let value = serde_json::from_slice::<serde_json::Value>(&output);
            assert!(value.is_ok(), "version output was not JSON: {value:?}");
            assert_eq!(
                value
                    .as_ref()
                    .ok()
                    .and_then(|item| item.get("schema_version"))
                    .and_then(serde_json::Value::as_u64),
                Some(1),
            );
            assert_eq!(
                value
                    .as_ref()
                    .ok()
                    .and_then(|item| item.get("command"))
                    .and_then(serde_json::Value::as_str),
                Some("version"),
            );
        }
    }

    #[test]
    fn completion_rejects_json_wrapping() {
        let cli = Cli::try_parse_from([
            "squid",
            "--output",
            "json",
            "completion",
            "generate",
            "bash",
        ]);
        assert!(cli.is_ok(), "command did not parse: {cli:?}");
        if let Ok(cli) = cli {
            let result = run(cli, Locale::En, &mut Vec::new());
            assert!(result.is_err());
        }
    }

    #[test]
    fn auth_commands_parse_explicit_security_choices() {
        let login = Cli::try_parse_from([
            "squid",
            "auth",
            "login",
            "--label",
            "Alice workstation",
            "--allow-file-fallback",
            "--timeout-seconds",
            "30",
        ]);
        assert!(matches!(
            login,
            Ok(Cli {
                command: Command::Auth {
                    command: AuthCommand::Login {
                        allow_file_fallback: true,
                        timeout_seconds: 30,
                        ..
                    },
                },
                ..
            })
        ));
        assert!(Cli::try_parse_from(["squid", "auth", "login", "--timeout-seconds", "0"]).is_err());
        assert!(Cli::try_parse_from(["squid", "auth", "logout", "--local-only"]).is_ok());
        assert!(Cli::try_parse_from(["squid", "auth", "status"]).is_ok());
    }

    #[test]
    fn draft_and_guided_submission_commands_parse_stable_inputs() {
        let draft_id = "64760b2f-b352-45e0-9ed1-67b9da901992";
        let set = Cli::try_parse_from([
            "squid",
            "--output",
            "json",
            "draft",
            "set",
            draft_id,
            "capture_width",
            "7",
        ]);
        assert!(matches!(
            set,
            Ok(Cli {
                output: OutputFormat::Json,
                command: Command::Draft {
                    command: DraftCommand::Set { field_id, value, .. },
                },
                ..
            }) if field_id == "capture_width" && value == "7"
        ));
        assert!(
            Cli::try_parse_from([
                "squid",
                "draft",
                "submit",
                draft_id,
                "--wait",
                "--timeout-seconds",
                "30",
            ])
            .is_ok()
        );
        assert!(Cli::try_parse_from(["squid", "submit", "door", "--wait"]).is_ok());
        assert!(
            Cli::try_parse_from([
                "squid",
                "draft",
                "submit",
                draft_id,
                "--timeout-seconds",
                "0",
            ])
            .is_err()
        );
        assert!(Cli::try_parse_from(["squid", "draft", "show", "not-a-uuid"]).is_err());
    }

    #[test]
    fn media_commands_parse_explicit_upload_policy() {
        let draft_id = "64760b2f-b352-45e0-9ed1-67b9da901992";
        let upload = Cli::try_parse_from([
            "squid",
            "media",
            "upload",
            draft_id,
            "video",
            "demo.mp4",
            "--strip-audio",
            "--wait",
            "--timeout-seconds",
            "60",
        ]);
        assert!(matches!(
            upload,
            Ok(Cli {
                command: Command::Media {
                    command: MediaCommand::Upload {
                        kind: MediaKindArgument::Video,
                        strip_audio: true,
                        wait: true,
                        timeout_seconds: 60,
                        ..
                    },
                },
                ..
            })
        ));
        assert!(Cli::try_parse_from(["squid", "media", "list", draft_id]).is_ok());
        assert!(
            Cli::try_parse_from([
                "squid",
                "media",
                "upload",
                draft_id,
                "image",
                "shot.png",
                "--timeout-seconds",
                "0",
            ])
            .is_err()
        );
    }

    #[test]
    fn explicit_unsupported_locale_is_rejected() {
        let result = resolve_locale(Some("is-IS"));
        assert!(result.is_err());
    }

    #[test]
    fn profile_lifecycle_has_stable_json() {
        let directory = tempdir();
        assert!(
            directory.is_ok(),
            "temporary directory failed: {directory:?}"
        );
        if let Ok(directory) = directory {
            let store = ProfileStore::new(ProfilePaths::under(directory.path()));
            let add = Cli::try_parse_from([
                "squid",
                "--output",
                "json",
                "profile",
                "add",
                "local",
                "--origin",
                "http://127.0.0.1:8000",
                "--trust",
                "--default-locale",
                "zh-CN",
                "--editor",
                "prompt",
                "--no-update-checks",
            ]);
            assert!(add.is_ok(), "profile add did not parse: {add:?}");
            if let Ok(add) = add {
                if let Command::Profile { command } = add.command {
                    let mut output = Vec::new();
                    let result =
                        run_profile(command, OutputFormat::Json, Locale::En, &store, &mut output);
                    assert!(result.is_ok(), "profile add failed: {result:?}");
                    let value = serde_json::from_slice::<serde_json::Value>(&output);
                    assert!(value.is_ok(), "profile add was not JSON: {value:?}");
                    if let Ok(value) = value {
                        assert_eq!(value["command"], "profile.add");
                        assert_eq!(value["data"]["name"], "local");
                        assert_eq!(value["data"]["origin"], "http://127.0.0.1:8000");
                        assert_eq!(value["data"]["locale"], "zh-CN");
                        assert_eq!(value["data"]["editor"], "prompt");
                        assert_eq!(value["data"]["update_checks"], false);
                    }
                }
            }

            let list = Cli::try_parse_from(["squid", "profile", "list"]);
            assert!(list.is_ok(), "profile list did not parse: {list:?}");
            if let Ok(list) = list {
                if let Command::Profile { command } = list.command {
                    let mut output = Vec::new();
                    let result = run_profile(
                        command,
                        OutputFormat::Human,
                        Locale::ZhCn,
                        &store,
                        &mut output,
                    );
                    assert!(result.is_ok(), "profile list failed: {result:?}");
                    let rendered = String::from_utf8(output);
                    assert!(rendered.is_ok(), "profile list was not UTF-8: {rendered:?}");
                    if let Ok(rendered) = rendered {
                        assert!(rendered.contains("* local"));
                        assert!(rendered.contains("来源：http://127.0.0.1:8000"));
                    }
                }
            }

            let remove = Cli::try_parse_from([
                "squid", "--output", "json", "profile", "remove", "local", "--yes",
            ]);
            assert!(remove.is_ok(), "profile remove did not parse: {remove:?}");
            if let Ok(remove) = remove {
                if let Command::Profile { command } = remove.command {
                    let mut output = Vec::new();
                    let result =
                        run_profile(command, OutputFormat::Json, Locale::En, &store, &mut output);
                    assert!(result.is_ok(), "profile remove failed: {result:?}");
                    let value = serde_json::from_slice::<serde_json::Value>(&output);
                    assert!(value.is_ok(), "profile remove was not JSON: {value:?}");
                    if let Ok(value) = value {
                        assert_eq!(value["data"]["removed"], true);
                        assert_eq!(value["data"]["purged_credential_backend"], "system");
                    }
                    let loaded = store.load();
                    assert!(loaded.is_ok(), "profile store failed: {loaded:?}");
                    if let Ok(loaded) = loaded {
                        assert!(loaded.profiles.is_empty());
                    }
                }
            }
        }
    }

    #[test]
    fn adding_profile_requires_explicit_trust() {
        let directory = tempdir();
        assert!(
            directory.is_ok(),
            "temporary directory failed: {directory:?}"
        );
        if let Ok(directory) = directory {
            let store = ProfileStore::new(ProfilePaths::under(directory.path()));
            let cli = Cli::try_parse_from([
                "squid",
                "profile",
                "add",
                "main",
                "--origin",
                "https://example.com",
            ]);
            assert!(cli.is_ok(), "profile add did not parse: {cli:?}");
            if let Ok(cli) = cli {
                if let Command::Profile { command } = cli.command {
                    let result = run_profile(
                        command,
                        OutputFormat::Human,
                        Locale::En,
                        &store,
                        &mut Vec::new(),
                    );
                    assert!(result.is_err());
                    let loaded = store.load();
                    assert!(loaded.is_ok(), "profile store failed: {loaded:?}");
                    if let Ok(loaded) = loaded {
                        assert!(loaded.profiles.is_empty());
                    }
                }
            }
        }
    }
}
