use std::{
    env,
    path::{Path, PathBuf},
    time::Duration,
};

const DEFAULT_WS_URL: &str = "ws://127.0.0.1:8765/ws/pc";
const DEFAULT_POLL_MS: u64 = 1_000;
const DEFAULT_DISCORD_SOURCE_DIR: &str = r"C:\Users\kohak\programs\pc_ellie";

#[derive(Clone, Debug)]
pub struct Config {
    pub server_url: String,
    pub client_id: String,
    pub poll_interval: Duration,
    pub discord_source_dir: PathBuf,
    pub discord_token_store_path: PathBuf,
    pub discord_client_id: Option<String>,
    pub discord_client_secret: Option<String>,
}

impl Config {
    pub fn from_env() -> Self {
        let server_url = env::var("ELLIE_WS_URL").unwrap_or_else(|_| DEFAULT_WS_URL.to_string());
        let client_id = env::var("ELLIE_CLIENT_ID")
            .ok()
            .filter(|value| !value.trim().is_empty())
            .unwrap_or_else(default_client_id);
        let poll_ms = env::var("ELLIE_POLL_MS")
            .ok()
            .and_then(|value| value.parse::<u64>().ok())
            .filter(|value| *value > 0)
            .unwrap_or(DEFAULT_POLL_MS);
        let discord_source_dir = env::var("ELLIE_DISCORD_SOURCE_DIR")
            .map(PathBuf::from)
            .unwrap_or_else(|_| PathBuf::from(DEFAULT_DISCORD_SOURCE_DIR));
        let discord_token_store_path = env::var("ELLIE_DISCORD_TOKEN_STORE")
            .map(PathBuf::from)
            .unwrap_or_else(|_| {
                env::current_dir()
                    .unwrap_or_else(|_| PathBuf::from("."))
                    .join("discord_tokens.json")
            });
        let local_env = env::current_dir()
            .unwrap_or_else(|_| PathBuf::from("."))
            .join(".env");
        let source_env = discord_source_dir.join(".env");

        Self {
            server_url,
            client_id,
            poll_interval: Duration::from_millis(poll_ms),
            discord_client_id: secret_value("DISCORD_CLIENT_ID", &local_env, &source_env),
            discord_client_secret: secret_value("DISCORD_CLIENT_SECRET", &local_env, &source_env),
            discord_source_dir,
            discord_token_store_path,
        }
    }
}

fn secret_value(key: &str, local_env: &Path, source_env: &Path) -> Option<String> {
    env::var(key)
        .ok()
        .filter(|value| !value.trim().is_empty())
        .or_else(|| dotenv_value(local_env, key))
        .or_else(|| dotenv_value(source_env, key))
}

fn dotenv_value(path: &Path, key: &str) -> Option<String> {
    let iter = dotenvy::from_path_iter(path).ok()?;
    iter.filter_map(Result::ok)
        .find_map(|(name, value)| (name == key && !value.trim().is_empty()).then_some(value))
}

fn default_client_id() -> String {
    env::var("COMPUTERNAME")
        .or_else(|_| env::var("HOSTNAME"))
        .ok()
        .filter(|value| !value.trim().is_empty())
        .unwrap_or_else(|| "pc-client".to_string())
}
