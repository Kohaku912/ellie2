use anyhow::{Context, Result};
use pc_ellie2::{config::Config, ws_client};
use tracing::info;
use tracing_subscriber::EnvFilter;

#[tokio::main]
async fn main() -> Result<()> {
    init_logging();

    let config = Config::from_env();
    info!(
        client_id = %config.client_id,
        server_url = %config.server_url,
        "starting pc client"
    );

    tokio::select! {
        result = ws_client::run_forever(config) => result,
        signal = tokio::signal::ctrl_c() => {
            signal.context("failed to listen for Ctrl+C")?;
            info!("shutdown requested");
            Ok(())
        }
    }
}

fn init_logging() {
    let filter =
        EnvFilter::try_from_default_env().unwrap_or_else(|_| EnvFilter::new("pc_ellie2=info,info"));
    tracing_subscriber::fmt().with_env_filter(filter).init();
}
