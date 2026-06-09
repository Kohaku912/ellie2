use std::process::Command;

use anyhow::{anyhow, Context, Result};
use serde_json::{json, Value};

pub fn execute_shell(args: Value) -> Result<Value> {
    let command = args
        .get("command")
        .and_then(Value::as_str)
        .ok_or_else(|| anyhow!("command is required"))?;

    #[cfg(windows)]
    let output = Command::new("powershell")
        .args(["-NoProfile", "-Command", command])
        .output()
        .context("failed to execute PowerShell command")?;

    #[cfg(not(windows))]
    let output = Command::new("sh")
        .args(["-c", command])
        .output()
        .context("failed to execute shell command")?;

    Ok(json!({
        "stdout": String::from_utf8_lossy(&output.stdout).to_string(),
        "stderr": String::from_utf8_lossy(&output.stderr).to_string(),
        "exit_code": output.status.code().unwrap_or(-1),
    }))
}

pub fn kill_process(args: Value) -> Result<Value> {
    if let Some(pid) = args.get("pid").and_then(Value::as_u64) {
        run_command(&kill_by_pid_command(pid as u32)?)?;
        return Ok(json!({ "success": true, "pid": pid }));
    }

    if let Some(name) = args.get("name").and_then(Value::as_str) {
        run_command(&kill_by_name_command(name)?)?;
        return Ok(json!({ "success": true, "name": name }));
    }

    Err(anyhow!("pid or name is required"))
}

pub fn shutdown() -> Result<Value> {
    run_command(&shutdown_command())?;
    Ok(json!({ "success": true }))
}

pub fn reboot() -> Result<Value> {
    run_command(&reboot_command())?;
    Ok(json!({ "success": true }))
}

pub fn sleep() -> Result<Value> {
    run_command(&sleep_command())?;
    Ok(json!({ "success": true }))
}

pub fn lock_screen() -> Result<Value> {
    run_command(&lock_command())?;
    Ok(json!({ "success": true }))
}

pub fn logout() -> Result<Value> {
    run_command(&logout_command())?;
    Ok(json!({ "success": true }))
}

fn run_command(command: &[String]) -> Result<()> {
    let (program, args) = command
        .split_first()
        .ok_or_else(|| anyhow!("empty command"))?;
    let output = Command::new(program)
        .args(args)
        .output()
        .with_context(|| format!("failed to run {program}"))?;

    if !output.status.success() {
        return Err(anyhow!(
            "{} failed with exit code {:?}: {}",
            program,
            output.status.code(),
            String::from_utf8_lossy(&output.stderr)
        ));
    }
    Ok(())
}

fn shutdown_command() -> Vec<String> {
    platform_command(&["shutdown", "/s", "/t", "0"], &["systemctl", "poweroff"])
}

fn reboot_command() -> Vec<String> {
    platform_command(&["shutdown", "/r", "/t", "0"], &["systemctl", "reboot"])
}

fn sleep_command() -> Vec<String> {
    #[cfg(windows)]
    return vec![
        "rundll32.exe".to_string(),
        "powrprof.dll,SetSuspendState".to_string(),
        "0,1,0".to_string(),
    ];
    #[cfg(not(windows))]
    return vec!["systemctl".to_string(), "suspend".to_string()];
}

fn lock_command() -> Vec<String> {
    #[cfg(windows)]
    return vec![
        "rundll32.exe".to_string(),
        "user32.dll,LockWorkStation".to_string(),
    ];
    #[cfg(not(windows))]
    return vec!["loginctl".to_string(), "lock-session".to_string()];
}

fn logout_command() -> Vec<String> {
    #[cfg(windows)]
    return vec!["shutdown".to_string(), "/l".to_string()];
    #[cfg(not(windows))]
    return vec![
        "loginctl".to_string(),
        "terminate-session".to_string(),
        "self".to_string(),
    ];
}

fn kill_by_pid_command(pid: u32) -> Result<Vec<String>> {
    #[cfg(windows)]
    return Ok(vec![
        "taskkill".to_string(),
        "/F".to_string(),
        "/PID".to_string(),
        pid.to_string(),
    ]);
    #[cfg(not(windows))]
    return Ok(vec!["kill".to_string(), "-9".to_string(), pid.to_string()]);
}

fn kill_by_name_command(name: &str) -> Result<Vec<String>> {
    if name.trim().is_empty() {
        return Err(anyhow!("process name is empty"));
    }
    #[cfg(windows)]
    return Ok(vec![
        "taskkill".to_string(),
        "/F".to_string(),
        "/IM".to_string(),
        name.to_string(),
    ]);
    #[cfg(not(windows))]
    return Ok(vec![
        "pkill".to_string(),
        "-9".to_string(),
        name.to_string(),
    ]);
}

fn platform_command(windows: &[&str], _other: &[&str]) -> Vec<String> {
    #[cfg(windows)]
    let selected = windows;
    #[cfg(not(windows))]
    let selected = _other;
    selected.iter().map(|value| value.to_string()).collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn builds_destructive_commands() {
        assert!(!shutdown_command().is_empty());
        assert!(!reboot_command().is_empty());
        assert!(!sleep_command().is_empty());
        assert!(!lock_command().is_empty());
        assert!(!logout_command().is_empty());
    }
}
