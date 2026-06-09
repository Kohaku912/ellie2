use anyhow::{anyhow, Context, Result};
use arboard::Clipboard;
use serde_json::{json, Value};

pub fn get_clipboard() -> Result<Value> {
    let mut clipboard = Clipboard::new().context("failed to open clipboard")?;
    let text = clipboard
        .get_text()
        .context("failed to read clipboard text")?;
    Ok(json!({ "text": text }))
}

pub fn set_clipboard(args: Value) -> Result<Value> {
    let text = args
        .get("text")
        .and_then(Value::as_str)
        .ok_or_else(|| anyhow!("text is required"))?;
    let mut clipboard = Clipboard::new().context("failed to open clipboard")?;
    clipboard
        .set_text(text.to_string())
        .context("failed to set clipboard text")?;
    Ok(json!({ "success": true }))
}
