use anyhow::{anyhow, Context, Result};
use pc_ellie2::{config::Config, protocol::ToolCall, tools::ToolRuntime};
use serde_json::{json, Value};

#[tokio::main]
async fn main() -> Result<()> {
    let config = Config::from_env();
    if config.discord_client_id.is_none() {
        println!("discord smoke skipped: DISCORD_CLIENT_ID is not configured");
        return Ok(());
    }
    if !config
        .discord_source_dir
        .join("discord_tokens.json")
        .exists()
        && !config.discord_token_store_path.exists()
    {
        println!("discord smoke skipped: no Discord token file found");
        return Ok(());
    }

    let runtime = ToolRuntime::new(config).await;

    let status = call(&runtime, "discord_status", json!({})).await?;
    if !status
        .get("source_token_exists")
        .and_then(Value::as_bool)
        .unwrap_or(false)
        && !status
            .get("token_store")
            .and_then(Value::as_str)
            .map(std::path::Path::new)
            .map(|path| path.exists())
            .unwrap_or(false)
    {
        println!("discord smoke skipped: token migration source was not available");
        return Ok(());
    }

    match call(&runtime, "discord_connect", json!({})).await {
        Ok(_) => {}
        Err(error) => {
            let text = runtime.redact_error(&error.to_string()).await;
            if text.contains("could not connect to Discord IPC") {
                println!("discord smoke skipped: Discord IPC is not available");
                return Ok(());
            }
            return Err(anyhow!(text)).context("discord_connect failed");
        }
    }

    let _ = call(&runtime, "discord_refresh_tokens", json!({})).await;
    assert_object(
        call(&runtime, "discord_status", json!({})).await?,
        "discord_status",
    )?;
    assert_object(
        call(&runtime, "discord_get_guilds", json!({})).await?,
        "discord_get_guilds",
    )?;
    assert_object(
        call(
            &runtime,
            "discord_command",
            json!({ "cmd": "GET_GUILDS", "args": {} }),
        )
        .await?,
        "discord_command",
    )?;

    let _ = call(&runtime, "discord_disconnect", json!({})).await?;
    println!("discord smoke passed");
    Ok(())
}

async fn call(runtime: &ToolRuntime, name: &str, arguments: Value) -> Result<Value> {
    runtime
        .execute_tool(ToolCall {
            call_id: format!("discord-smoke-{name}"),
            name: name.to_string(),
            arguments,
        })
        .await
}

fn assert_object(value: Value, label: &str) -> Result<()> {
    if !value.is_object() {
        return Err(anyhow!("{label} did not return an object: {value}"));
    }
    Ok(())
}
