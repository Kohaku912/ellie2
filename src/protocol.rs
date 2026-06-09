use serde::Serialize;
use serde_json::Value;
use tokio_tungstenite::tungstenite::Message;

use crate::platform::ActiveWindowInfo;
use crate::util::now_ms;

#[derive(Debug, Serialize)]
pub struct ToolDescriptor {
    pub name: &'static str,
    pub description: &'static str,
    pub parameters: Value,
}

#[derive(Debug, Serialize)]
pub struct WindowState {
    pub active_window: Option<ActiveWindowInfo>,
}

#[derive(Debug, Serialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum ClientMessage {
    Hello {
        client_id: String,
        os: &'static str,
        version: &'static str,
        tools: Vec<ToolDescriptor>,
        timestamp_ms: u128,
    },
    StateDelta {
        client_id: String,
        seq: u64,
        timestamp_ms: u128,
        state: WindowState,
    },
    ToolResult {
        client_id: String,
        call_id: String,
        ok: bool,
        result: Option<Value>,
        error: Option<String>,
        timestamp_ms: u128,
    },
}

#[derive(Debug)]
pub enum OutboundMessage {
    Json(ClientMessage),
    Raw(Message),
}

impl From<ClientMessage> for OutboundMessage {
    fn from(message: ClientMessage) -> Self {
        Self::Json(message)
    }
}

#[derive(Clone, Debug)]
pub struct ToolCall {
    pub call_id: String,
    pub name: String,
    pub arguments: Value,
}

pub fn hello(client_id: String, tools: Vec<ToolDescriptor>) -> ClientMessage {
    ClientMessage::Hello {
        client_id,
        os: std::env::consts::OS,
        version: env!("CARGO_PKG_VERSION"),
        tools,
        timestamp_ms: now_ms(),
    }
}

pub fn parse_tool_call(value: Value) -> Option<ToolCall> {
    let message_type = value.get("type").and_then(Value::as_str);
    let looks_like_tool_call = message_type == Some("tool_call")
        || value.get("tool").is_some()
        || value.get("name").is_some()
        || value
            .get("function")
            .and_then(|function| function.get("name"))
            .is_some();

    if !looks_like_tool_call {
        return None;
    }

    let call_id = value
        .get("call_id")
        .or_else(|| value.get("tool_call_id"))
        .or_else(|| value.get("id"))
        .and_then(Value::as_str)
        .map(ToOwned::to_owned)
        .unwrap_or_else(|| format!("local-{}", now_ms()));

    let name = value
        .get("tool")
        .or_else(|| value.get("name"))
        .and_then(Value::as_str)
        .or_else(|| {
            value
                .get("function")
                .and_then(|function| function.get("name"))
                .and_then(Value::as_str)
        })?
        .to_string();

    let arguments = value
        .get("arguments")
        .or_else(|| value.get("args"))
        .cloned()
        .or_else(|| {
            value
                .get("function")
                .and_then(|function| function.get("arguments"))
                .cloned()
        })
        .unwrap_or(Value::Null);

    let arguments = match arguments {
        Value::String(text) => serde_json::from_str::<Value>(&text).unwrap_or(Value::Null),
        other => other,
    };

    Some(ToolCall {
        call_id,
        name,
        arguments,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn parses_simple_tool_call() {
        let call = parse_tool_call(json!({
            "type": "tool_call",
            "call_id": "shot-1",
            "tool": "take_screenshot",
            "arguments": {}
        }))
        .expect("tool call should parse");

        assert_eq!(call.call_id, "shot-1");
        assert_eq!(call.name, "take_screenshot");
        assert_eq!(call.arguments, json!({}));
    }

    #[test]
    fn parses_function_style_tool_call() {
        let call = parse_tool_call(json!({
            "id": "launch-1",
            "function": {
                "name": "launch_application",
                "arguments": "{\"app_name\":\"notepad\"}"
            }
        }))
        .expect("function-style tool call should parse");

        assert_eq!(call.call_id, "launch-1");
        assert_eq!(call.name, "launch_application");
        assert_eq!(call.arguments, json!({ "app_name": "notepad" }));
    }
}
