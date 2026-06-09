mod clipboard;
mod control;
mod files;
mod input;
mod launcher;
mod notify;
mod pc_info;
mod screenshot;
mod window;

use std::sync::Arc;

use anyhow::{anyhow, Context, Result};
use serde_json::{json, Value};
use tokio::sync::Mutex;

use crate::{
    config::Config,
    discord::{DiscordRpc, TokenStore},
    overlay::{OverlayConfig, OverlayController},
    protocol::{ToolCall, ToolDescriptor},
};

#[derive(Clone)]
pub struct ToolRuntime {
    config: Config,
    discord: Arc<Mutex<DiscordRpc>>,
    token_store: TokenStore,
    overlay: OverlayController,
}

impl ToolRuntime {
    pub async fn new(config: Config) -> Self {
        let token_store = TokenStore::new(
            config.discord_token_store_path.clone(),
            config.discord_source_dir.join("discord_tokens.json"),
        );
        let _ = token_store.migrate_from_source_if_missing().await;

        Self {
            config,
            discord: Arc::new(Mutex::new(DiscordRpc::new())),
            token_store,
            overlay: OverlayController::default(),
        }
    }

    pub async fn execute_tool(&self, tool_call: ToolCall) -> Result<Value> {
        let name = tool_call.name;
        let args = object_or_empty(tool_call.arguments);

        match name.as_str() {
            "utils_screenshot" | "screenshot" => {
                spawn_blocking_json(move || screenshot::take_screenshot()).await
            }
            "take_screenshot" => spawn_blocking_json(move || screenshot::take_screenshot()).await,
            "launch_application" | "control_launch" | "launch_app" => {
                spawn_blocking_json(move || launcher::launch_application(args)).await
            }

            "system" | "get_system_info" => spawn_blocking_json(pc_info::get_system).await,
            "system_os" | "get_os_info" => spawn_blocking_json(pc_info::get_os_info).await,
            "system_uptime" | "get_uptime" => spawn_blocking_json(pc_info::get_uptime).await,
            "system_users" | "get_users" => spawn_blocking_json(pc_info::get_users).await,
            "system_battery" | "get_battery" => spawn_blocking_json(pc_info::get_battery).await,
            "system_snapshot" => spawn_blocking_json(pc_info::system_snapshot).await,
            "processes" | "get_processes" => spawn_blocking_json(pc_info::get_processes).await,
            "processes_startup" | "get_startup_programs" => {
                spawn_blocking_json(pc_info::get_startup_programs).await
            }
            "processes_active_window" => spawn_blocking_json(pc_info::get_active_window).await,
            "get_hardware_info" => spawn_blocking_json(pc_info::get_hardware_info).await,
            "hardware_cpu" | "get_cpu" => spawn_blocking_json(pc_info::get_cpu).await,
            "hardware_memory" | "get_memory" => spawn_blocking_json(pc_info::get_memory).await,
            "hardware_disks" | "get_disks" => spawn_blocking_json(pc_info::get_disks).await,
            "hardware_network" | "get_network" => spawn_blocking_json(pc_info::get_network).await,
            "get_active_window" => spawn_blocking_json(pc_info::get_active_window).await,
            "list_windows" => spawn_blocking_json(window::list_windows).await,
            "get_clipboard" | "utils_get_clipboard" => {
                spawn_blocking_json(clipboard::get_clipboard).await
            }
            "overlay_show" | "overlay_update" => {
                let overlay = self.overlay.clone();
                spawn_blocking_json(move || {
                    let config: OverlayConfig = serde_json::from_value(args)?;
                    overlay.show(config)
                })
                .await
            }
            "overlay_hide" => {
                let overlay = self.overlay.clone();
                spawn_blocking_json(move || overlay.hide()).await
            }
            "overlay_clear" => {
                let overlay = self.overlay.clone();
                spawn_blocking_json(move || overlay.clear()).await
            }
            "overlay_status" => {
                let overlay = self.overlay.clone();
                spawn_blocking_json(move || Ok(json!(overlay.status()))).await
            }

            "focus_window" => spawn_blocking_json(move || window::focus_window(args)).await,
            "move_resize_window" => {
                spawn_blocking_json(move || window::move_resize_window(args)).await
            }
            "show_window" => spawn_blocking_json(move || window::show_window(args)).await,
            "close_window" => spawn_blocking_json(move || window::close_window(args)).await,

            "execute_shell" | "control_execute" => {
                spawn_blocking_json(move || control::execute_shell(args)).await
            }
            "kill_process" | "delete_process" => {
                spawn_blocking_json(move || control::kill_process(args)).await
            }
            "shutdown" | "control_shutdown" => spawn_blocking_json(control::shutdown).await,
            "reboot" | "control_reboot" => spawn_blocking_json(control::reboot).await,
            "sleep" | "control_sleep" => spawn_blocking_json(control::sleep).await,
            "lock_screen" | "control_lock" => spawn_blocking_json(control::lock_screen).await,
            "logout" | "control_logout" => spawn_blocking_json(control::logout).await,

            "set_clipboard" | "utils_set_clipboard" => {
                spawn_blocking_json(move || clipboard::set_clipboard(args)).await
            }
            "notify" | "utils_notify" => spawn_blocking_json(move || notify::notify(args)).await,
            "mouse_move" | "input_mouse_move" => {
                spawn_blocking_json(move || input::mouse_move(args)).await
            }
            "mouse_click" | "input_mouse_click" => {
                spawn_blocking_json(move || input::mouse_click(args)).await
            }
            "mouse_scroll" | "input_mouse_scroll" => {
                spawn_blocking_json(move || input::mouse_scroll(args)).await
            }
            "keyboard_type" | "input_keyboard_type" => {
                spawn_blocking_json(move || input::keyboard_type(args)).await
            }
            "keyboard_shortcut" | "input_keyboard_shortcut" => {
                spawn_blocking_json(move || input::keyboard_shortcut(args)).await
            }
            "media_key" | "input_media" => {
                spawn_blocking_json(move || input::media_key(args)).await
            }

            "list_directory" | "files_list" => {
                spawn_blocking_json(move || files::list_directory(args)).await
            }
            "read_file_base64" | "files_download" => {
                spawn_blocking_json(move || files::read_file_base64(args)).await
            }
            "write_file_base64" | "files_upload" => {
                spawn_blocking_json(move || files::write_file_base64(args)).await
            }
            "copy_file" | "files_copy" => spawn_blocking_json(move || files::copy_file(args)).await,
            "move_file" | "files_move" => spawn_blocking_json(move || files::move_file(args)).await,
            "rename_file" | "files_rename" => {
                spawn_blocking_json(move || files::rename_file(args)).await
            }
            "delete_path" | "files_delete" => {
                spawn_blocking_json(move || files::delete_path(args)).await
            }

            "discord_status" => self.discord_status().await,
            "discord_connect" => self.discord_connect().await,
            "discord_disconnect" => self.discord_disconnect().await,
            "discord_refresh_tokens" => self.discord_refresh_tokens().await,
            "discord_get_guilds" => self.discord_command("GET_GUILDS", json!({}), None).await,
            "discord_get_guild" => {
                let guild_id = required_string(&args, "guild_id")?;
                let timeout = args.get("timeout").cloned();
                let mut rpc_args = json!({ "guild_id": guild_id });
                if let Some(timeout) = timeout {
                    rpc_args["timeout"] = timeout;
                }
                self.discord_command("GET_GUILD", rpc_args, None).await
            }
            "discord_get_channels" => {
                let guild_id = required_string(&args, "guild_id")?;
                self.discord_command("GET_CHANNELS", json!({ "guild_id": guild_id }), None)
                    .await
            }
            "discord_get_channel" => {
                let channel_id = required_string(&args, "channel_id")?;
                self.discord_command("GET_CHANNEL", json!({ "channel_id": channel_id }), None)
                    .await
            }
            "discord_get_voice_settings" => {
                self.discord_command("GET_VOICE_SETTINGS", json!({}), None)
                    .await
            }
            "discord_set_voice_settings" => {
                self.discord_command("SET_VOICE_SETTINGS", args, None).await
            }
            "discord_get_voice_channel" => {
                self.discord_command("GET_SELECTED_VOICE_CHANNEL", json!({}), None)
                    .await
            }
            "discord_select_voice_channel" => {
                self.discord_command("SELECT_VOICE_CHANNEL", args, None)
                    .await
            }
            "discord_select_text_channel" => {
                self.discord_command("SELECT_TEXT_CHANNEL", args, None)
                    .await
            }
            "discord_set_user_voice_settings" => {
                self.discord_command("SET_USER_VOICE_SETTINGS", args, None)
                    .await
            }
            "discord_set_activity" => {
                let pid = args
                    .get("pid")
                    .and_then(Value::as_i64)
                    .unwrap_or_else(|| std::process::id() as i64);
                let activity = args.get("activity").cloned().unwrap_or(Value::Null);
                self.discord_command(
                    "SET_ACTIVITY",
                    json!({ "pid": pid, "activity": activity }),
                    None,
                )
                .await
            }
            "discord_send_activity_join_invite" => {
                let user_id = required_string(&args, "user_id")?;
                self.discord_command(
                    "SEND_ACTIVITY_JOIN_INVITE",
                    json!({ "user_id": user_id }),
                    None,
                )
                .await
            }
            "discord_close_activity_request" => {
                let user_id = required_string(&args, "user_id")?;
                self.discord_command(
                    "CLOSE_ACTIVITY_REQUEST",
                    json!({ "user_id": user_id }),
                    None,
                )
                .await
            }
            "discord_subscribe" => {
                let evt = required_string(&args, "evt")?;
                let rpc_args = args.get("args").cloned().unwrap_or_else(|| json!({}));
                self.discord_command("SUBSCRIBE", rpc_args, Some(evt)).await
            }
            "discord_unsubscribe" => {
                let evt = required_string(&args, "evt")?;
                let rpc_args = args.get("args").cloned().unwrap_or_else(|| json!({}));
                self.discord_command("UNSUBSCRIBE", rpc_args, Some(evt))
                    .await
            }
            "discord_command" => {
                let cmd = required_string(&args, "cmd")?;
                let rpc_args = args.get("args").cloned().unwrap_or_else(|| json!({}));
                let evt = args
                    .get("evt")
                    .and_then(Value::as_str)
                    .map(ToOwned::to_owned);
                self.discord_command(cmd, rpc_args, evt).await
            }

            other => Err(anyhow!("unknown tool: {other}")),
        }
    }

    pub async fn redact_error(&self, text: &str) -> String {
        let mut redacted = text.to_string();
        for secret in self.secret_values().await {
            if secret.len() >= 6 {
                redacted = redacted.replace(&secret, "[REDACTED]");
            }
        }
        redacted
    }

    async fn discord_status(&self) -> Result<Value> {
        let (connected, client_id, authenticated, token_store_path, source_token_exists) = {
            let discord = self.discord.lock().await;
            (
                discord.connected,
                discord.client_id.clone(),
                discord.access_token.is_some(),
                self.token_store.path().display().to_string(),
                self.token_store.source_path().exists(),
            )
        };

        Ok(json!({
            "connected": connected,
            "client_id": client_id,
            "authenticated": authenticated,
            "token_store": token_store_path,
            "source_token_exists": source_token_exists,
        }))
    }

    async fn discord_connect(&self) -> Result<Value> {
        let client_id = self
            .config
            .discord_client_id
            .clone()
            .ok_or_else(|| anyhow!("DISCORD_CLIENT_ID is not configured"))?;
        let token = self
            .token_store
            .load()
            .await?
            .map(|tokens| tokens.access_token);
        let discord = self.discord.clone();

        let ready = tokio::task::spawn_blocking(move || {
            let mut discord = discord.blocking_lock();
            let ready = discord.connect(&client_id)?;
            if let Some(token) = token {
                discord.authenticate(&token)?;
            }
            Ok::<_, anyhow::Error>(ready)
        })
        .await
        .context("discord connect worker failed")??;

        Ok(json!({ "connected": true, "ready": ready }))
    }

    async fn discord_disconnect(&self) -> Result<Value> {
        let discord = self.discord.clone();
        tokio::task::spawn_blocking(move || {
            discord.blocking_lock().disconnect();
        })
        .await
        .context("discord disconnect worker failed")?;
        Ok(json!({ "connected": false }))
    }

    async fn discord_refresh_tokens(&self) -> Result<Value> {
        let client_id = self
            .config
            .discord_client_id
            .clone()
            .ok_or_else(|| anyhow!("DISCORD_CLIENT_ID is not configured"))?;
        let client_secret = self.config.discord_client_secret.clone();
        let refresh_token = self
            .token_store
            .load()
            .await?
            .ok_or_else(|| anyhow!("discord token store is empty"))?
            .refresh_token;

        let new_tokens = DiscordRpc::refresh_discord_tokens(
            &client_id,
            client_secret.as_deref(),
            &refresh_token,
        )
        .await?;
        self.token_store.save(&new_tokens).await?;

        let discord = self.discord.clone();
        let access_token = new_tokens.access_token.clone();
        let _ = tokio::task::spawn_blocking(move || {
            let mut discord = discord.blocking_lock();
            if discord.connected {
                let _ = discord.authenticate(&access_token);
            }
        })
        .await;

        Ok(json!({ "refreshed": true }))
    }

    async fn discord_command(
        &self,
        cmd: impl Into<String>,
        args: Value,
        evt: Option<String>,
    ) -> Result<Value> {
        self.ensure_discord_connected().await?;
        let cmd = cmd.into();
        let discord = self.discord.clone();

        tokio::task::spawn_blocking(move || {
            let mut discord = discord.blocking_lock();
            discord.send_command(&cmd, args, evt.as_deref())
        })
        .await
        .context("discord command worker failed")?
    }

    async fn ensure_discord_connected(&self) -> Result<()> {
        if self.discord.lock().await.connected {
            return Ok(());
        }
        self.discord_connect().await?;
        Ok(())
    }

    async fn secret_values(&self) -> Vec<String> {
        let mut values = Vec::new();
        if let Some(value) = self.config.discord_client_id.clone() {
            values.push(value);
        }
        if let Some(value) = self.config.discord_client_secret.clone() {
            values.push(value);
        }
        if let Ok(Some(tokens)) = self.token_store.load().await {
            values.push(tokens.access_token);
            values.push(tokens.refresh_token);
        }
        values
    }
}

async fn spawn_blocking_json<F>(f: F) -> Result<Value>
where
    F: FnOnce() -> Result<Value> + Send + 'static,
{
    tokio::task::spawn_blocking(f)
        .await
        .context("tool worker failed")?
}

fn object_or_empty(value: Value) -> Value {
    match value {
        Value::Null => json!({}),
        other => other,
    }
}

fn required_string(value: &Value, key: &str) -> Result<String> {
    value
        .get(key)
        .and_then(Value::as_str)
        .map(ToOwned::to_owned)
        .ok_or_else(|| anyhow!("{key} is required"))
}

pub fn tool_descriptors() -> Vec<ToolDescriptor> {
    let object = json!({ "type": "object", "additionalProperties": true });
    [
        ("system", "pc_ellie compatible: GET /system."),
        ("system_os", "pc_ellie compatible: GET /system/os."),
        ("system_uptime", "pc_ellie compatible: GET /system/uptime."),
        ("system_users", "pc_ellie compatible: GET /system/users."),
        (
            "system_battery",
            "pc_ellie compatible: GET /system/battery.",
        ),
        (
            "take_screenshot",
            "Capture the primary display as base64 PNG.",
        ),
        ("utils_screenshot", "pc_ellie compatible screenshot tool."),
        ("screenshot", "Alias for take_screenshot."),
        (
            "launch_application",
            "Launch an application by alias, executable name, or path.",
        ),
        (
            "control_launch",
            "pc_ellie compatible: POST /control/launch.",
        ),
        ("launch_app", "Alias for launch_application."),
        (
            "system_snapshot",
            "Return OS, uptime, users, battery, hardware, processes, and active window summary.",
        ),
        ("get_system_info", "Return OS, uptime, users, and battery."),
        ("get_os_info", "Return OS information."),
        ("get_uptime", "Return system uptime."),
        ("get_users", "Return logged-in users."),
        ("get_battery", "Return battery information."),
        ("processes", "pc_ellie compatible: GET /processes."),
        ("get_processes", "Return running process list."),
        (
            "processes_startup",
            "pc_ellie compatible: GET /processes/startup.",
        ),
        (
            "get_startup_programs",
            "Return Windows startup program registry entries.",
        ),
        (
            "processes_active_window",
            "pc_ellie compatible: GET /processes/active-window.",
        ),
        (
            "get_hardware_info",
            "Return CPU, memory, disk, and network information.",
        ),
        ("hardware_cpu", "pc_ellie compatible: GET /hardware/cpu."),
        (
            "hardware_memory",
            "pc_ellie compatible: GET /hardware/memory.",
        ),
        (
            "hardware_disks",
            "pc_ellie compatible: GET /hardware/disks.",
        ),
        (
            "hardware_network",
            "pc_ellie compatible: GET /hardware/network.",
        ),
        ("get_cpu", "Return CPU information."),
        ("get_memory", "Return memory information."),
        ("get_disks", "Return disk information."),
        ("get_network", "Return network interface information."),
        (
            "get_active_window",
            "Return current foreground window information.",
        ),
        ("list_windows", "Return top-level Windows desktop windows."),
        ("get_clipboard", "Return clipboard text."),
        ("utils_get_clipboard", "pc_ellie compatible clipboard read."),
        (
            "overlay_show",
            "Show a click-through transparent overlay with text, images, and shapes.",
        ),
        (
            "overlay_update",
            "Replace the click-through transparent overlay contents.",
        ),
        ("overlay_hide", "Hide the overlay window."),
        ("overlay_clear", "Show an empty transparent overlay window."),
        (
            "overlay_status",
            "Return overlay visibility and click-through state.",
        ),
        ("focus_window", "Bring a window to foreground by hwnd."),
        ("move_resize_window", "Move and resize a window by hwnd."),
        (
            "show_window",
            "Hide, show, minimize, maximize, or restore a window.",
        ),
        ("close_window", "Send WM_CLOSE to a window by hwnd."),
        (
            "execute_shell",
            "Run a PowerShell command and return stdout/stderr/exit code.",
        ),
        (
            "control_execute",
            "pc_ellie compatible: POST /control/execute.",
        ),
        ("kill_process", "Kill a process by pid or image name."),
        (
            "delete_process",
            "pc_ellie compatible process delete alias.",
        ),
        ("shutdown", "Shut down the PC immediately."),
        (
            "control_shutdown",
            "pc_ellie compatible: POST /control/shutdown.",
        ),
        ("reboot", "Reboot the PC immediately."),
        (
            "control_reboot",
            "pc_ellie compatible: POST /control/reboot.",
        ),
        ("sleep", "Put the PC to sleep."),
        ("control_sleep", "pc_ellie compatible: POST /control/sleep."),
        ("lock_screen", "Lock the current Windows session."),
        ("control_lock", "pc_ellie compatible: POST /control/lock."),
        ("logout", "Log out the current Windows session."),
        (
            "control_logout",
            "pc_ellie compatible: POST /control/logout.",
        ),
        ("set_clipboard", "Set clipboard text."),
        (
            "utils_set_clipboard",
            "pc_ellie compatible clipboard write.",
        ),
        ("notify", "Show a desktop notification."),
        ("utils_notify", "pc_ellie compatible: POST /utils/notify."),
        ("mouse_move", "Move the mouse cursor."),
        (
            "input_mouse_move",
            "pc_ellie compatible: POST /input/mouse/move.",
        ),
        ("mouse_click", "Click a mouse button."),
        (
            "input_mouse_click",
            "pc_ellie compatible: POST /input/mouse/click.",
        ),
        ("mouse_scroll", "Scroll the mouse wheel."),
        (
            "input_mouse_scroll",
            "pc_ellie compatible: POST /input/mouse/scroll.",
        ),
        ("keyboard_type", "Type text via keyboard input simulation."),
        (
            "input_keyboard_type",
            "pc_ellie compatible: POST /input/keyboard/type.",
        ),
        ("keyboard_shortcut", "Press and release a key chord."),
        (
            "input_keyboard_shortcut",
            "pc_ellie compatible: POST /input/keyboard/shortcut.",
        ),
        ("media_key", "Send a media key action."),
        ("input_media", "pc_ellie compatible: POST /input/media."),
        ("list_directory", "List directory entries."),
        ("files_list", "pc_ellie compatible: GET /files/list."),
        ("read_file_base64", "Read a file and return base64 bytes."),
        (
            "files_download",
            "pc_ellie compatible file download as base64.",
        ),
        ("write_file_base64", "Write base64 bytes to a file."),
        ("files_upload", "pc_ellie compatible file upload as base64."),
        ("copy_file", "Copy a file."),
        ("files_copy", "pc_ellie compatible: POST /files/copy."),
        ("move_file", "Move a file or directory."),
        ("files_move", "pc_ellie compatible: POST /files/move."),
        (
            "rename_file",
            "Rename a file or directory within its parent.",
        ),
        ("files_rename", "pc_ellie compatible: POST /files/rename."),
        ("delete_path", "Delete a file or directory recursively."),
        ("files_delete", "pc_ellie compatible: DELETE /files."),
        (
            "discord_status",
            "Return Discord IPC/token state without secrets.",
        ),
        (
            "discord_connect",
            "Connect and authenticate to Discord IPC.",
        ),
        ("discord_disconnect", "Disconnect from Discord IPC."),
        (
            "discord_refresh_tokens",
            "Refresh Discord OAuth tokens and store them locally.",
        ),
        ("discord_get_guilds", "Run Discord GET_GUILDS."),
        ("discord_get_guild", "Run Discord GET_GUILD."),
        ("discord_get_channels", "Run Discord GET_CHANNELS."),
        ("discord_get_channel", "Run Discord GET_CHANNEL."),
        (
            "discord_get_voice_settings",
            "Run Discord GET_VOICE_SETTINGS.",
        ),
        (
            "discord_set_voice_settings",
            "Run Discord SET_VOICE_SETTINGS.",
        ),
        (
            "discord_get_voice_channel",
            "Run Discord GET_SELECTED_VOICE_CHANNEL.",
        ),
        (
            "discord_select_voice_channel",
            "Run Discord SELECT_VOICE_CHANNEL.",
        ),
        (
            "discord_select_text_channel",
            "Run Discord SELECT_TEXT_CHANNEL.",
        ),
        (
            "discord_set_user_voice_settings",
            "Run Discord SET_USER_VOICE_SETTINGS.",
        ),
        ("discord_set_activity", "Run Discord SET_ACTIVITY."),
        (
            "discord_send_activity_join_invite",
            "Run Discord SEND_ACTIVITY_JOIN_INVITE.",
        ),
        (
            "discord_close_activity_request",
            "Run Discord CLOSE_ACTIVITY_REQUEST.",
        ),
        ("discord_subscribe", "Run Discord SUBSCRIBE."),
        ("discord_unsubscribe", "Run Discord UNSUBSCRIBE."),
        ("discord_command", "Send an arbitrary Discord RPC command."),
    ]
    .into_iter()
    .map(|(name, description)| ToolDescriptor {
        name,
        description,
        parameters: object.clone(),
    })
    .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn registry_has_pc_ellie_compatible_surface() {
        let tools = tool_descriptors();
        assert!(
            tools.len() >= 70,
            "expected at least 70 tools, got {}",
            tools.len()
        );

        for name in [
            "system",
            "system_os",
            "hardware_cpu",
            "processes_startup",
            "control_execute",
            "input_keyboard_shortcut",
            "files_download",
            "utils_notify",
            "overlay_show",
            "discord_command",
        ] {
            assert!(tools.iter().any(|tool| tool.name == name), "missing {name}");
        }
    }
}
