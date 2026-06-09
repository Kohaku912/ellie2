use std::{process::Command, time::Duration};

use anyhow::{anyhow, Context, Result};
use base64::{engine::general_purpose::STANDARD as BASE64, Engine as _};
use futures_util::{SinkExt, StreamExt};
use pc_ellie2::{config::Config, ws_client};
use serde_json::{json, Value};
use tokio::{net::TcpListener, sync::oneshot, time};
use tokio_tungstenite::{accept_async, tungstenite::Message};

#[tokio::main]
async fn main() -> Result<()> {
    let listener = TcpListener::bind("127.0.0.1:0")
        .await
        .context("failed to bind smoke websocket server")?;
    let addr = listener.local_addr()?;
    let server_url = format!("ws://{addr}/ws/pc");

    let (ready_tx, ready_rx) = oneshot::channel();
    let server = tokio::spawn(async move {
        ready_tx
            .send(())
            .map_err(|_| anyhow!("failed to notify smoke server readiness"))?;
        run_smoke_server(listener).await
    });

    ready_rx
        .await
        .context("smoke server failed before becoming ready")?;

    let mut config = Config::from_env();
    config.server_url = server_url;
    config.client_id = "smoke-client".to_string();
    config.poll_interval = Duration::from_millis(200);
    let client = tokio::spawn(ws_client::run_connection(config));

    let server_result = time::timeout(Duration::from_secs(20), server)
        .await
        .context("smoke server timed out")?
        .context("smoke server task panicked")?;
    server_result?;

    let client_result = time::timeout(Duration::from_secs(5), client)
        .await
        .context("smoke client did not exit after server close")?
        .context("smoke client task panicked")?;
    client_result?;

    println!("smoke harness passed");
    Ok(())
}

async fn run_smoke_server(listener: TcpListener) -> Result<()> {
    let (stream, _) = listener
        .accept()
        .await
        .context("failed to accept smoke client")?;
    let mut socket = accept_async(stream)
        .await
        .context("failed to accept websocket")?;

    let hello = read_json_message(&mut socket)
        .await
        .context("failed to read hello")?;
    assert_type(&hello, "hello")?;
    validate_hello_tools(&hello)?;

    send_tool_call(
        &mut socket,
        "smoke-launch",
        "launch_application",
        json!({ "app_name": "whoami.exe" }),
    )
    .await
    .context("failed to send launch tool call")?;
    read_expected_tool_result(&mut socket, "smoke-launch", validate_launch_result).await?;

    send_tool_call(
        &mut socket,
        "smoke-shell",
        "execute_shell",
        json!({ "command": "Write-Output pc_ellie2_smoke" }),
    )
    .await?;
    read_expected_tool_result(&mut socket, "smoke-shell", validate_shell_result).await?;

    send_tool_call(&mut socket, "smoke-system", "system_snapshot", json!({})).await?;
    read_expected_tool_result(&mut socket, "smoke-system", validate_object_result).await?;

    send_tool_call(&mut socket, "smoke-system-os", "system_os", json!({})).await?;
    read_expected_tool_result(&mut socket, "smoke-system-os", validate_object_result).await?;

    send_tool_call(&mut socket, "smoke-processes", "get_processes", json!({})).await?;
    read_expected_tool_result(&mut socket, "smoke-processes", validate_array_result).await?;

    send_tool_call(&mut socket, "smoke-startup", "processes_startup", json!({})).await?;
    read_expected_tool_result(&mut socket, "smoke-startup", validate_any_result).await?;

    send_tool_call(
        &mut socket,
        "smoke-hardware",
        "get_hardware_info",
        json!({}),
    )
    .await?;
    read_expected_tool_result(&mut socket, "smoke-hardware", validate_object_result).await?;

    send_tool_call(&mut socket, "smoke-cpu", "hardware_cpu", json!({})).await?;
    read_expected_tool_result(&mut socket, "smoke-cpu", validate_object_result).await?;

    send_tool_call(&mut socket, "smoke-window", "list_windows", json!({})).await?;
    read_expected_tool_result(&mut socket, "smoke-window", validate_array_result).await?;

    run_overlay_smoke(&mut socket).await?;
    run_file_smoke(&mut socket).await?;
    run_clipboard_smoke(&mut socket).await?;
    run_kill_process_smoke(&mut socket).await?;

    send_tool_call(
        &mut socket,
        "smoke-screenshot",
        "take_screenshot",
        json!({}),
    )
    .await
    .context("failed to send screenshot tool call")?;
    read_expected_tool_result(&mut socket, "smoke-screenshot", validate_screenshot_result).await?;

    socket
        .close(None)
        .await
        .context("failed to close smoke websocket")?;
    Ok(())
}

async fn run_overlay_smoke<S>(socket: &mut S) -> Result<()>
where
    S: SinkExt<Message, Error = tokio_tungstenite::tungstenite::Error>
        + StreamExt<Item = Result<Message, tokio_tungstenite::tungstenite::Error>>
        + Unpin,
{
    send_tool_call(
        socket,
        "smoke-overlay-show",
        "overlay_show",
        json!({
            "x": 20,
            "y": 20,
            "width": 420,
            "height": 180,
            "opacity": 230,
            "clear_after_ms": 1000,
            "items": [
                { "type": "text", "text": "pc_ellie2 overlay", "x": 24, "y": 20, "size": 28, "color": "#ffffff" },
                { "type": "rect", "x": 20, "y": 70, "width": 160, "height": 70, "color": "#00ff80", "stroke_width": 3 },
                { "type": "ellipse", "x": 210, "y": 70, "width": 120, "height": 70, "color": "#ff4080", "fill": false, "stroke_width": 4 },
                { "type": "line", "x1": 20, "y1": 155, "x2": 390, "y2": 155, "color": "#80c0ff", "stroke_width": 2 }
            ]
        }),
    )
    .await?;
    read_expected_tool_result(socket, "smoke-overlay-show", validate_overlay_show_result).await?;

    send_tool_call(socket, "smoke-overlay-status", "overlay_status", json!({})).await?;
    read_expected_tool_result(
        socket,
        "smoke-overlay-status",
        validate_overlay_status_result,
    )
    .await?;

    time::sleep(Duration::from_millis(1300)).await;
    send_tool_call(
        socket,
        "smoke-overlay-expired-status",
        "overlay_status",
        json!({}),
    )
    .await?;
    read_expected_tool_result(
        socket,
        "smoke-overlay-expired-status",
        validate_overlay_expired_status_result,
    )
    .await?;
    Ok(())
}

async fn run_file_smoke<S>(socket: &mut S) -> Result<()>
where
    S: SinkExt<Message, Error = tokio_tungstenite::tungstenite::Error>
        + StreamExt<Item = Result<Message, tokio_tungstenite::tungstenite::Error>>
        + Unpin,
{
    let temp_dir = tempfile::tempdir().context("failed to create temp dir")?;
    let base = temp_dir.path().to_string_lossy().to_string();
    let original = temp_dir.path().join("original.txt");
    let copied = temp_dir.path().join("copied.txt");
    let moved = temp_dir.path().join("moved.txt");

    send_tool_call(
        socket,
        "smoke-file-write",
        "write_file_base64",
        json!({
            "path": original.to_string_lossy(),
            "data": BASE64.encode("hello from smoke")
        }),
    )
    .await?;
    read_expected_tool_result(socket, "smoke-file-write", validate_success_result).await?;

    send_tool_call(
        socket,
        "smoke-file-read",
        "read_file_base64",
        json!({ "path": original.to_string_lossy() }),
    )
    .await?;
    read_expected_tool_result(socket, "smoke-file-read", validate_file_read_result).await?;

    send_tool_call(
        socket,
        "smoke-file-list",
        "list_directory",
        json!({ "path": base }),
    )
    .await?;
    read_expected_tool_result(socket, "smoke-file-list", validate_array_result).await?;

    send_tool_call(
        socket,
        "smoke-file-copy",
        "copy_file",
        json!({ "src": original.to_string_lossy(), "dst": copied.to_string_lossy() }),
    )
    .await?;
    read_expected_tool_result(socket, "smoke-file-copy", validate_success_result).await?;

    send_tool_call(
        socket,
        "smoke-file-move",
        "move_file",
        json!({ "src": copied.to_string_lossy(), "dst": moved.to_string_lossy() }),
    )
    .await?;
    read_expected_tool_result(socket, "smoke-file-move", validate_success_result).await?;

    send_tool_call(
        socket,
        "smoke-file-rename",
        "rename_file",
        json!({ "src": moved.to_string_lossy(), "new_name": "renamed.txt" }),
    )
    .await?;
    read_expected_tool_result(socket, "smoke-file-rename", validate_success_result).await?;

    send_tool_call(
        socket,
        "smoke-file-delete",
        "delete_path",
        json!({ "path": original.to_string_lossy() }),
    )
    .await?;
    read_expected_tool_result(socket, "smoke-file-delete", validate_success_result).await?;

    Ok(())
}

async fn run_clipboard_smoke<S>(socket: &mut S) -> Result<()>
where
    S: SinkExt<Message, Error = tokio_tungstenite::tungstenite::Error>
        + StreamExt<Item = Result<Message, tokio_tungstenite::tungstenite::Error>>
        + Unpin,
{
    send_tool_call(
        socket,
        "smoke-clipboard-set",
        "set_clipboard",
        json!({ "text": "pc_ellie2_smoke_clipboard" }),
    )
    .await?;
    read_expected_tool_result(socket, "smoke-clipboard-set", validate_success_result).await?;

    send_tool_call(socket, "smoke-clipboard-get", "get_clipboard", json!({})).await?;
    read_expected_tool_result(socket, "smoke-clipboard-get", validate_clipboard_result).await?;
    Ok(())
}

async fn run_kill_process_smoke<S>(socket: &mut S) -> Result<()>
where
    S: SinkExt<Message, Error = tokio_tungstenite::tungstenite::Error>
        + StreamExt<Item = Result<Message, tokio_tungstenite::tungstenite::Error>>
        + Unpin,
{
    let mut child = Command::new("powershell")
        .args(["-NoProfile", "-Command", "Start-Sleep -Seconds 60"])
        .spawn()
        .context("failed to spawn kill smoke child")?;
    let pid = child.id();
    send_tool_call(socket, "smoke-kill", "kill_process", json!({ "pid": pid })).await?;
    read_expected_tool_result(socket, "smoke-kill", validate_success_result).await?;
    let _ = child.kill();
    let _ = child.wait();
    Ok(())
}

async fn send_tool_call<S>(
    socket: &mut S,
    call_id: &str,
    tool: &str,
    arguments: Value,
) -> Result<()>
where
    S: SinkExt<Message, Error = tokio_tungstenite::tungstenite::Error> + Unpin,
{
    socket
        .send(Message::Text(
            json!({
                "type": "tool_call",
                "call_id": call_id,
                "tool": tool,
                "arguments": arguments
            })
            .to_string(),
        ))
        .await
        .context("failed to send tool call")
}

async fn read_expected_tool_result<S, F>(socket: &mut S, call_id: &str, validate: F) -> Result<()>
where
    S: StreamExt<Item = Result<Message, tokio_tungstenite::tungstenite::Error>> + Unpin,
    F: Fn(&Value) -> Result<()>,
{
    loop {
        let message = read_json_message(socket)
            .await
            .with_context(|| format!("failed to read tool result for {call_id}"))?;

        if message.get("type").and_then(Value::as_str) != Some("tool_result") {
            continue;
        }

        if message.get("call_id").and_then(Value::as_str) != Some(call_id) {
            continue;
        }

        validate(&message)?;
        return Ok(());
    }
}

async fn read_json_message<S>(socket: &mut S) -> Result<Value>
where
    S: StreamExt<Item = Result<Message, tokio_tungstenite::tungstenite::Error>> + Unpin,
{
    while let Some(message) = socket.next().await {
        match message.context("failed to read websocket message")? {
            Message::Text(text) => return serde_json::from_str(&text).context("invalid json text"),
            Message::Binary(bytes) => {
                let text = String::from_utf8(bytes).context("invalid utf8 binary message")?;
                return serde_json::from_str(&text).context("invalid json binary");
            }
            Message::Ping(_) | Message::Pong(_) => {}
            Message::Close(frame) => return Err(anyhow!("websocket closed early: {frame:?}")),
            Message::Frame(_) => {}
        }
    }

    Err(anyhow!("websocket ended before json message"))
}

fn assert_type(message: &Value, expected: &str) -> Result<()> {
    let actual = message.get("type").and_then(Value::as_str);
    if actual != Some(expected) {
        return Err(anyhow!("expected message type {expected}, got {actual:?}"));
    }
    Ok(())
}

fn validate_hello_tools(message: &Value) -> Result<()> {
    let tools = message
        .get("tools")
        .and_then(Value::as_array)
        .ok_or_else(|| anyhow!("hello missing tools array"))?;
    if tools.len() < 70 {
        return Err(anyhow!("hello advertised too few tools: {}", tools.len()));
    }
    for expected in [
        "take_screenshot",
        "system_os",
        "hardware_cpu",
        "processes_startup",
        "utils_notify",
        "execute_shell",
        "discord_command",
    ] {
        if !tools
            .iter()
            .any(|tool| tool.get("name").and_then(Value::as_str) == Some(expected))
        {
            return Err(anyhow!("hello tools missing {expected}"));
        }
    }
    Ok(())
}

fn validate_success_result(message: &Value) -> Result<()> {
    if message.get("ok").and_then(Value::as_bool) != Some(true) {
        return Err(anyhow!("tool result was not ok: {message}"));
    }
    let result = message
        .get("result")
        .ok_or_else(|| anyhow!("tool result missing result object"))?;
    if result.get("success").and_then(Value::as_bool) != Some(true) {
        return Err(anyhow!("success result payload was invalid: {result}"));
    }
    Ok(())
}

fn validate_object_result(message: &Value) -> Result<()> {
    if message.get("ok").and_then(Value::as_bool) != Some(true) {
        return Err(anyhow!("tool result was not ok: {message}"));
    }
    let result = message
        .get("result")
        .ok_or_else(|| anyhow!("tool result missing result object"))?;
    if !result.is_object() {
        return Err(anyhow!("expected object result: {result}"));
    }
    Ok(())
}

fn validate_array_result(message: &Value) -> Result<()> {
    if message.get("ok").and_then(Value::as_bool) != Some(true) {
        return Err(anyhow!("tool result was not ok: {message}"));
    }
    let result = message
        .get("result")
        .ok_or_else(|| anyhow!("tool result missing result object"))?;
    if !result.is_array() {
        return Err(anyhow!("expected array result: {result}"));
    }
    Ok(())
}

fn validate_any_result(message: &Value) -> Result<()> {
    if message.get("ok").and_then(Value::as_bool) != Some(true) {
        return Err(anyhow!("tool result was not ok: {message}"));
    }
    if message.get("result").is_none() {
        return Err(anyhow!("tool result missing result value"));
    }
    Ok(())
}

fn validate_shell_result(message: &Value) -> Result<()> {
    if message.get("ok").and_then(Value::as_bool) != Some(true) {
        return Err(anyhow!("shell tool result was not ok: {message}"));
    }
    let result = message
        .get("result")
        .ok_or_else(|| anyhow!("shell result missing result object"))?;
    let stdout = result
        .get("stdout")
        .and_then(Value::as_str)
        .unwrap_or_default();
    if !stdout.contains("pc_ellie2_smoke") {
        return Err(anyhow!("unexpected shell stdout: {stdout}"));
    }
    Ok(())
}

fn validate_file_read_result(message: &Value) -> Result<()> {
    if message.get("ok").and_then(Value::as_bool) != Some(true) {
        return Err(anyhow!("file read result was not ok: {message}"));
    }
    let result = message
        .get("result")
        .ok_or_else(|| anyhow!("file read result missing result object"))?;
    let data = result
        .get("data")
        .and_then(Value::as_str)
        .unwrap_or_default();
    let decoded = BASE64
        .decode(data)
        .context("file read data was not base64")?;
    if decoded != b"hello from smoke" {
        return Err(anyhow!("unexpected file read bytes"));
    }
    Ok(())
}

fn validate_clipboard_result(message: &Value) -> Result<()> {
    if message.get("ok").and_then(Value::as_bool) != Some(true) {
        return Err(anyhow!("clipboard result was not ok: {message}"));
    }
    let result = message
        .get("result")
        .ok_or_else(|| anyhow!("clipboard result missing result object"))?;
    let text = result
        .get("text")
        .and_then(Value::as_str)
        .unwrap_or_default();
    if text != "pc_ellie2_smoke_clipboard" {
        return Err(anyhow!("unexpected clipboard text"));
    }
    Ok(())
}

fn validate_overlay_show_result(message: &Value) -> Result<()> {
    validate_success_result(message)?;
    let result = message
        .get("result")
        .ok_or_else(|| anyhow!("overlay result missing result object"))?;
    if result.get("click_through").and_then(Value::as_bool) != Some(true) {
        return Err(anyhow!("overlay is not click-through"));
    }
    Ok(())
}

fn validate_overlay_status_result(message: &Value) -> Result<()> {
    if message.get("ok").and_then(Value::as_bool) != Some(true) {
        return Err(anyhow!("overlay status was not ok: {message}"));
    }
    let result = message
        .get("result")
        .ok_or_else(|| anyhow!("overlay status missing result object"))?;
    if result.get("click_through").and_then(Value::as_bool) != Some(true) {
        return Err(anyhow!("overlay status is not click-through"));
    }
    if result.get("visible").and_then(Value::as_bool) != Some(true) {
        return Err(anyhow!("overlay status is not visible"));
    }
    Ok(())
}

fn validate_overlay_expired_status_result(message: &Value) -> Result<()> {
    if message.get("ok").and_then(Value::as_bool) != Some(true) {
        return Err(anyhow!("overlay expired status was not ok: {message}"));
    }
    let result = message
        .get("result")
        .ok_or_else(|| anyhow!("overlay expired status missing result object"))?;
    if result.get("visible").and_then(Value::as_bool) != Some(false) {
        return Err(anyhow!("overlay did not auto-clear"));
    }
    Ok(())
}

fn validate_screenshot_result(message: &Value) -> Result<()> {
    if message.get("ok").and_then(Value::as_bool) != Some(true) {
        return Err(anyhow!("tool result was not ok: {message}"));
    }

    let result = message
        .get("result")
        .ok_or_else(|| anyhow!("tool result missing result object"))?;
    let mime_type = result.get("mime_type").and_then(Value::as_str);
    let data = result
        .get("data")
        .and_then(Value::as_str)
        .unwrap_or_default();
    let width = result
        .get("width")
        .and_then(Value::as_u64)
        .unwrap_or_default();
    let height = result
        .get("height")
        .and_then(Value::as_u64)
        .unwrap_or_default();

    if mime_type != Some("image/png") || data.len() < 128 || width == 0 || height == 0 {
        return Err(anyhow!("invalid screenshot result payload: {result}"));
    }

    Ok(())
}

fn validate_launch_result(message: &Value) -> Result<()> {
    if message.get("ok").and_then(Value::as_bool) != Some(true) {
        return Err(anyhow!("launch tool result was not ok: {message}"));
    }

    let result = message
        .get("result")
        .ok_or_else(|| anyhow!("launch result missing result object"))?;
    if result.get("launched").and_then(Value::as_bool) != Some(true) {
        return Err(anyhow!("invalid launch result payload: {result}"));
    }

    Ok(())
}
