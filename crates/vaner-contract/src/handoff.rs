//! Adopt-handoff file-drop contract.
//!
//! When the user clicks Adopt on a prediction row, the desktop drops
//! the full [`Resolution`] as JSON at a well-known path so the AI
//! agent's next-turn skill (`/vaner:next` in Claude Code, equivalents
//! elsewhere) can inject the prepared package verbatim.
//!
//! The raw server bytes travel alongside the decoded `Resolution` — see
//! [`crate::http::EngineClient::adopt`] — so this function preserves
//! any unknown top-level server keys (future additive `next_actions`,
//! `gaps`, `metrics`, …) by rewriting from the raw JSON rather than
//! re-serializing the decoded struct.
//!
//! # Paths per OS
//!
//! | OS      | Path                                            |
//! |---------|-------------------------------------------------|
//! | Linux   | `$XDG_STATE_HOME/vaner/pending-adopt.json`      |
//! | macOS   | `~/Library/Application Support/Vaner/pending-adopt.json` |
//! | Windows | `%LOCALAPPDATA%\Vaner\pending-adopt.json`       |
//!
//! On Linux `$XDG_STATE_HOME` is resolved via the `xdg` crate with the
//! spec-compliant fallback to `~/.local/state`.

use std::io::Write as _;
use std::path::{Path, PathBuf};

use serde_json::{Map, Value};
use thiserror::Error;

use crate::models::Resolution;

#[derive(Debug, Error)]
pub enum AdoptHandoffError {
    #[error("could not resolve handoff directory: {0}")]
    NoHandoffDir(String),
    #[error("filesystem error: {0}")]
    Io(#[from] std::io::Error),
    #[error("json error: {0}")]
    Json(#[from] serde_json::Error),
}

/// Return the platform-canonical path where the handoff file lives.
///
/// Falls back to `./pending-adopt.json` only in the pathological case
/// where no home directory can be determined (CI containers with
/// `$HOME` unset); callers should prefer propagating the error.
#[must_use]
pub fn handoff_path() -> PathBuf {
    resolve_handoff_path().unwrap_or_else(|_| PathBuf::from("pending-adopt.json"))
}

fn resolve_handoff_path() -> Result<PathBuf, AdoptHandoffError> {
    #[cfg(target_os = "linux")]
    {
        let dirs = xdg::BaseDirectories::with_prefix("vaner");
        // `place_state_file` creates the state dir if needed and returns
        // the full path to the file name under it.
        dirs.place_state_file("pending-adopt.json")
            .map_err(|e| AdoptHandoffError::NoHandoffDir(e.to_string()))
    }
    #[cfg(target_os = "macos")]
    {
        let base = dirs::config_dir()
            .or_else(dirs::home_dir)
            .ok_or_else(|| AdoptHandoffError::NoHandoffDir("no home dir".into()))?;
        // On macOS, `config_dir` is `~/Library/Application Support/`.
        Ok(base.join("Vaner").join("pending-adopt.json"))
    }
    #[cfg(target_os = "windows")]
    {
        let base = dirs::data_local_dir()
            .ok_or_else(|| AdoptHandoffError::NoHandoffDir("no LocalAppData".into()))?;
        Ok(base.join("Vaner").join("pending-adopt.json"))
    }
    #[cfg(not(any(target_os = "linux", target_os = "macos", target_os = "windows")))]
    {
        let base = dirs::home_dir()
            .ok_or_else(|| AdoptHandoffError::NoHandoffDir("no home dir".into()))?;
        Ok(base.join(".vaner").join("pending-adopt.json"))
    }
}

/// Atomic-write the Resolution to the handoff path. Injects a top-level
/// `stashed_at` epoch-seconds key so agents can ignore stale drops
/// (CONTRACT.md recommends 10 min TTL on the consumer side).
///
/// `raw_payload` should be the exact bytes the server returned from
/// `POST /predictions/{id}/adopt` — this preserves unknown server keys.
/// If `raw_payload` is empty or not valid JSON, falls back to
/// re-serializing the decoded `resolution` (loses future fields).
pub fn stash_adopt(resolution: &Resolution, raw_payload: &[u8]) -> Result<(), AdoptHandoffError> {
    let target = resolve_handoff_path()?;
    stash_adopt_at(resolution, raw_payload, &target)
}

/// Testable variant of [`stash_adopt`] that writes to a caller-chosen
/// path. Production code should prefer [`stash_adopt`].
pub fn stash_adopt_at(
    resolution: &Resolution,
    raw_payload: &[u8],
    target: &Path,
) -> Result<(), AdoptHandoffError> {
    if let Some(parent) = target.parent() {
        std::fs::create_dir_all(parent)?;
    }

    // Prefer the raw bytes — they preserve every top-level server key.
    let mut obj: Map<String, Value> = match serde_json::from_slice::<Value>(raw_payload) {
        Ok(Value::Object(map)) => map,
        _ => {
            // Fallback: re-serialize the decoded struct.
            let reencoded = serde_json::to_value(resolution)?;
            match reencoded {
                Value::Object(map) => map,
                _ => {
                    return Err(AdoptHandoffError::Json(serde::de::Error::custom(
                        "resolution did not encode as an object",
                    )));
                }
            }
        }
    };

    let stashed_at = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0);
    obj.insert("stashed_at".to_string(), Value::from(stashed_at));

    let payload = serde_json::to_vec_pretty(&Value::Object(obj))?;

    // Atomic write: temp file in the same directory, then rename.
    let tmp = tempfile::NamedTempFile::new_in(target.parent().unwrap_or_else(|| Path::new(".")))?;
    tmp.as_file().write_all(&payload)?;
    tmp.as_file().sync_all()?;
    tmp.persist(target)
        .map_err(|e| AdoptHandoffError::Io(e.error))?;
    Ok(())
}

/// Remove the handoff file if present. Idempotent.
pub fn clear_handoff() -> Result<(), AdoptHandoffError> {
    let path = resolve_handoff_path()?;
    match std::fs::remove_file(&path) {
        Ok(()) => Ok(()),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(e) => Err(AdoptHandoffError::Io(e)),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::models::Provenance;

    fn fake_resolution() -> Resolution {
        Resolution {
            intent: "Write test".into(),
            confidence: 0.8,
            summary: "summary".into(),
            evidence: vec![],
            provenance: Provenance {
                mode: "predictive_hit".into(),
                cache: Some("warm".into()),
                freshness: Some("fresh".into()),
            },
            resolution_id: "adopt-p-1".into(),
            prepared_briefing: Some("## Context".into()),
            predicted_response: Some("draft".into()),
            briefing_token_used: 100,
            briefing_token_budget: 2048,
            adopted_from_prediction_id: Some("p-1".into()),
            alternatives_considered: vec![],
            gaps: vec![],
            next_actions: vec![],
        }
    }

    #[test]
    fn raw_bytes_preserve_unknown_keys() {
        let tmp = tempfile::tempdir().unwrap();
        let target = tmp.path().join("pending-adopt.json");

        // Simulate a server that emits a field the decoded struct
        // doesn't know about — AdoptHandoff must still write it through.
        let raw = br#"{
            "intent": "Write test",
            "confidence": 0.8,
            "summary": "summary",
            "evidence": [],
            "provenance": { "mode": "predictive_hit" },
            "resolution_id": "adopt-p-1",
            "adopted_from_prediction_id": "p-1",
            "future_field": { "something_new": 42 }
        }"#;

        stash_adopt_at(&fake_resolution(), raw, &target).unwrap();

        let written = std::fs::read_to_string(&target).unwrap();
        let json: Value = serde_json::from_str(&written).unwrap();
        assert_eq!(json["future_field"]["something_new"], 42);
        assert!(json["stashed_at"].as_f64().unwrap() > 0.0);
    }

    #[test]
    fn fallback_when_raw_is_empty() {
        let tmp = tempfile::tempdir().unwrap();
        let target = tmp.path().join("pending-adopt.json");
        stash_adopt_at(&fake_resolution(), b"", &target).unwrap();

        let written = std::fs::read_to_string(&target).unwrap();
        let json: Value = serde_json::from_str(&written).unwrap();
        assert_eq!(json["resolution_id"], "adopt-p-1");
        assert!(json["stashed_at"].as_f64().unwrap() > 0.0);
    }

    #[test]
    fn atomic_write_replaces_existing_file() {
        let tmp = tempfile::tempdir().unwrap();
        let target = tmp.path().join("pending-adopt.json");
        std::fs::write(&target, b"old").unwrap();

        stash_adopt_at(&fake_resolution(), b"{}", &target).unwrap();
        let written = std::fs::read_to_string(&target).unwrap();
        let json: Value = serde_json::from_str(&written).unwrap();
        assert!(json.as_object().unwrap().contains_key("stashed_at"));
    }
}
