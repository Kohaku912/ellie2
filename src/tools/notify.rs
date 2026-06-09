use std::process::Command;

use anyhow::{anyhow, Context, Result};
use serde_json::{json, Value};

pub fn notify(args: Value) -> Result<Value> {
    let title = args
        .get("title")
        .and_then(Value::as_str)
        .ok_or_else(|| anyhow!("title is required"))?;
    let body = args
        .get("body")
        .and_then(Value::as_str)
        .ok_or_else(|| anyhow!("body is required"))?;

    #[cfg(windows)]
    {
        let script = format!(
            r#"
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$n = New-Object System.Windows.Forms.NotifyIcon
$n.Icon = [System.Drawing.SystemIcons]::Information
$n.BalloonTipTitle = @'
{title}
'@
$n.BalloonTipText = @'
{body}
'@
$n.Visible = $true
$n.ShowBalloonTip(3000)
Start-Sleep -Milliseconds 3500
$n.Dispose()
"#
        );
        Command::new("powershell")
            .args(["-NoProfile", "-WindowStyle", "Hidden", "-Command", &script])
            .spawn()
            .context("failed to spawn notification helper")?;
    }

    #[cfg(not(windows))]
    {
        let _ = Command::new("notify-send").arg(title).arg(body).spawn();
    }

    Ok(json!({ "success": true }))
}
