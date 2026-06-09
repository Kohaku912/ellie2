use anyhow::{anyhow, Context, Result};
use serde_json::{json, Value};

use crate::platform;

pub fn launch_application(args: Value) -> Result<Value> {
    let app_name = args
        .get("app_name")
        .or_else(|| args.get("path"))
        .and_then(Value::as_str)
        .ok_or_else(|| anyhow!("app_name is required"))?;
    let target = normalize_app_name(app_name)?;
    platform::launch_application(&target)
        .with_context(|| format!("failed to launch application: {target}"))?;

    Ok(json!({
        "launched": true,
        "target": target,
    }))
}

fn normalize_app_name(app_name: &str) -> Result<String> {
    let trimmed = app_name.trim();
    if trimmed.is_empty() {
        return Err(anyhow!("app_name is empty"));
    }

    let normalized = match trimmed.to_ascii_lowercase().as_str() {
        "notepad" | "memo" => "notepad.exe",
        "calculator" | "calc" => "calc.exe",
        "browser" | "edge" | "microsoft edge" => "msedge.exe",
        "chrome" | "google chrome" => "chrome.exe",
        "firefox" | "mozilla firefox" => "firefox.exe",
        "explorer" | "file explorer" => "explorer.exe",
        "terminal" | "windows terminal" => "wt.exe",
        "powershell" => "powershell.exe",
        "cmd" | "command prompt" => "cmd.exe",
        _ => trimmed,
    };

    Ok(normalized.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn normalizes_known_application_names() {
        assert_eq!(normalize_app_name("notepad").unwrap(), "notepad.exe");
        assert_eq!(normalize_app_name(" calc ").unwrap(), "calc.exe");
        assert_eq!(
            normalize_app_name("custom-tool.exe").unwrap(),
            "custom-tool.exe"
        );
    }
}
