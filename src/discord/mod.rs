use std::io::{Read, Write};
use std::path::{Path, PathBuf};

use anyhow::{anyhow, Context, Result};
use reqwest::StatusCode;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use tokio::fs;
use url::Url;
use uuid::Uuid;

#[cfg(target_os = "windows")]
use std::fs::OpenOptions;
#[cfg(unix)]
use std::os::unix::net::UnixStream;

const OP_HANDSHAKE: u32 = 0;
const OP_FRAME: u32 = 1;
const OP_CLOSE: u32 = 2;
const OP_PING: u32 = 3;
const OP_PONG: u32 = 4;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DiscordTokens {
    pub access_token: String,
    pub refresh_token: String,
    pub token_type: Option<String>,
    pub scope: Option<String>,
    pub expires_in: Option<u64>,
}

#[derive(Clone, Debug)]
pub struct TokenStore {
    path: PathBuf,
    source_path: PathBuf,
}

impl TokenStore {
    pub fn new(path: PathBuf, source_path: PathBuf) -> Self {
        Self { path, source_path }
    }

    pub fn path(&self) -> &Path {
        &self.path
    }

    pub fn source_path(&self) -> &Path {
        &self.source_path
    }

    pub async fn migrate_from_source_if_missing(&self) -> Result<bool> {
        if self.path.exists() {
            return Ok(false);
        }
        if !self.source_path.exists() {
            return Ok(false);
        }
        let tokens = self.load_from(&self.source_path).await?;
        self.save(&tokens).await?;
        Ok(true)
    }

    pub async fn load(&self) -> Result<Option<DiscordTokens>> {
        if !self.path.exists() {
            return Ok(None);
        }
        self.load_from(&self.path).await.map(Some)
    }

    pub async fn save(&self, tokens: &DiscordTokens) -> Result<()> {
        if let Some(parent) = self.path.parent() {
            fs::create_dir_all(parent).await?;
        }
        let text = serde_json::to_string_pretty(tokens)?;
        fs::write(&self.path, text).await?;
        Ok(())
    }

    async fn load_from(&self, path: &Path) -> Result<DiscordTokens> {
        let text = fs::read_to_string(path)
            .await
            .with_context(|| format!("failed to read {}", path.display()))?;
        serde_json::from_str(&text).context("failed to parse Discord token file")
    }
}

enum IpcSocket {
    #[cfg(unix)]
    Unix(UnixStream),
    #[cfg(target_os = "windows")]
    Pipe(std::fs::File),
}

impl IpcSocket {
    fn write_all(&mut self, buffer: &[u8]) -> std::io::Result<()> {
        match self {
            #[cfg(unix)]
            IpcSocket::Unix(stream) => stream.write_all(buffer),
            #[cfg(target_os = "windows")]
            IpcSocket::Pipe(file) => file.write_all(buffer),
        }
    }

    fn read_exact(&mut self, buffer: &mut [u8]) -> std::io::Result<()> {
        match self {
            #[cfg(unix)]
            IpcSocket::Unix(stream) => stream.read_exact(buffer),
            #[cfg(target_os = "windows")]
            IpcSocket::Pipe(file) => file.read_exact(buffer),
        }
    }
}

pub struct DiscordRpc {
    http: reqwest::Client,
    socket: Option<IpcSocket>,
    pub client_id: Option<String>,
    pub access_token: Option<String>,
    pub connected: bool,
}

impl DiscordRpc {
    pub fn new() -> Self {
        Self {
            http: reqwest::Client::new(),
            socket: None,
            client_id: None,
            access_token: None,
            connected: false,
        }
    }

    pub fn connect(&mut self, client_id: &str) -> Result<Value> {
        let socket = Self::open_ipc_socket()?;
        self.socket = Some(socket);
        self.client_id = Some(client_id.to_string());

        let handshake = json!({ "v": 1, "client_id": client_id });
        self.write_frame(OP_HANDSHAKE, &handshake.to_string())?;

        let (op, payload) = self.read_frame()?;
        if op != OP_FRAME {
            return Err(anyhow!("unexpected opcode during Discord handshake: {op}"));
        }

        self.connected = true;
        Ok(payload)
    }

    pub fn disconnect(&mut self) {
        if let Some(socket) = self.socket.as_mut() {
            let _ = socket.write_all(&Self::encode_frame(OP_CLOSE, "{}"));
        }
        self.socket = None;
        self.connected = false;
        self.access_token = None;
    }

    pub fn authenticate(&mut self, access_token: &str) -> Result<Value> {
        let response = self.send_command(
            "AUTHENTICATE",
            json!({ "access_token": access_token }),
            None,
        )?;
        self.access_token = Some(access_token.to_string());
        Ok(response)
    }

    pub async fn is_access_token_valid(&self) -> Result<bool> {
        let Some(token) = self.access_token.as_ref() else {
            return Ok(false);
        };
        let response = self
            .http
            .get("https://discord.com/api/v10/users/@me")
            .bearer_auth(token)
            .send()
            .await?;
        Ok(response.status() == StatusCode::OK)
    }

    pub fn build_authorize_url(&self) -> Result<String> {
        let mut url = Url::parse("https://discord.com/oauth2/authorize")?;
        url.query_pairs_mut()
            .append_pair("response_type", "code")
            .append_pair(
                "scope",
                "rpc.voice.read rpc.video.write rpc.activities.write rpc.screenshare.read rpc.voice.write rpc rpc.notifications.read rpc.video.read rpc.screenshare.write identify",
            )
            .append_pair("redirect_uri", "http://localhost:8080/oauth/callback")
            .append_pair("prompt", "consent")
            .append_pair("client_id", self.client_id.as_deref().unwrap_or(""));
        Ok(url.to_string())
    }

    pub async fn refresh_discord_tokens(
        client_id: &str,
        client_secret: Option<&str>,
        refresh_token: &str,
    ) -> Result<DiscordTokens> {
        let client = reqwest::Client::new();
        let mut params = vec![
            ("grant_type", "refresh_token"),
            ("refresh_token", refresh_token),
            ("client_id", client_id),
        ];
        if let Some(client_secret) = client_secret {
            params.push(("client_secret", client_secret));
        }

        let response = client
            .post("https://discord.com/api/oauth2/token")
            .form(&params)
            .send()
            .await?
            .error_for_status()?;
        Ok(response.json::<DiscordTokens>().await?)
    }

    pub fn send_command(&mut self, cmd: &str, args: Value, evt: Option<&str>) -> Result<Value> {
        if !self.connected {
            return Err(anyhow!("not connected to Discord IPC"));
        }

        let nonce = Uuid::new_v4().to_string();
        let mut payload = json!({
            "cmd": cmd,
            "args": args,
            "nonce": nonce,
        });
        if let Some(evt) = evt {
            payload["evt"] = json!(evt);
        }

        self.write_frame(OP_FRAME, &payload.to_string())?;

        loop {
            let (op, response) = self.read_frame()?;
            match op {
                OP_FRAME => return Ok(response),
                OP_PING => self.write_frame(OP_PONG, &response.to_string())?,
                OP_CLOSE => {
                    self.connected = false;
                    return Err(anyhow!("Discord IPC closed: {response}"));
                }
                other => return Err(anyhow!("unexpected Discord IPC opcode: {other}")),
            }
        }
    }

    fn open_ipc_socket() -> Result<IpcSocket> {
        #[cfg(unix)]
        {
            for prefix in Self::ipc_prefixes() {
                for i in 0..10_u32 {
                    let path = format!("{prefix}/discord-ipc-{i}");
                    if let Ok(stream) = UnixStream::connect(&path) {
                        return Ok(IpcSocket::Unix(stream));
                    }
                }
            }
            Err(anyhow!("could not connect to Discord IPC socket"))
        }
        #[cfg(target_os = "windows")]
        {
            for i in 0..10_u32 {
                let path = format!(r"\\.\pipe\discord-ipc-{i}");
                if let Ok(file) = OpenOptions::new().read(true).write(true).open(path) {
                    return Ok(IpcSocket::Pipe(file));
                }
            }
            Err(anyhow!("could not connect to Discord IPC pipe"))
        }
    }

    #[cfg(unix)]
    fn ipc_prefixes() -> Vec<String> {
        ["XDG_RUNTIME_DIR", "TMPDIR", "TMP", "TEMP"]
            .into_iter()
            .filter_map(|key| std::env::var(key).ok())
            .chain(std::iter::once("/tmp".to_string()))
            .collect()
    }

    fn encode_frame(opcode: u32, payload: &str) -> Vec<u8> {
        let bytes = payload.as_bytes();
        let mut frame = Vec::with_capacity(8 + bytes.len());
        frame.extend_from_slice(&opcode.to_le_bytes());
        frame.extend_from_slice(&(bytes.len() as u32).to_le_bytes());
        frame.extend_from_slice(bytes);
        frame
    }

    fn write_frame(&mut self, opcode: u32, payload: &str) -> Result<()> {
        let frame = Self::encode_frame(opcode, payload);
        let socket = self
            .socket
            .as_mut()
            .ok_or_else(|| anyhow!("not connected to Discord IPC"))?;
        socket.write_all(&frame)?;
        Ok(())
    }

    fn read_frame(&mut self) -> Result<(u32, Value)> {
        let socket = self
            .socket
            .as_mut()
            .ok_or_else(|| anyhow!("not connected to Discord IPC"))?;
        let mut header = [0_u8; 8];
        socket.read_exact(&mut header)?;

        let opcode = u32::from_le_bytes(header[0..4].try_into().unwrap());
        let length = u32::from_le_bytes(header[4..8].try_into().unwrap()) as usize;
        let mut payload = vec![0_u8; length];
        socket.read_exact(&mut payload)?;

        let value = serde_json::from_slice(&payload)
            .unwrap_or_else(|_| json!({ "raw": String::from_utf8_lossy(&payload).to_string() }));
        Ok((opcode, value))
    }
}
