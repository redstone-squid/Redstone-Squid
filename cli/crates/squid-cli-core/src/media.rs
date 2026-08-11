//! Owner-scoped draft media upload and processing contract.

use std::path::Path;

use serde::{Deserialize, Serialize};
use thiserror::Error;
use uuid::Uuid;

use crate::credential::SecretBytes;
use crate::transport::{ApiClient, ApiMethod, ApiRequest, ApiResponse, TransportError};

const MAXIMUM_MEDIA_ITEMS: usize = 13;

/// Source kind accepted by the normalization pipeline.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum MediaKind {
    Image,
    Video,
}

impl MediaKind {
    #[must_use]
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Image => "image",
            Self::Video => "video",
        }
    }
}

/// Server-advertised source and normalization budgets.
#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct MediaLimits {
    pub max_upload_bytes: u64,
    pub max_images: u32,
    pub max_videos: u32,
    pub max_output_bytes: u64,
    pub max_duration_milliseconds: u64,
    pub max_pixels_per_frame: u64,
    pub max_decoded_pixels_per_second: u64,
}

/// Safe normalized artifact facts retained for an owner.
#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct MediaArtifact {
    pub role: String,
    pub content_type: String,
    pub width: u32,
    pub height: u32,
}

/// One durable normalization job projected onto public states.
#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct DraftMedia {
    pub id: Uuid,
    pub draft_id: Uuid,
    pub kind: String,
    pub status: String,
    pub source_content_type: String,
    pub artifacts: Vec<MediaArtifact>,
}

impl DraftMedia {
    /// Reject cross-draft, unknown-state, or malformed media responses before display.
    pub fn validate(&self, expected_draft_id: Uuid) -> Result<(), MediaContractError> {
        if self.id.is_nil() || self.draft_id != expected_draft_id {
            return Err(MediaContractError::MismatchedIdentifier);
        }
        if !matches!(self.kind.as_str(), "image" | "video")
            || !matches!(
                self.status.as_str(),
                "processing" | "completed" | "dead" | "discarded"
            )
            || self.artifacts.len() > 2
            || self.artifacts.iter().any(|artifact| {
                !matches!(artifact.role.as_str(), "output" | "poster")
                    || artifact.width == 0
                    || artifact.height == 0
            })
        {
            return Err(MediaContractError::InvalidResponse);
        }
        Ok(())
    }

    #[must_use]
    pub fn is_terminal(&self) -> bool {
        matches!(self.status.as_str(), "completed" | "dead" | "discarded")
    }
}

/// Complete bounded media state for one draft.
#[derive(Debug, Deserialize, Serialize)]
pub struct DraftMediaList {
    pub limits: MediaLimits,
    pub media: Vec<DraftMedia>,
}

impl DraftMediaList {
    pub fn validate(&self, expected_draft_id: Uuid) -> Result<(), MediaContractError> {
        if self.limits.max_upload_bytes == 0
            || self.limits.max_images == 0
            || self.limits.max_videos == 0
            || self.media.len() > MAXIMUM_MEDIA_ITEMS
        {
            return Err(MediaContractError::InvalidResponse);
        }
        for media in &self.media {
            media.validate(expected_draft_id)?;
        }
        let unique = self
            .media
            .iter()
            .map(|media| media.id)
            .collect::<std::collections::BTreeSet<_>>();
        if unique.len() != self.media.len() {
            return Err(MediaContractError::MismatchedIdentifier);
        }
        Ok(())
    }
}

/// Media operations bound to one trusted API origin.
#[derive(Clone, Copy, Debug)]
pub struct SubmissionMediaApi<'a> {
    client: &'a ApiClient,
}

impl<'a> SubmissionMediaApi<'a> {
    #[must_use]
    pub const fn new(client: &'a ApiClient) -> Self {
        Self { client }
    }

    pub fn list(
        &self,
        draft_id: Uuid,
        token: &SecretBytes,
    ) -> Result<ApiResponse<DraftMediaList>, TransportError> {
        self.client.send_json(
            ApiRequest::new(
                ApiMethod::Get,
                format!("/api/v1/submissions/drafts/{draft_id}/media"),
            ),
            Some(token),
        )
    }

    pub fn get(
        &self,
        draft_id: Uuid,
        upload_id: Uuid,
        token: &SecretBytes,
    ) -> Result<ApiResponse<DraftMedia>, TransportError> {
        self.client.send_json(
            ApiRequest::new(
                ApiMethod::Get,
                format!("/api/v1/submissions/drafts/{draft_id}/media/{upload_id}"),
            ),
            Some(token),
        )
    }

    #[allow(clippy::too_many_arguments)]
    pub fn upload(
        &self,
        draft_id: Uuid,
        kind: MediaKind,
        path: &Path,
        content_type: &str,
        strip_audio: bool,
        upload_id: Uuid,
        maximum_bytes: u64,
        token: &SecretBytes,
    ) -> Result<ApiResponse<DraftMedia>, TransportError> {
        let request = ApiRequest::new(
            ApiMethod::Post,
            format!(
                "/api/v1/submissions/drafts/{draft_id}/media/{}",
                kind.as_str()
            ),
        )
        .with_query_param("upload_id", upload_id.hyphenated().to_string())?
        .with_query_param("strip_audio", strip_audio.to_string())?
        .with_file(path, content_type, maximum_bytes)?;
        self.client.send_json(request, Some(token))
    }

    pub fn discard(
        &self,
        draft_id: Uuid,
        upload_id: Uuid,
        token: &SecretBytes,
        idempotency_key: Uuid,
    ) -> Result<ApiResponse<()>, TransportError> {
        self.client.send_no_content(
            ApiRequest::new(
                ApiMethod::Delete,
                format!("/api/v1/submissions/drafts/{draft_id}/media/{upload_id}"),
            )
            .with_idempotency_key(idempotency_key),
            Some(token),
        )
    }
}

/// Invalid or cross-resource media data returned by a server.
#[derive(Debug, Error)]
pub enum MediaContractError {
    #[error("the media response contains a mismatched or duplicate identifier")]
    MismatchedIdentifier,
    #[error("the media response exceeds bounds or contains an unknown state")]
    InvalidResponse,
}

#[cfg(test)]
mod tests {
    use uuid::Uuid;

    use super::{DraftMedia, DraftMediaList, MediaArtifact, MediaContractError, MediaLimits};

    fn media(draft_id: Uuid) -> DraftMedia {
        DraftMedia {
            id: Uuid::new_v4(),
            draft_id,
            kind: String::from("image"),
            status: String::from("completed"),
            source_content_type: String::from("image/png"),
            artifacts: vec![MediaArtifact {
                role: String::from("output"),
                content_type: String::from("image/webp"),
                width: 100,
                height: 80,
            }],
        }
    }

    #[test]
    fn validates_bounded_owner_scoped_media() {
        let draft_id = Uuid::new_v4();
        let item = media(draft_id);
        assert!(item.validate(draft_id).is_ok());
        assert!(matches!(
            item.validate(Uuid::new_v4()),
            Err(MediaContractError::MismatchedIdentifier),
        ));
        let list = DraftMediaList {
            limits: MediaLimits {
                max_upload_bytes: 500,
                max_images: 10,
                max_videos: 3,
                max_output_bytes: 500,
                max_duration_milliseconds: 300_000,
                max_pixels_per_frame: 33_200_000,
                max_decoded_pixels_per_second: 250_000_000,
            },
            media: vec![item],
        };
        assert!(list.validate(draft_id).is_ok());
    }
}
