use std::{fs, path::Path};

use anyhow::{anyhow, Context, Result};
use base64::{engine::general_purpose::STANDARD as BASE64, Engine as _};
use serde_json::{json, Value};

pub fn list_directory(args: Value) -> Result<Value> {
    let path = required_path(&args)?;
    if !path.exists() {
        return Err(anyhow!("path not found: {}", path.display()));
    }
    if !path.is_dir() {
        return Err(anyhow!("path is not a directory: {}", path.display()));
    }

    let entries = fs::read_dir(path)?
        .filter_map(|entry| entry.ok())
        .map(|entry| {
            let metadata = entry.metadata().ok();
            let modified = metadata
                .as_ref()
                .and_then(|metadata| metadata.modified().ok())
                .map(|time| {
                    let dt: chrono::DateTime<chrono::Local> = time.into();
                    dt.to_rfc3339()
                });
            json!({
                "name": entry.file_name().to_string_lossy(),
                "path": entry.path().to_string_lossy(),
                "is_dir": entry.file_type().map(|file_type| file_type.is_dir()).unwrap_or(false),
                "size_bytes": metadata.as_ref().and_then(|metadata| metadata.is_file().then_some(metadata.len())),
                "modified": modified,
            })
        })
        .collect::<Vec<_>>();

    Ok(json!(entries))
}

pub fn read_file_base64(args: Value) -> Result<Value> {
    let path = required_path(&args)?;
    let bytes = fs::read(path).with_context(|| format!("failed to read {}", path.display()))?;
    Ok(json!({
        "path": path.to_string_lossy(),
        "encoding": "base64",
        "data": BASE64.encode(&bytes),
        "bytes": bytes.len(),
    }))
}

pub fn write_file_base64(args: Value) -> Result<Value> {
    let path = required_path(&args)?;
    let data = args
        .get("data")
        .and_then(Value::as_str)
        .ok_or_else(|| anyhow!("data is required"))?;
    let bytes = BASE64
        .decode(data)
        .context("failed to decode base64 data")?;
    if let Some(parent) = path.parent() {
        if !parent.as_os_str().is_empty() {
            fs::create_dir_all(parent)?;
        }
    }
    fs::write(path, &bytes).with_context(|| format!("failed to write {}", path.display()))?;
    Ok(json!({ "success": true, "path": path.to_string_lossy(), "bytes": bytes.len() }))
}

pub fn copy_file(args: Value) -> Result<Value> {
    let src = required_named_path(&args, "src")?;
    let dst = required_named_path(&args, "dst")?;
    let bytes = fs::copy(src, dst)?;
    Ok(
        json!({ "success": true, "src": src.to_string_lossy(), "dst": dst.to_string_lossy(), "bytes": bytes }),
    )
}

pub fn move_file(args: Value) -> Result<Value> {
    let src = required_named_path(&args, "src")?;
    let dst = required_named_path(&args, "dst")?;
    fs::rename(src, dst)?;
    Ok(json!({ "success": true, "src": src.to_string_lossy(), "dst": dst.to_string_lossy() }))
}

pub fn rename_file(args: Value) -> Result<Value> {
    let src = required_named_path(&args, "src")?;
    let new_name = args
        .get("new_name")
        .and_then(Value::as_str)
        .ok_or_else(|| anyhow!("new_name is required"))?;
    let dst = src
        .parent()
        .unwrap_or_else(|| Path::new("."))
        .join(new_name);
    fs::rename(src, &dst)?;
    Ok(json!({ "success": true, "new_path": dst.to_string_lossy() }))
}

pub fn delete_path(args: Value) -> Result<Value> {
    let path = required_path(&args)?;
    if path.is_dir() {
        fs::remove_dir_all(path)?;
    } else {
        fs::remove_file(path)?;
    }
    Ok(json!({ "success": true, "path": path.to_string_lossy() }))
}

fn required_path(args: &Value) -> Result<&Path> {
    required_named_path(args, "path")
}

fn required_named_path<'a>(args: &'a Value, key: &str) -> Result<&'a Path> {
    let path = args
        .get(key)
        .and_then(Value::as_str)
        .ok_or_else(|| anyhow!("{key} is required"))?;
    if path.trim().is_empty() {
        return Err(anyhow!("{key} is empty"));
    }
    Ok(Path::new(path))
}
