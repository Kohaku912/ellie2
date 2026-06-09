use anyhow::{anyhow, Result};
use serde_json::{json, Value};

use crate::platform::{self, WindowShowCommand};

pub fn list_windows() -> Result<Value> {
    Ok(json!(platform::list_windows()?))
}

pub fn focus_window(args: Value) -> Result<Value> {
    let hwnd = resolve_hwnd(&args)?;
    platform::focus_window(hwnd)?;
    Ok(json!({ "success": true, "hwnd": hwnd }))
}

pub fn move_resize_window(args: Value) -> Result<Value> {
    let hwnd = resolve_hwnd(&args)?;
    let x = required_i32(&args, "x")?;
    let y = required_i32(&args, "y")?;
    let width = required_i32(&args, "width")?;
    let height = required_i32(&args, "height")?;
    platform::move_resize_window(hwnd, x, y, width, height)?;
    Ok(json!({
        "success": true,
        "hwnd": hwnd,
        "x": x,
        "y": y,
        "width": width,
        "height": height,
    }))
}

pub fn show_window(args: Value) -> Result<Value> {
    let hwnd = resolve_hwnd(&args)?;
    let command = args
        .get("command")
        .and_then(Value::as_str)
        .ok_or_else(|| anyhow!("command is required"))?;
    let command = match command {
        "hide" => WindowShowCommand::Hide,
        "show" => WindowShowCommand::Show,
        "minimize" => WindowShowCommand::Minimize,
        "maximize" => WindowShowCommand::Maximize,
        "restore" => WindowShowCommand::Restore,
        other => return Err(anyhow!("unknown show_window command: {other}")),
    };
    platform::show_window(hwnd, command)?;
    Ok(json!({ "success": true, "hwnd": hwnd }))
}

pub fn close_window(args: Value) -> Result<Value> {
    let hwnd = resolve_hwnd(&args)?;
    platform::close_window(hwnd)?;
    Ok(json!({ "success": true, "hwnd": hwnd }))
}

fn resolve_hwnd(args: &Value) -> Result<i64> {
    if let Some(hwnd) = args.get("hwnd").and_then(Value::as_i64) {
        return Ok(hwnd);
    }

    let title_contains = args
        .get("title_contains")
        .and_then(Value::as_str)
        .map(str::to_lowercase);
    let app_name = args
        .get("app_name")
        .and_then(Value::as_str)
        .map(str::to_lowercase);
    let process_id = args.get("process_id").and_then(Value::as_u64);

    let windows = platform::list_windows()?;
    windows
        .into_iter()
        .find(|window| {
            title_contains
                .as_ref()
                .map(|needle| window.title.to_lowercase().contains(needle))
                .unwrap_or(true)
                && app_name
                    .as_ref()
                    .map(|name| window.app_name.to_lowercase() == *name)
                    .unwrap_or(true)
                && process_id
                    .map(|pid| window.process_id as u64 == pid)
                    .unwrap_or(true)
        })
        .map(|window| window.hwnd)
        .ok_or_else(|| {
            anyhow!(
                "no matching window found; provide hwnd, title_contains, app_name, or process_id"
            )
        })
}

fn required_i32(args: &Value, key: &str) -> Result<i32> {
    args.get(key)
        .and_then(Value::as_i64)
        .and_then(|value| i32::try_from(value).ok())
        .ok_or_else(|| anyhow!("{key} is required as i32"))
}
