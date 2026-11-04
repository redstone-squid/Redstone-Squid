//! Server form definitions and retained submission drafts used by the CLI.
//!
//! A draft stores answers on the server so editing can continue across invocations and clients.
//! Its pinned form manifest defines the fields, choices, and validation rules for those answers.
//! This adapter resolves server field definitions into the controls in `form`; `SubmissionApi`
//! provides the requests used to create drafts, save edits, and finalize them into builds.

use std::collections::{BTreeMap, BTreeSet};

use serde::{Deserialize, Serialize};
use serde_json::{Number, Value};
use thiserror::Error;
use uuid::Uuid;

use crate::credential::SecretBytes;
use crate::form::{
    CapabilityAssessment, CapabilityRequirement, ChoiceOption, FormCode, FormControl, FormError,
    FormField, RendererCapabilities,
};
use crate::transport::{ApiClient, ApiMethod, ApiRequest, ApiResponse, TransportError};
use crate::version::{MAXIMUM_PROTOCOL, MINIMUM_PROTOCOL};

const MAXIMUM_DISCOVERED_DRAFTS: usize = 10;

/// A versioned form definition returned by the server.
///
/// A draft's `schema_id` and `schema_revision` select its manifest. Resuming that draft uses
/// the pinned revision even when the server offers a newer form for newly created drafts.
#[derive(Debug, Deserialize)]
pub struct FormManifest {
    pub schema_id: String,
    pub revision: u32,
    pub minimum_protocol: u32,
    pub maximum_protocol: u32,
    pub common_sections: Vec<FormSection>,
    pub categories: Vec<CategoryForm>,
}

/// One category-specific form definition.
#[derive(Debug, Deserialize)]
pub struct CategoryForm {
    pub code: String,
    pub label: String,
    pub sections: Vec<FormSection>,
}

/// Ordered group of form fields.
#[derive(Debug, Deserialize)]
pub struct FormSection {
    pub id: String,
    pub title: String,
    pub fields: Vec<FormFieldSchema>,
}

/// Narrow control kinds authored by the backend.
#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq)]
#[serde(rename_all = "snake_case")]
pub enum ControlKind {
    Text,
    Number,
    Choice,
    MultiChoice,
    Duration,
    Boolean,
    #[serde(other)]
    Unknown,
}

/// Canonical JSON value kinds authored by the backend.
#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq)]
#[serde(rename_all = "snake_case")]
pub enum ValueKind {
    String,
    Integer,
    Number,
    Boolean,
    StringList,
    GameTicks,
    #[serde(other)]
    Unknown,
}

/// One inline or dynamically fetched stable choice.
#[derive(Clone, Debug, Deserialize, Eq, PartialEq)]
pub struct FormOption {
    pub value: String,
    pub label: String,
}

/// Renderer-neutral field constraints.
#[derive(Clone, Debug, Deserialize)]
pub struct FieldConstraints {
    pub minimum: Option<Number>,
    pub maximum: Option<Number>,
    pub min_length: Option<usize>,
    pub max_length: Option<usize>,
    pub min_items: Option<usize>,
    pub max_items: Option<usize>,
    pub must_equal: Value,
}

/// Declarative visibility condition.
#[derive(Clone, Debug, Deserialize)]
pub struct VisibilityRule {
    pub field_id: String,
    pub operator: VisibilityOperator,
    pub value: Value,
}

/// Operators understood by the v1 visibility language.
#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq)]
#[serde(rename_all = "snake_case")]
pub enum VisibilityOperator {
    Equals,
    NotEquals,
    In,
    #[serde(other)]
    Unknown,
}

/// One untrusted server field before conversion into the bounded terminal model.
#[derive(Debug, Deserialize)]
pub struct FormFieldSchema {
    pub id: String,
    pub label: String,
    pub control: ControlKind,
    pub value_kind: ValueKind,
    pub required: bool,
    pub help_text: Option<String>,
    pub constraints: FieldConstraints,
    pub options: Vec<FormOption>,
    pub option_source: Option<String>,
    pub visible_when: Option<VisibilityRule>,
    pub default: Value,
    pub repeatable: bool,
    pub required_capability: Option<String>,
    pub origins: Vec<String>,
}

impl FormManifest {
    /// Check protocol overlap, category codes, and the requested category's field identifiers.
    ///
    /// Also reject excessive inline options or fields mixing inline and dynamic options.
    /// Control support and constraints are checked later during field adaptation; this is
    /// not a complete validation of every category in the manifest.
    pub fn validate_for_category(&self, category: &str) -> Result<(), SubmissionContractError> {
        if self.revision == 0
            || self.minimum_protocol == 0
            || self.maximum_protocol < self.minimum_protocol
            || self.minimum_protocol > MAXIMUM_PROTOCOL
            || self.maximum_protocol < MINIMUM_PROTOCOL
        {
            return Err(SubmissionContractError::IncompatibleProtocol);
        }
        if self.category(category).is_none() {
            return Err(SubmissionContractError::UnknownCategory);
        }
        let mut category_codes = BTreeSet::new();
        for category in &self.categories {
            FormCode::parse(&category.code)?;
            if !category_codes.insert(&category.code) {
                return Err(SubmissionContractError::DuplicateIdentifier);
            }
        }
        let mut field_codes = BTreeSet::new();
        for field in self.fields_for(category)? {
            FormCode::parse(&field.id)?;
            if !field_codes.insert(&field.id) {
                return Err(SubmissionContractError::DuplicateIdentifier);
            }
            if field.options.len() > 500
                || (!field.options.is_empty() && field.option_source.is_some())
            {
                return Err(SubmissionContractError::InvalidField);
            }
        }
        Ok(())
    }

    /// Stable category codes in server display order.
    #[must_use]
    pub fn category_codes(&self) -> Vec<&str> {
        self.categories
            .iter()
            .map(|category| category.code.as_str())
            .collect()
    }

    /// Return common fields followed by category fields in server display order.
    ///
    /// Visibility rules are not applied here.
    pub fn fields_for(
        &self,
        category: &str,
    ) -> Result<Vec<&FormFieldSchema>, SubmissionContractError> {
        let category = self
            .category(category)
            .ok_or(SubmissionContractError::UnknownCategory)?;
        Ok(self
            .common_sections
            .iter()
            .chain(category.sections.iter())
            .flat_map(|section| section.fields.iter())
            .collect())
    }

    /// Assess declared capabilities for fields currently visible to the CLI.
    ///
    /// This checks `required_capability` declarations only. A field with no declaration can
    /// still fail adaptation or require a control the selected renderer does not support.
    pub fn assess_capabilities(
        &self,
        category: &str,
        answers: &BTreeMap<String, Value>,
        capabilities: &RendererCapabilities,
    ) -> Result<CapabilityAssessment, SubmissionContractError> {
        let requirements = self
            .fields_for(category)?
            .into_iter()
            .filter(|field| field.is_visible(answers))
            .filter_map(|field| {
                field
                    .required_capability
                    .as_ref()
                    .map(|code| CapabilityRequirement {
                        code: code.clone(),
                        required: field.required,
                    })
            })
            .collect::<Vec<_>>();
        Ok(capabilities.assess(&requirements)?)
    }

    fn category(&self, code: &str) -> Option<&CategoryForm> {
        self.categories
            .iter()
            .find(|category| category.code == code)
    }
}

impl FormFieldSchema {
    /// Apply the CLI origin filter and this field's visibility rule to current answers.
    ///
    /// Missing answers compare as JSON null. Unknown operators and non-array `in` operands
    /// hide the field. Defaults are not added to the answer map here.
    #[must_use]
    pub fn is_visible(&self, answers: &BTreeMap<String, Value>) -> bool {
        if !self.origins.iter().any(|origin| origin == "cli") {
            return false;
        }
        let Some(rule) = &self.visible_when else {
            return true;
        };
        let actual = answers.get(&rule.field_id).unwrap_or(&Value::Null);
        match rule.operator {
            VisibilityOperator::Equals => actual == &rule.value,
            VisibilityOperator::NotEquals => actual != &rule.value,
            VisibilityOperator::In => rule
                .value
                .as_array()
                .is_some_and(|values| values.contains(actual)),
            VisibilityOperator::Unknown => false,
        }
    }

    /// Convert a supported control/value pair into a validated CLI field.
    ///
    /// Supplied dynamic options replace inline options. The caller resolves option sources
    /// and visibility first. Numeric bounds must fit i64; duration fields are whole game ticks.
    /// Defaults and `must_equal` are not applied here, and the server remains responsible for
    /// final validation.
    pub fn adapt(
        &self,
        dynamic_options: Option<&[FormOption]>,
    ) -> Result<AdaptedFormField, SubmissionContractError> {
        let code = FormCode::parse(&self.id)?;
        let options = dynamic_options.unwrap_or(&self.options);
        let choices = || {
            options
                .iter()
                .map(|option| {
                    Ok(ChoiceOption {
                        code: FormCode::parse(&option.value)?,
                        label: option.label.clone(),
                    })
                })
                .collect::<Result<Vec<_>, FormError>>()
        };
        let minimum = integer_constraint(self.constraints.minimum.as_ref())?;
        let maximum = integer_constraint(self.constraints.maximum.as_ref())?;
        let control = match (self.control, self.value_kind, self.repeatable) {
            (ControlKind::Text, ValueKind::String, false) => FormControl::Text {
                minimum_characters: self.constraints.min_length,
                maximum_characters: self.constraints.max_length,
            },
            (ControlKind::Text, ValueKind::StringList, true) => FormControl::RepeatableText {
                minimum_items: self.constraints.min_items,
                maximum_items: self.constraints.max_items,
                minimum_characters: self.constraints.min_length,
                maximum_characters: self.constraints.max_length,
            },
            (
                ControlKind::Number | ControlKind::Duration,
                ValueKind::Integer | ValueKind::GameTicks,
                false,
            ) => FormControl::Integer { minimum, maximum },
            (ControlKind::Boolean, ValueKind::Boolean, false) => FormControl::Boolean,
            (ControlKind::Choice, ValueKind::String, false) if !options.is_empty() => {
                FormControl::SingleChoice {
                    options: choices()?,
                }
            }
            (ControlKind::MultiChoice, ValueKind::StringList, false) if !options.is_empty() => {
                FormControl::MultipleChoice {
                    options: choices()?,
                    minimum_selections: self.constraints.min_items,
                    maximum_selections: self.constraints.max_items,
                }
            }
            _ => return Err(SubmissionContractError::UnsupportedControl),
        };
        let field = FormField {
            code,
            label: self.label.clone(),
            description: self.help_text.clone(),
            required: self.required,
            control,
        };
        field.validate()?;
        Ok(AdaptedFormField {
            field,
            repeatable: self.repeatable,
        })
    }
}

fn integer_constraint(value: Option<&Number>) -> Result<Option<i64>, SubmissionContractError> {
    value
        .map(|value| {
            value
                .as_i64()
                .ok_or(SubmissionContractError::UnsupportedControl)
        })
        .transpose()
}

/// Internal field plus conversion metadata retained by the API adapter.
#[derive(Debug)]
pub struct AdaptedFormField {
    pub field: FormField,
    pub repeatable: bool,
}

/// Dynamic option-source response.
#[derive(Debug, Deserialize)]
pub struct FormOptionSet {
    pub source: String,
    pub category: String,
    pub revision: u32,
    pub options: Vec<FormOption>,
}

/// One active submission draft's metadata for discovery, without its answers.
#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct DraftSummary {
    pub id: Uuid,
    pub schema_id: String,
    pub schema_revision: u32,
    pub category: String,
    pub revision: u64,
    pub status: String,
    pub origin: String,
    pub display_name: Option<String>,
    pub created_at: String,
    pub updated_at: String,
    pub expires_at: String,
}

/// Bounded account-owned active draft list.
#[derive(Debug, Deserialize, Serialize)]
pub struct DraftList {
    pub drafts: Vec<DraftSummary>,
}

impl DraftList {
    /// Validate server bounds and duplicate IDs before displaying or caching the list.
    pub fn validate(&self) -> Result<(), SubmissionContractError> {
        if self.drafts.len() > MAXIMUM_DISCOVERED_DRAFTS {
            return Err(SubmissionContractError::TooManyDrafts);
        }
        let ids = self
            .drafts
            .iter()
            .map(|draft| draft.id)
            .collect::<BTreeSet<_>>();
        if ids.len() != self.drafts.len() {
            return Err(SubmissionContractError::DuplicateIdentifier);
        }
        Ok(())
    }
}

/// A snapshot of a submission draft retained by the server, including its answers.
///
/// `schema_revision` identifies the form definition; `revision` identifies the draft state
/// used for edit-conflict checks. Saving an answer advances the draft revision while retaining
/// the same form definition.
#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct StoredDraft {
    pub id: Uuid,
    pub schema_id: String,
    pub schema_revision: u32,
    pub category: String,
    pub revision: u64,
    pub status: String,
    pub answers: BTreeMap<String, Value>,
    pub origin: String,
    pub created_at: String,
    pub updated_at: String,
    pub expires_at: String,
    pub source_installation_id: Option<Uuid>,
}

#[derive(Debug, Serialize)]
struct DraftCreateRequest<'a> {
    category: &'a str,
    origin: &'static str,
    client_capabilities: &'a [String],
}

/// Stable-field mutation kind.
#[derive(Clone, Copy, Debug, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum FieldOperationKind {
    Set,
    Unset,
}

/// One set or unset operation identified for server-side replay handling.
#[derive(Debug, Serialize)]
pub struct FieldOperation {
    pub operation_id: Uuid,
    pub field_id: String,
    pub kind: FieldOperationKind,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub value: Option<Value>,
}

/// A batch of field operations against one expected draft revision.
///
/// The server checks the revision and applies the batch atomically. Callers supply operation
/// IDs and an idempotency key so a lost response can be retried as the same request.
#[derive(Debug, Serialize)]
pub struct DraftChangeRequest {
    pub base_revision: u64,
    pub client_instance_id: String,
    pub idempotency_key: String,
    pub operations: Vec<FieldOperation>,
}

/// Accepted change and replay indicator.
#[derive(Debug, Deserialize, Serialize)]
pub struct DraftChangeResponse {
    pub draft: StoredDraft,
    pub replayed: bool,
}

/// One actionable finalization issue.
#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct SubmissionIssue {
    pub field_id: String,
    pub reason: String,
}

/// Server-retained progress of turning a submission draft into a build.
///
/// `build_id` identifies the resulting build when completed. Issues describe answers that need
/// attention; callers inspect `status` to distinguish completion, pending work, and failures.
#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct SubmissionFinalization {
    pub draft_id: Uuid,
    pub draft_revision: u64,
    pub status: String,
    pub issues: Vec<SubmissionIssue>,
    pub build_id: Option<i64>,
}

/// Submission operations through an origin-bound HTTP client.
///
/// Methods deserialize responses but do not validate manifests, draft lists, or returned IDs.
/// Callers perform those checks and retain request IDs and idempotency keys needed for recovery.
#[derive(Clone, Copy, Debug)]
pub struct SubmissionApi<'a> {
    client: &'a ApiClient,
}

impl<'a> SubmissionApi<'a> {
    #[must_use]
    pub const fn new(client: &'a ApiClient) -> Self {
        Self { client }
    }

    pub fn current_form(
        &self,
        token: Option<&SecretBytes>,
    ) -> Result<ApiResponse<FormManifest>, TransportError> {
        self.client.send_json(
            ApiRequest::new(ApiMethod::Get, "/v1/submissions/form/current"),
            token,
        )
    }

    pub fn pinned_form(
        &self,
        schema_id: &str,
        revision: u32,
        token: Option<&SecretBytes>,
    ) -> Result<ApiResponse<FormManifest>, TransportError> {
        FormCode::parse(schema_id).map_err(|_error| TransportError::InvalidEndpointPath)?;
        if revision == 0 {
            return Err(TransportError::InvalidEndpointPath);
        }
        self.client.send_json(
            ApiRequest::new(
                ApiMethod::Get,
                format!("/v1/submissions/form/schemas/{schema_id}/revisions/{revision}"),
            ),
            token,
        )
    }

    pub fn form_options(
        &self,
        source: &str,
        category: &str,
        token: Option<&SecretBytes>,
    ) -> Result<ApiResponse<FormOptionSet>, TransportError> {
        FormCode::parse(source).map_err(|_error| TransportError::InvalidEndpointPath)?;
        FormCode::parse(category).map_err(|_error| TransportError::InvalidQueryParameter)?;
        self.client.send_json(
            ApiRequest::new(
                ApiMethod::Get,
                format!("/v1/submissions/form/options/{source}"),
            )
            .with_query_param("category", category)?,
            token,
        )
    }

    pub fn list_drafts(
        &self,
        token: &SecretBytes,
    ) -> Result<ApiResponse<DraftList>, TransportError> {
        self.client.send_json(
            ApiRequest::new(ApiMethod::Get, "/v1/submissions/drafts"),
            Some(token),
        )
    }

    pub fn create_draft(
        &self,
        category: &str,
        capabilities: &[String],
        token: &SecretBytes,
        idempotency_key: Uuid,
    ) -> Result<ApiResponse<StoredDraft>, TransportError> {
        FormCode::parse(category).map_err(|_error| TransportError::InvalidEndpointPath)?;
        self.client.send_json(
            ApiRequest::new(ApiMethod::Post, "/v1/submissions/drafts")
                .with_json(&DraftCreateRequest {
                    category,
                    origin: "cli",
                    client_capabilities: capabilities,
                })?
                .with_idempotency_key(idempotency_key),
            Some(token),
        )
    }

    pub fn get_draft(
        &self,
        draft_id: Uuid,
        token: &SecretBytes,
    ) -> Result<ApiResponse<StoredDraft>, TransportError> {
        self.client.send_json(
            ApiRequest::new(
                ApiMethod::Get,
                format!("/v1/submissions/drafts/{draft_id}"),
            ),
            Some(token),
        )
    }

    /// Submit a change batch with an idempotency header matching the key in its body.
    ///
    /// This method does not check that the two caller-provided keys agree.
    pub fn change_draft(
        &self,
        draft_id: Uuid,
        change: &DraftChangeRequest,
        token: &SecretBytes,
        idempotency_key: Uuid,
    ) -> Result<ApiResponse<DraftChangeResponse>, TransportError> {
        self.client.send_json(
            ApiRequest::new(
                ApiMethod::Post,
                format!("/v1/submissions/drafts/{draft_id}/changes"),
            )
            .with_json(change)?
            .with_idempotency_key(idempotency_key),
            Some(token),
        )
    }

    pub fn delete_draft(
        &self,
        draft_id: Uuid,
        token: &SecretBytes,
        idempotency_key: Uuid,
    ) -> Result<ApiResponse<()>, TransportError> {
        self.client.send_no_content(
            ApiRequest::new(
                ApiMethod::Delete,
                format!("/v1/submissions/drafts/{draft_id}"),
            )
            .with_idempotency_key(idempotency_key),
            Some(token),
        )
    }

    /// Request finalization and return its current state; completion may require polling.
    pub fn submit_draft(
        &self,
        draft_id: Uuid,
        token: &SecretBytes,
        idempotency_key: Uuid,
    ) -> Result<ApiResponse<SubmissionFinalization>, TransportError> {
        self.client.send_json(
            ApiRequest::new(
                ApiMethod::Post,
                format!("/v1/submissions/drafts/{draft_id}/submission"),
            )
            .with_idempotency_key(idempotency_key),
            Some(token),
        )
    }

    pub fn submission_status(
        &self,
        draft_id: Uuid,
        token: &SecretBytes,
    ) -> Result<ApiResponse<SubmissionFinalization>, TransportError> {
        self.client.send_json(
            ApiRequest::new(
                ApiMethod::Get,
                format!("/v1/submissions/drafts/{draft_id}/submission"),
            ),
            Some(token),
        )
    }
}

/// Invalid or unsupported server submission contract.
#[derive(Debug, Error)]
pub enum SubmissionContractError {
    #[error("the form does not overlap this CLI's supported submission protocol")]
    IncompatibleProtocol,
    #[error("the form does not define the requested category")]
    UnknownCategory,
    #[error("the form contains a duplicate identifier")]
    DuplicateIdentifier,
    #[error("the form field is malformed")]
    InvalidField,
    #[error("the form field uses an unsupported control or value kind")]
    UnsupportedControl,
    #[error("the draft list exceeds the client safety bound")]
    TooManyDrafts,
    #[error(transparent)]
    Form(#[from] FormError),
}

#[cfg(test)]
mod tests {
    use std::collections::BTreeMap;
    use std::error::Error;

    use serde_json::json;

    use super::{FormManifest, FormOption, SubmissionContractError};
    use crate::form::{FormControl, RendererCapabilities};

    fn manifest() -> Result<FormManifest, serde_json::Error> {
        serde_json::from_value(json!({
            "schema_id": "build_submission.v1",
            "revision": 1,
            "minimum_protocol": 1,
            "maximum_protocol": 1,
            "common_sections": [{
                "id": "common",
                "title": "Common",
                "fields": [{
                    "id": "creators",
                    "label": "Creators",
                    "control": "text",
                    "value_kind": "string_list",
                    "required": true,
                    "help_text": null,
                    "constraints": {"minimum": null, "maximum": null, "min_length": 1, "max_length": 80, "min_items": 1, "max_items": 10, "must_equal": null},
                    "options": [],
                    "option_source": null,
                    "visible_when": null,
                    "default": null,
                    "repeatable": true,
                    "required_capability": "repeatable_text",
                    "origins": ["cli", "web"]
                }]
            }],
            "categories": [{
                "code": "door",
                "label": "Door",
                "sections": [{
                    "id": "door_details",
                    "title": "Door",
                    "fields": [{
                        "id": "orientation",
                        "label": "Orientation",
                        "control": "choice",
                        "value_kind": "string",
                        "required": true,
                        "help_text": null,
                        "constraints": {"minimum": null, "maximum": null, "min_length": null, "max_length": null, "min_items": null, "max_items": null, "must_equal": null},
                        "options": [],
                        "option_source": "approved_orientations",
                        "visible_when": null,
                        "default": null,
                        "repeatable": false,
                        "required_capability": null,
                        "origins": ["cli"]
                    }]
                }]
            }]
        }))
    }

    #[test]
    fn adapts_repeatable_and_dynamic_fields_without_losing_stable_codes()
    -> Result<(), Box<dyn Error>> {
        let manifest = manifest()?;
        manifest.validate_for_category("door")?;
        let answers = BTreeMap::new();
        let assessment =
            manifest.assess_capabilities("door", &answers, &RendererCapabilities::tui())?;
        assert!(!assessment.web_continuation_required);
        let fields = manifest.fields_for("door")?;
        let creators = fields[0].adapt(None)?;
        assert!(creators.repeatable);
        assert!(matches!(
            creators.field.control,
            FormControl::RepeatableText { .. }
        ));
        let orientation = fields[1].adapt(Some(&[FormOption {
            value: String::from("floor"),
            label: String::from("Floor"),
        }]))?;
        assert!(matches!(
            orientation.field.control,
            FormControl::SingleChoice { .. }
        ));
        Ok(())
    }

    #[test]
    fn fails_closed_for_unknown_required_capabilities_and_protocols() -> Result<(), Box<dyn Error>>
    {
        let mut manifest = manifest()?;
        manifest.common_sections[0].fields[0].required_capability =
            Some(String::from("cli.control.future.v1"));
        let assessment = manifest.assess_capabilities(
            "door",
            &BTreeMap::new(),
            &RendererCapabilities::prompt(false),
        )?;
        assert!(assessment.web_continuation_required);
        manifest.minimum_protocol = 2;
        assert!(matches!(
            manifest.validate_for_category("door"),
            Err(SubmissionContractError::IncompatibleProtocol)
        ));
        Ok(())
    }
}
