//! Cross-platform contract integration layer for Vaner desktop clients.
//!
//! See the crate-level `README.md` for the policy: Linux/Windows Tauri
//! apps consume this crate directly; macOS Swift runs the same
//! conformance fixtures through its own `Codable` models.
//!
//! # Quick start
//!
//! ```no_run
//! # #[cfg(all(feature = "http", feature = "sse"))]
//! # async fn demo() -> Result<(), vaner_contract::EngineClientError> {
//! use vaner_contract::{EngineClient, HttpEngineClient};
//!
//! let client = HttpEngineClient::new("http://127.0.0.1:8473".parse().unwrap());
//! let predictions = client.active_predictions().await?;
//! for p in predictions {
//!     println!("{}: {:?}", p.id, p.run.readiness);
//! }
//! # Ok(()) }
//! ```

#![deny(unsafe_code)]

pub mod enums;
pub mod errors;
pub mod handoff;
pub mod models;
pub mod reducer;

#[cfg(feature = "http")]
pub mod http;

#[cfg(feature = "sse")]
pub mod sse;

#[cfg(feature = "ts-rs")]
pub mod ts;

pub use enums::{HypothesisType, PredictionSource, Readiness, Specificity};
pub use errors::EngineClientError;
pub use handoff::{AdoptHandoffError, handoff_path, stash_adopt};
pub use models::{
    EngineStatus, PredictedPrompt, PredictionArtifacts, PredictionRun, PredictionSpec, Provenance,
    Resolution, ResolutionAlternative, ResolutionEvidence,
};
pub use reducer::{ReducerInputs, VanerState, reduce};

#[cfg(feature = "http")]
pub use http::{EngineClient, HttpEngineClient};

#[cfg(feature = "sse")]
pub use sse::stream_prediction_events;
