//! SSE stream consumer for `/events/stream?stages=predictions`.
//!
//! Per the SSE spec, a single event can span multiple `data:` lines
//! joined by `\n`, terminated by a blank line. The daemon usually emits
//! a single line per frame today, but nothing in the protocol prevents
//! a pretty-printed payload tomorrow — so this parser accumulates
//! `data:` lines into a buffer and decodes on the blank-line boundary.
//!
//! This mirrors the post-fix Swift SSE parser at
//! `vaner-desktop/vaner/Services/HTTPEngineClient.swift`.
//!
//! The daemon emits a JSON frame of the shape
//! `{"stage": "...", "payload": ...}`. We filter to `stage ==
//! "predictions"` and deliver the decoded payload through the provided
//! [`tokio::sync::mpsc::Sender`].

use std::time::Duration;

use bytes::Bytes;
use futures::StreamExt;
use reqwest::Url;
use serde::Deserialize;
use tokio::sync::mpsc;

use crate::errors::EngineClientError;
use crate::http::HttpEngineClient;
use crate::models::PredictedPrompt;

#[derive(Deserialize)]
struct Frame {
    stage: String,
    payload: Vec<PredictedPrompt>,
}

/// Spawn a task that subscribes to `/events/stream?stages=predictions`
/// and forwards every snapshot frame to `sink`. The task reconnects
/// with exponential backoff (3s → 60s cap) on transient errors and
/// exits cleanly when `sink` is dropped.
///
/// Returns the `JoinHandle` so the caller can cancel via
/// `handle.abort()`.
pub fn stream_prediction_events(
    client: &HttpEngineClient,
    sink: mpsc::Sender<Vec<PredictedPrompt>>,
) -> tokio::task::JoinHandle<()> {
    let http = client.inner_client().clone();
    let url = match client.base_url().join("/events/stream?stages=predictions") {
        Ok(u) => u,
        Err(e) => {
            // If we can't even build the URL, bail immediately; caller
            // will notice the task exited.
            eprintln!("vaner-contract: sse url join failed: {e}");
            return tokio::spawn(async {});
        }
    };

    tokio::spawn(async move {
        let mut backoff = Duration::from_secs(3);
        loop {
            if sink.is_closed() {
                return;
            }
            match run_once(&http, &url, &sink).await {
                Ok(()) => {
                    // Clean disconnect — reset backoff and reconnect shortly.
                    backoff = Duration::from_secs(3);
                }
                Err(EngineClientError::NotFound) => {
                    // Endpoint not available on this engine build — give up.
                    return;
                }
                Err(_) => {
                    backoff = (backoff * 2).min(Duration::from_secs(60));
                }
            }
            tokio::time::sleep(backoff).await;
        }
    })
}

/// One connect-and-read cycle. Returns Ok when the stream ends cleanly.
async fn run_once(
    http: &reqwest::Client,
    url: &Url,
    sink: &mpsc::Sender<Vec<PredictedPrompt>>,
) -> Result<(), EngineClientError> {
    let response = http
        .get(url.clone())
        .header("Accept", "text/event-stream")
        .send()
        .await?;

    if !response.status().is_success() {
        let status = response.status();
        if status.as_u16() == 404 {
            return Err(EngineClientError::NotFound);
        }
        return Err(EngineClientError::Http {
            status: status.as_u16(),
            message: format!("HTTP {status}"),
        });
    }

    let mut stream = response.bytes_stream();
    let mut line_buf = Vec::<u8>::new();
    let mut data_buf = String::new();

    while let Some(chunk) = stream.next().await {
        let chunk: Bytes = chunk.map_err(EngineClientError::from)?;
        for byte in chunk.iter().copied() {
            if byte == b'\n' {
                // Completed a line.
                let line = String::from_utf8_lossy(&line_buf).into_owned();
                line_buf.clear();

                if line.is_empty() {
                    // Blank line → dispatch accumulated event.
                    if !data_buf.is_empty() {
                        if let Ok(frame) = serde_json::from_str::<Frame>(&data_buf) {
                            if frame.stage == "predictions"
                                && sink.send(frame.payload).await.is_err()
                            {
                                // Receiver gone.
                                return Ok(());
                            }
                        }
                        data_buf.clear();
                    }
                } else if let Some(rest) = line.strip_prefix("data:") {
                    if !data_buf.is_empty() {
                        data_buf.push('\n');
                    }
                    data_buf.push_str(rest.trim_start());
                }
                // Other SSE line types (`event:`, `id:`, `retry:`, `:` comment) are ignored.
            } else {
                line_buf.push(byte);
            }
        }
    }

    // Stream ended; clear any residual buffer.
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn frame_decodes_single_line_payload() {
        let raw = r#"{"stage":"predictions","payload":[]}"#;
        let frame: Frame = serde_json::from_str(raw).unwrap();
        assert_eq!(frame.stage, "predictions");
        assert!(frame.payload.is_empty());
    }

    #[test]
    fn non_prediction_stage_is_filtered_in_caller() {
        // The filter happens inside `run_once`; here we just prove a
        // non-prediction stage decodes fine — the protocol-level skip is
        // the caller's job.
        let raw = r#"{"stage":"budget","payload":[]}"#;
        let decoded: Result<Frame, _> = serde_json::from_str(raw);
        // `payload` type is Vec<PredictedPrompt>, so an empty array decodes;
        // a populated non-prediction frame would fail here — but the real
        // daemon sends the correct payload shape per stage, and our stages
        // filter keeps us out of that mismatch.
        assert!(decoded.is_ok());
    }
}
