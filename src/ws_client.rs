use std::time::Duration;

use anyhow::{Context, Result};
use futures_util::{SinkExt, StreamExt};
use serde_json::Value;
use tokio::{sync::mpsc, time};
use tokio_tungstenite::{connect_async, tungstenite::Message};
use tracing::{debug, info, warn};

use crate::{
    config::Config,
    monitor::monitor_active_window,
    protocol::{hello, parse_tool_call, ClientMessage, OutboundMessage},
    tools::{self, ToolRuntime},
    util::now_ms,
};

pub async fn run_forever(config: Config) -> Result<()> {
    let mut reconnect_delay = Duration::from_secs(1);

    loop {
        match run_connection(config.clone()).await {
            Ok(()) => {
                info!("websocket connection closed");
                reconnect_delay = Duration::from_secs(1);
            }
            Err(error) => warn!(%error, "websocket connection failed"),
        }

        info!(
            delay_ms = reconnect_delay.as_millis(),
            "reconnecting after delay"
        );
        time::sleep(reconnect_delay).await;
        reconnect_delay = (reconnect_delay * 2).min(Duration::from_secs(30));
    }
}

pub async fn run_connection(config: Config) -> Result<()> {
    let (socket, _) = connect_async(&config.server_url)
        .await
        .with_context(|| format!("failed to connect to {}", config.server_url))?;
    info!("websocket connected");

    let (mut write, mut read) = socket.split();
    let (out_tx, mut out_rx) = mpsc::channel::<OutboundMessage>(64);
    let tool_runtime = ToolRuntime::new(config.clone()).await;

    out_tx
        .send(hello(config.client_id.clone(), tools::tool_descriptors()).into())
        .await
        .context("failed to enqueue hello message")?;

    let writer = tokio::spawn(async move {
        while let Some(payload) = out_rx.recv().await {
            let message = match payload {
                OutboundMessage::Json(payload) => {
                    let text =
                        serde_json::to_string(&payload).context("failed to serialize message")?;
                    Message::Text(text)
                }
                OutboundMessage::Raw(message) => message,
            };

            write
                .send(message)
                .await
                .context("failed to send websocket message")?;
        }
        Result::<()>::Ok(())
    });

    let monitor = tokio::spawn(monitor_active_window(
        out_tx.clone(),
        config.client_id.clone(),
        config.poll_interval,
    ));

    while let Some(message) = read.next().await {
        match message.context("failed to read websocket message")? {
            Message::Text(text) => {
                handle_server_text(&text, &out_tx, &config.client_id, &tool_runtime).await
            }
            Message::Binary(bytes) => match String::from_utf8(bytes) {
                Ok(text) => {
                    handle_server_text(&text, &out_tx, &config.client_id, &tool_runtime).await
                }
                Err(error) => warn!(%error, "ignoring non-utf8 binary websocket message"),
            },
            Message::Ping(bytes) => {
                if let Err(error) = out_tx
                    .send(OutboundMessage::Raw(Message::Pong(bytes)))
                    .await
                {
                    warn!(%error, "failed to enqueue websocket pong");
                    break;
                }
            }
            Message::Pong(_) => {}
            Message::Close(frame) => {
                info!(?frame, "server closed websocket");
                break;
            }
            Message::Frame(_) => {}
        }
    }

    monitor.abort();
    let _ = monitor.await;
    drop(out_tx);

    match writer.await {
        Ok(Ok(())) => {}
        Ok(Err(error)) => return Err(error),
        Err(error) if error.is_cancelled() => {}
        Err(error) => return Err(error).context("writer task failed"),
    }

    Ok(())
}

async fn handle_server_text(
    text: &str,
    out_tx: &mpsc::Sender<OutboundMessage>,
    client_id: &str,
    tool_runtime: &ToolRuntime,
) {
    let value = match serde_json::from_str::<Value>(text) {
        Ok(value) => value,
        Err(error) => {
            warn!(%error, "ignoring invalid json from server");
            return;
        }
    };

    let Some(tool_call) = parse_tool_call(value) else {
        debug!("ignoring non-tool server message");
        return;
    };

    info!(call_id = %tool_call.call_id, tool = %tool_call.name, "received tool call");
    let out_tx = out_tx.clone();
    let client_id = client_id.to_string();
    let tool_runtime = tool_runtime.clone();
    tokio::spawn(async move {
        let call_id = tool_call.call_id.clone();
        let result = tool_runtime.execute_tool(tool_call).await;
        let message = match result {
            Ok(result) => ClientMessage::ToolResult {
                client_id,
                call_id,
                ok: true,
                result: Some(result),
                error: None,
                timestamp_ms: now_ms(),
            },
            Err(error) => ClientMessage::ToolResult {
                client_id,
                call_id,
                ok: false,
                result: None,
                error: Some(tool_runtime.redact_error(&error.to_string()).await),
                timestamp_ms: now_ms(),
            },
        };

        if let Err(error) = out_tx.send(message.into()).await {
            warn!(%error, "failed to enqueue tool result");
        }
    });
}
