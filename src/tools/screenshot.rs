use std::io::Cursor;

use anyhow::{anyhow, Context, Result};
use base64::{engine::general_purpose::STANDARD as BASE64, Engine as _};
use image::{DynamicImage, ImageOutputFormat};
use screenshots::Screen;
use serde_json::{json, Value};

pub fn take_screenshot() -> Result<Value> {
    let screens = Screen::all().context("failed to enumerate screens")?;
    let screen = screens.first().ok_or_else(|| anyhow!("no screen found"))?;
    let image = screen
        .capture()
        .context("failed to capture primary screen")?;
    let width = image.width();
    let height = image.height();

    let mut png = Cursor::new(Vec::new());
    DynamicImage::ImageRgba8(image)
        .write_to(&mut png, ImageOutputFormat::Png)
        .context("failed to encode screenshot as png")?;

    let bytes = png.into_inner();
    Ok(json!({
        "mime_type": "image/png",
        "encoding": "base64",
        "data": BASE64.encode(&bytes),
        "width": width,
        "height": height,
        "bytes": bytes.len(),
    }))
}
