//! HTTP client + `EngineClient` trait.
//!
//! Mirrors `vaner-desktop/vaner/Services/HTTPEngineClient.swift`
//! method-for-method so the Linux/Windows Tauri apps behave the same as
//! the macOS Swift app at the wire level. The trait exists so the Tauri
//! backend can swap a mock implementation in tests; the concrete
//! [`HttpEngineClient`] wraps [`reqwest::Client`].

use std::time::Duration;

use async_trait::async_trait;
use bytes::Bytes;
use reqwest::Url;
use serde::Deserialize;

use crate::errors::EngineClientError;
use crate::models::{EngineStatus, PredictedPrompt, Resolution};

/// Contract the UI layer depends on. Implement this to swap in a mock.
#[async_trait]
pub trait EngineClient: Send + Sync {
    /// `GET /status` — daemon health + scenario counts.
    async fn status(&self) -> Result<EngineStatus, EngineClientError>;

    /// `GET /predictions/active` — current snapshot of non-stale predictions.
    async fn active_predictions(&self) -> Result<Vec<PredictedPrompt>, EngineClientError>;

    /// `POST /predictions/{id}/adopt` — returns the decoded `Resolution`
    /// paired with the raw server bytes. The raw bytes flow through to
    /// `AdoptHandoff` so unknown top-level server keys (future additive
    /// fields) reach the agent verbatim.
    async fn adopt(
        &self,
        prediction_id: &str,
    ) -> Result<(Resolution, Bytes), EngineClientError>;
}

/// Concrete `reqwest`-backed engine client. Default base URL is
/// `http://127.0.0.1:8473` (the cockpit loopback).
pub struct HttpEngineClient {
    base_url: Url,
    client: reqwest::Client,
}

impl HttpEngineClient {
    /// Construct with a custom base URL (tests swap in a wiremock URL).
    pub fn new(base_url: Url) -> Self {
        let client = reqwest::Client::builder()
            .timeout(Duration::from_secs(10))
            .connect_timeout(Duration::from_secs(5))
            .build()
            .expect("reqwest::Client::builder should not fail on a default config");
        Self { base_url, client }
    }

    /// Convenience: loopback cockpit on the canonical port.
    #[must_use]
    pub fn localhost() -> Self {
        Self::new(
            "http://127.0.0.1:8473"
                .parse()
                .expect("hard-coded URL must parse"),
        )
    }

    /// Expose the `reqwest::Client` for adjacent streaming modules (SSE).
    pub(crate) fn inner_client(&self) -> &reqwest::Client {
        &self.client
    }

    /// Expose the base URL for adjacent streaming modules (SSE).
    pub(crate) fn base_url(&self) -> &Url {
        &self.base_url
    }

    fn url(&self, path: &str) -> Result<Url, EngineClientError> {
        self.base_url
            .join(path)
            .map_err(|e| EngineClientError::Transport(format!("url join failed: {e}")))
    }
}

#[async_trait]
impl EngineClient for HttpEngineClient {
    async fn status(&self) -> Result<EngineStatus, EngineClientError> {
        let url = self.url("/status")?;
        let response = self.client.get(url).send().await?.error_for_status()?;
        let status = response.json::<EngineStatus>().await?;
        Ok(status)
    }

    async fn active_predictions(&self) -> Result<Vec<PredictedPrompt>, EngineClientError> {
        #[derive(Deserialize)]
        struct Envelope {
            predictions: Vec<PredictedPrompt>,
        }
        let url = self.url("/predictions/active")?;
        let response = self.client.get(url).send().await?.error_for_status()?;
        let Envelope { predictions } = response.json().await?;
        Ok(predictions)
    }

    async fn adopt(
        &self,
        prediction_id: &str,
    ) -> Result<(Resolution, Bytes), EngineClientError> {
        let trimmed = prediction_id.trim();
        if trimmed.is_empty() {
            return Err(EngineClientError::InvalidInput);
        }
        let url = self.url(&format!("/predictions/{trimmed}/adopt"))?;
        let response = self
            .client
            .post(url)
            .header("Content-Type", "application/json")
            .body("{}")
            .send()
            .await?;

        let status = response.status();
        if !status.is_success() {
            return Err(match status.as_u16() {
                400 => EngineClientError::InvalidInput,
                404 => EngineClientError::NotFound,
                409 => EngineClientError::EngineUnavailable,
                other => EngineClientError::Http {
                    status: other,
                    message: format!("HTTP {other}"),
                },
            });
        }

        let raw = response.bytes().await?;
        let resolution: Resolution = serde_json::from_slice(&raw)?;
        Ok((resolution, raw))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use wiremock::matchers::{method, path};
    use wiremock::{Mock, MockServer, ResponseTemplate};

    #[tokio::test]
    async fn status_decodes() {
        let server = MockServer::start().await;
        Mock::given(method("GET"))
            .and(path("/status"))
            .respond_with(
                ResponseTemplate::new(200)
                    .set_body_json(serde_json::json!({
                        "health": "ok",
                        "scenario_counts": {"fresh": 1, "recent": 2, "stale": 0, "total": 3}
                    })),
            )
            .mount(&server)
            .await;

        let client = HttpEngineClient::new(server.uri().parse().unwrap());
        let status = client.status().await.unwrap();
        assert!(status.reachable());
        assert_eq!(status.total_scenarios(), 3);
    }

    #[tokio::test]
    async fn active_predictions_unwraps_envelope() {
        let server = MockServer::start().await;
        Mock::given(method("GET"))
            .and(path("/predictions/active"))
            .respond_with(
                ResponseTemplate::new(200).set_body_json(serde_json::json!({
                    "predictions": [{
                        "id": "p-1",
                        "spec": {
                            "label": "Write test", "description": "foo",
                            "source": "arc", "anchor": "testing",
                            "confidence": 0.8, "hypothesis_type": "likely_next",
                            "specificity": "concrete", "created_at": 1.0
                        },
                        "run": {
                            "weight": 0.5, "token_budget": 2048, "tokens_used": 1024,
                            "model_calls": 2, "scenarios_spawned": 1, "scenarios_complete": 1,
                            "readiness": "ready", "updated_at": 2.0
                        },
                        "artifacts": {
                            "scenario_ids": ["scn_1"], "evidence_score": 0.5,
                            "has_draft": true, "has_briefing": true,
                            "thinking_trace_count": 1
                        }
                    }]
                })),
            )
            .mount(&server)
            .await;

        let client = HttpEngineClient::new(server.uri().parse().unwrap());
        let preds = client.active_predictions().await.unwrap();
        assert_eq!(preds.len(), 1);
        assert_eq!(preds[0].id, "p-1");
    }

    #[tokio::test]
    async fn adopt_returns_resolution_and_raw_bytes() {
        let server = MockServer::start().await;
        let body = serde_json::json!({
            "intent": "Write the test",
            "confidence": 0.8,
            "summary": "summary",
            "evidence": [],
            "provenance": { "mode": "predictive_hit", "cache": "warm", "freshness": "fresh" },
            "resolution_id": "adopt-p-1",
            "prepared_briefing": "## Context\nfoo",
            "predicted_response": "draft",
            "briefing_token_used": 100,
            "briefing_token_budget": 2048,
            "adopted_from_prediction_id": "p-1",
            "unknown_future_field": {"nested": true}
        });
        Mock::given(method("POST"))
            .and(path("/predictions/p-1/adopt"))
            .respond_with(ResponseTemplate::new(200).set_body_json(body.clone()))
            .mount(&server)
            .await;

        let client = HttpEngineClient::new(server.uri().parse().unwrap());
        let (res, raw) = client.adopt("p-1").await.unwrap();
        assert_eq!(res.resolution_id, "adopt-p-1");
        assert_eq!(res.adopted_from_prediction_id.as_deref(), Some("p-1"));

        // Unknown future field survives on the raw payload — this is the
        // whole point of handing raw bytes to AdoptHandoff.
        let reparsed: serde_json::Value = serde_json::from_slice(&raw).unwrap();
        assert_eq!(reparsed["unknown_future_field"]["nested"], true);
    }

    #[tokio::test]
    async fn adopt_empty_id_short_circuits() {
        let client = HttpEngineClient::new("http://127.0.0.1:1".parse().unwrap());
        let err = client.adopt("   ").await.unwrap_err();
        assert!(matches!(err, EngineClientError::InvalidInput));
    }

    #[tokio::test]
    async fn adopt_maps_404_to_not_found() {
        let server = MockServer::start().await;
        Mock::given(method("POST"))
            .and(path("/predictions/gone/adopt"))
            .respond_with(ResponseTemplate::new(404).set_body_json(serde_json::json!({
                "code": "not_found", "message": "nope"
            })))
            .mount(&server)
            .await;

        let client = HttpEngineClient::new(server.uri().parse().unwrap());
        let err = client.adopt("gone").await.unwrap_err();
        assert!(matches!(err, EngineClientError::NotFound));
    }

    #[tokio::test]
    async fn adopt_maps_409_to_engine_unavailable() {
        let server = MockServer::start().await;
        Mock::given(method("POST"))
            .and(path("/predictions/any/adopt"))
            .respond_with(ResponseTemplate::new(409))
            .mount(&server)
            .await;

        let client = HttpEngineClient::new(server.uri().parse().unwrap());
        let err = client.adopt("any").await.unwrap_err();
        assert!(matches!(err, EngineClientError::EngineUnavailable));
    }
}
