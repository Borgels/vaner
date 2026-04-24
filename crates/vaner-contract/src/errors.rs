//! Error type for engine-client operations.
//!
//! Mirrors the Swift `EngineClientError` enum case-for-case so both
//! sides map identical wire-level failures to identical client-visible
//! behaviours:
//!
//! | Wire                 | Swift case           | Rust variant      |
//! |----------------------|----------------------|-------------------|
//! | 400                  | `.invalidInput`      | `InvalidInput`    |
//! | 404                  | `.notFound`          | `NotFound`        |
//! | 409                  | `.engineUnavailable` | `EngineUnavailable` |
//! | non-2xx other        | `.http(status:,message:)` | `Http { .. }` |
//! | malformed body       | `.badResponse`       | `BadResponse`     |
//! | transport failure    | (mapped to `.http`)  | `Transport`       |
//!
//! The Rust side distinguishes transport-level failures from HTTP status
//! errors so callers can apply different retry/backoff policies without
//! string-matching on error messages.

use thiserror::Error;

#[derive(Debug, Error)]
pub enum EngineClientError {
    /// Response body couldn't be decoded (JSON shape drift, truncated body).
    #[error("invalid response from cockpit: {0}")]
    BadResponse(String),

    /// HTTP 400 — empty/whitespace id, malformed request.
    #[error("invalid input")]
    InvalidInput,

    /// HTTP 404 — requested prediction/scenario no longer exists.
    #[error("not found")]
    NotFound,

    /// HTTP 409 — daemon is up but has no live prediction registry.
    #[error("engine unavailable")]
    EngineUnavailable,

    /// Other non-2xx HTTP status.
    #[error("HTTP {status}: {message}")]
    Http { status: u16, message: String },

    /// Transport-level failure (connection refused, DNS, TLS). Distinct
    /// from HTTP errors so callers can decide whether to retry or
    /// surface an "engine down" state.
    #[error("transport error: {0}")]
    Transport(String),

    /// SSE stream parse / protocol error.
    #[cfg(feature = "sse")]
    #[error("SSE stream error: {0}")]
    Stream(String),
}

#[cfg(feature = "http")]
impl From<reqwest::Error> for EngineClientError {
    fn from(err: reqwest::Error) -> Self {
        if let Some(status) = err.status() {
            match status.as_u16() {
                400 => Self::InvalidInput,
                404 => Self::NotFound,
                409 => Self::EngineUnavailable,
                other => Self::Http {
                    status: other,
                    message: err.to_string(),
                },
            }
        } else {
            Self::Transport(err.to_string())
        }
    }
}

impl From<serde_json::Error> for EngineClientError {
    fn from(err: serde_json::Error) -> Self {
        Self::BadResponse(err.to_string())
    }
}
