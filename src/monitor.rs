use std::time::Duration;

use tokio::{sync::mpsc, time};
use tracing::warn;

use crate::{
    platform::{active_window_snapshot, ActiveWindowInfo},
    protocol::{ClientMessage, OutboundMessage, WindowState},
    util::now_ms,
};

pub async fn monitor_active_window(
    out_tx: mpsc::Sender<OutboundMessage>,
    client_id: String,
    poll_interval: Duration,
) {
    let mut last_seen: Option<ActiveWindowInfo> = None;
    let mut seq = 0_u64;
    let mut ticker = time::interval(poll_interval);
    ticker.set_missed_tick_behavior(time::MissedTickBehavior::Delay);

    loop {
        ticker.tick().await;

        let current = match tokio::task::spawn_blocking(active_window_snapshot).await {
            Ok(Ok(snapshot)) => snapshot,
            Ok(Err(error)) => {
                warn!(%error, "failed to inspect active window");
                continue;
            }
            Err(error) => {
                warn!(%error, "active window worker failed");
                continue;
            }
        };

        if current == last_seen {
            continue;
        }

        seq += 1;
        last_seen = current.clone();
        let message = ClientMessage::StateDelta {
            client_id: client_id.clone(),
            seq,
            timestamp_ms: now_ms(),
            state: WindowState {
                active_window: current,
            },
        };

        if let Err(error) = out_tx.send(message.into()).await {
            warn!(%error, "active window monitor stopped");
            break;
        }
    }
}
