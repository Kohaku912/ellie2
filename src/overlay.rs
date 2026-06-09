use std::sync::{mpsc, Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};

use anyhow::{anyhow, Context, Result};
use base64::engine::general_purpose::STANDARD as BASE64;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

#[derive(Clone, Default)]
pub struct OverlayController {
    inner: Arc<Mutex<OverlayState>>,
}

#[derive(Default)]
struct OverlayState {
    close_tx: Option<mpsc::Sender<()>>,
    visible: bool,
    generation: u64,
    expires_at: Option<Instant>,
}

#[derive(Clone, Debug, Deserialize)]
pub struct OverlayConfig {
    #[serde(default)]
    pub x: i32,
    #[serde(default)]
    pub y: i32,
    #[serde(default = "default_width")]
    pub width: i32,
    #[serde(default = "default_height")]
    pub height: i32,
    #[serde(default = "default_opacity")]
    pub opacity: u8,
    #[serde(alias = "duration_ms", alias = "ttl_ms", alias = "erase_after_ms")]
    pub clear_after_ms: u64,
    #[serde(default)]
    pub items: Vec<OverlayItem>,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum OverlayItem {
    Text {
        text: String,
        x: i32,
        y: i32,
        #[serde(default = "default_font_size")]
        size: i32,
        #[serde(default = "default_color")]
        color: String,
        #[serde(default)]
        font: Option<String>,
    },
    Rect {
        x: i32,
        y: i32,
        width: i32,
        height: i32,
        #[serde(default = "default_color")]
        color: String,
        #[serde(default)]
        fill: bool,
        #[serde(default = "default_stroke_width")]
        stroke_width: i32,
    },
    Ellipse {
        x: i32,
        y: i32,
        width: i32,
        height: i32,
        #[serde(default = "default_color")]
        color: String,
        #[serde(default)]
        fill: bool,
        #[serde(default = "default_stroke_width")]
        stroke_width: i32,
    },
    Line {
        x1: i32,
        y1: i32,
        x2: i32,
        y2: i32,
        #[serde(default = "default_color")]
        color: String,
        #[serde(default = "default_stroke_width")]
        stroke_width: i32,
    },
    Image {
        x: i32,
        y: i32,
        width: i32,
        height: i32,
        path: Option<String>,
        data_base64: Option<String>,
    },
}

#[derive(Clone, Debug, Serialize)]
pub struct OverlayStatus {
    pub visible: bool,
    pub click_through: bool,
    pub remaining_ms: Option<u64>,
}

impl OverlayController {
    pub fn show(&self, config: OverlayConfig) -> Result<Value> {
        if config.width <= 0 || config.height <= 0 {
            return Err(anyhow!("overlay width and height must be positive"));
        }
        if config.clear_after_ms == 0 {
            return Err(anyhow!(
                "overlay clear_after_ms is required and must be greater than 0"
            ));
        }

        self.hide()?;
        let (close_tx, close_rx) = mpsc::channel();
        let thread_config = config.clone();
        thread::Builder::new()
            .name("pc_ellie2_overlay".to_string())
            .spawn(move || {
                if let Err(error) = run_overlay(thread_config, close_rx) {
                    tracing::warn!(%error, "overlay thread stopped with error");
                }
            })
            .context("failed to spawn overlay thread")?;

        let mut state = self.inner.lock().unwrap();
        state.generation = state.generation.wrapping_add(1);
        let generation = state.generation;
        state.close_tx = Some(close_tx.clone());
        state.visible = true;
        state.expires_at = Some(Instant::now() + Duration::from_millis(config.clear_after_ms));
        drop(state);

        let inner = self.inner.clone();
        let clear_after_ms = config.clear_after_ms;
        thread::Builder::new()
            .name("pc_ellie2_overlay_timer".to_string())
            .spawn(move || {
                thread::sleep(Duration::from_millis(clear_after_ms));
                let mut state = inner.lock().unwrap();
                if state.generation == generation && state.visible {
                    let _ = close_tx.send(());
                    state.close_tx = None;
                    state.visible = false;
                    state.expires_at = None;
                }
            })
            .context("failed to spawn overlay timer thread")?;

        Ok(json!({
            "success": true,
            "visible": true,
            "click_through": true,
            "width": config.width,
            "height": config.height,
            "clear_after_ms": config.clear_after_ms,
            "items": config.items.len(),
        }))
    }

    pub fn hide(&self) -> Result<Value> {
        let mut state = self.inner.lock().unwrap();
        if let Some(close_tx) = state.close_tx.take() {
            let _ = close_tx.send(());
        }
        state.visible = false;
        state.expires_at = None;
        Ok(json!({ "success": true, "visible": false }))
    }

    pub fn clear(&self) -> Result<Value> {
        self.hide()
    }

    pub fn status(&self) -> OverlayStatus {
        let mut state = self.inner.lock().unwrap();
        let remaining_ms = state.expires_at.map(|expires_at| {
            expires_at
                .saturating_duration_since(Instant::now())
                .as_millis() as u64
        });
        if remaining_ms == Some(0) {
            state.visible = false;
            state.close_tx = None;
            state.expires_at = None;
        }
        OverlayStatus {
            visible: state.visible,
            click_through: true,
            remaining_ms,
        }
    }
}

impl Default for OverlayConfig {
    fn default() -> Self {
        Self {
            x: 0,
            y: 0,
            width: default_width(),
            height: default_height(),
            opacity: default_opacity(),
            clear_after_ms: 0,
            items: vec![],
        }
    }
}

fn default_width() -> i32 {
    1280
}

fn default_height() -> i32 {
    720
}

fn default_opacity() -> u8 {
    255
}

fn default_font_size() -> i32 {
    32
}

fn default_color() -> String {
    "#ffffff".to_string()
}

fn default_stroke_width() -> i32 {
    2
}

#[cfg(windows)]
fn run_overlay(config: OverlayConfig, close_rx: mpsc::Receiver<()>) -> Result<()> {
    windows_overlay::run(config, close_rx)
}

#[cfg(not(windows))]
fn run_overlay(_config: OverlayConfig, close_rx: mpsc::Receiver<()>) -> Result<()> {
    let _ = close_rx.recv();
    Ok(())
}

#[cfg(windows)]
mod windows_overlay {
    use std::ffi::c_void;
    use std::mem::{size_of, zeroed};
    use std::ptr::null_mut;
    use std::sync::mpsc;

    use anyhow::{anyhow, Context, Result};
    use base64::Engine as _;
    use windows_sys::Win32::Foundation::{HWND, LPARAM, LRESULT, RECT, WPARAM};
    use windows_sys::Win32::Graphics::Gdi::{
        BeginPaint, CreateFontW, CreatePen, CreateSolidBrush, DeleteObject, Ellipse, EndPaint,
        FillRect, GetStockObject, LineTo, MoveToEx, Rectangle, SelectObject, SetBkMode,
        SetTextColor, StretchDIBits, TextOutW, BITMAPINFO, BITMAPINFOHEADER, BI_RGB, BLACK_BRUSH,
        DIB_RGB_COLORS, FW_NORMAL, HBRUSH, HDC, HGDIOBJ, OUT_DEFAULT_PRECIS, PAINTSTRUCT, PS_SOLID,
        RGBQUAD, SRCCOPY, TRANSPARENT,
    };
    use windows_sys::Win32::UI::WindowsAndMessaging::{
        CreateWindowExW, DefWindowProcW, DispatchMessageW, GetMessageW, GetWindowLongPtrW,
        PostMessageW, PostQuitMessage, RegisterClassW, SetLayeredWindowAttributes,
        SetWindowLongPtrW, ShowWindow, TranslateMessage, CS_HREDRAW, CS_VREDRAW, GWLP_USERDATA,
        LWA_ALPHA, LWA_COLORKEY, MSG, SW_SHOWNOACTIVATE, WM_CLOSE, WM_DESTROY, WM_NCCREATE,
        WM_PAINT, WNDCLASSW, WS_EX_LAYERED, WS_EX_NOACTIVATE, WS_EX_TOOLWINDOW, WS_EX_TOPMOST,
        WS_EX_TRANSPARENT, WS_POPUP,
    };

    use super::{parse_color, OverlayConfig, OverlayItem, BASE64};

    const CLASS_NAME: &str = "PcEllie2ClickThroughOverlay";
    const TRANSPARENT_KEY: u32 = 0;

    struct PaintState {
        config: OverlayConfig,
    }

    pub fn run(config: OverlayConfig, close_rx: mpsc::Receiver<()>) -> Result<()> {
        let class_name = wide_null(CLASS_NAME);
        let wnd_class = WNDCLASSW {
            style: CS_HREDRAW | CS_VREDRAW,
            lpfnWndProc: Some(wnd_proc),
            hInstance: 0,
            lpszClassName: class_name.as_ptr(),
            hbrBackground: unsafe { GetStockObject(BLACK_BRUSH) as HBRUSH },
            ..unsafe { zeroed() }
        };
        unsafe {
            RegisterClassW(&wnd_class);
        }

        let mut paint_state = Box::new(PaintState {
            config: config.clone(),
        });
        let state_ptr = paint_state.as_mut() as *mut PaintState;
        let hwnd = unsafe {
            CreateWindowExW(
                WS_EX_LAYERED
                    | WS_EX_TRANSPARENT
                    | WS_EX_TOPMOST
                    | WS_EX_TOOLWINDOW
                    | WS_EX_NOACTIVATE,
                class_name.as_ptr(),
                wide_null("pc_ellie2 overlay").as_ptr(),
                WS_POPUP,
                config.x,
                config.y,
                config.width,
                config.height,
                0,
                0,
                0,
                state_ptr as *const c_void,
            )
        };
        if hwnd == 0 {
            return Err(anyhow!("CreateWindowExW failed for overlay"));
        }

        let _keep_state_alive = paint_state;
        unsafe {
            SetLayeredWindowAttributes(
                hwnd,
                TRANSPARENT_KEY,
                config.opacity,
                LWA_COLORKEY | LWA_ALPHA,
            );
            ShowWindow(hwnd, SW_SHOWNOACTIVATE);
        }

        std::thread::spawn(move || {
            let _ = close_rx.recv();
            unsafe {
                PostMessageW(hwnd, WM_CLOSE, 0, 0);
            }
        });

        loop {
            let mut msg = MSG {
                hwnd: 0,
                message: 0,
                wParam: 0,
                lParam: 0,
                time: 0,
                pt: unsafe { zeroed() },
            };
            let result = unsafe { GetMessageW(&mut msg, 0, 0, 0) };
            if result <= 0 {
                break;
            }
            unsafe {
                TranslateMessage(&msg);
                DispatchMessageW(&msg);
            }
        }

        Ok(())
    }

    unsafe extern "system" fn wnd_proc(
        hwnd: HWND,
        msg: u32,
        wparam: WPARAM,
        lparam: LPARAM,
    ) -> LRESULT {
        match msg {
            WM_NCCREATE => {
                let createstruct =
                    lparam as *const windows_sys::Win32::UI::WindowsAndMessaging::CREATESTRUCTW;
                if !createstruct.is_null() {
                    SetWindowLongPtrW(hwnd, GWLP_USERDATA, (*createstruct).lpCreateParams as isize);
                }
                DefWindowProcW(hwnd, msg, wparam, lparam)
            }
            WM_PAINT => {
                let state = GetWindowLongPtrW(hwnd, GWLP_USERDATA) as *mut PaintState;
                if !state.is_null() {
                    paint(hwnd, &(*state).config);
                }
                0
            }
            WM_CLOSE | WM_DESTROY => {
                PostQuitMessage(0);
                0
            }
            _ => DefWindowProcW(hwnd, msg, wparam, lparam),
        }
    }

    unsafe fn paint(hwnd: HWND, config: &OverlayConfig) {
        let mut ps: PAINTSTRUCT = zeroed();
        let hdc = BeginPaint(hwnd, &mut ps);
        let black = CreateSolidBrush(TRANSPARENT_KEY);
        let rect = RECT {
            left: 0,
            top: 0,
            right: config.width,
            bottom: config.height,
        };
        FillRect(hdc, &rect, black);
        DeleteObject(black as HGDIOBJ);

        for item in &config.items {
            let _ = draw_item(hdc, item);
        }

        EndPaint(hwnd, &ps);
    }

    unsafe fn draw_item(hdc: HDC, item: &OverlayItem) -> Result<()> {
        match item {
            OverlayItem::Text {
                text,
                x,
                y,
                size,
                color,
                font,
            } => draw_text(hdc, text, *x, *y, *size, color, font.as_deref()),
            OverlayItem::Rect {
                x,
                y,
                width,
                height,
                color,
                fill,
                stroke_width,
            } => draw_rect(hdc, *x, *y, *width, *height, color, *fill, *stroke_width),
            OverlayItem::Ellipse {
                x,
                y,
                width,
                height,
                color,
                fill,
                stroke_width,
            } => draw_ellipse(hdc, *x, *y, *width, *height, color, *fill, *stroke_width),
            OverlayItem::Line {
                x1,
                y1,
                x2,
                y2,
                color,
                stroke_width,
            } => draw_line(hdc, *x1, *y1, *x2, *y2, color, *stroke_width),
            OverlayItem::Image {
                x,
                y,
                width,
                height,
                path,
                data_base64,
            } => draw_image(hdc, *x, *y, *width, *height, path, data_base64),
        }
    }

    unsafe fn draw_text(
        hdc: HDC,
        text: &str,
        x: i32,
        y: i32,
        size: i32,
        color: &str,
        font: Option<&str>,
    ) -> Result<()> {
        let font_name = wide_null(font.unwrap_or("Segoe UI"));
        let hfont = CreateFontW(
            -size,
            0,
            0,
            0,
            FW_NORMAL as i32,
            0,
            0,
            0,
            1,
            OUT_DEFAULT_PRECIS.into(),
            0,
            0,
            0,
            font_name.as_ptr(),
        );
        let old_font = SelectObject(hdc, hfont as HGDIOBJ);
        SetBkMode(hdc, TRANSPARENT as i32);
        SetTextColor(hdc, parse_color(color));
        let text = wide_null(text);
        TextOutW(
            hdc,
            x,
            y,
            text.as_ptr(),
            text.len().saturating_sub(1) as i32,
        );
        SelectObject(hdc, old_font);
        DeleteObject(hfont as HGDIOBJ);
        Ok(())
    }

    unsafe fn draw_rect(
        hdc: HDC,
        x: i32,
        y: i32,
        width: i32,
        height: i32,
        color: &str,
        fill: bool,
        stroke_width: i32,
    ) -> Result<()> {
        let color = parse_color(color);
        let pen = CreatePen(PS_SOLID, stroke_width.max(1), color);
        let brush = if fill {
            CreateSolidBrush(color)
        } else {
            GetStockObject(5) as HBRUSH
        };
        let old_pen = SelectObject(hdc, pen as HGDIOBJ);
        let old_brush = SelectObject(hdc, brush as HGDIOBJ);
        Rectangle(hdc, x, y, x + width, y + height);
        SelectObject(hdc, old_pen);
        SelectObject(hdc, old_brush);
        DeleteObject(pen as HGDIOBJ);
        if fill {
            DeleteObject(brush as HGDIOBJ);
        }
        Ok(())
    }

    unsafe fn draw_ellipse(
        hdc: HDC,
        x: i32,
        y: i32,
        width: i32,
        height: i32,
        color: &str,
        fill: bool,
        stroke_width: i32,
    ) -> Result<()> {
        let color = parse_color(color);
        let pen = CreatePen(PS_SOLID, stroke_width.max(1), color);
        let brush = if fill {
            CreateSolidBrush(color)
        } else {
            GetStockObject(5) as HBRUSH
        };
        let old_pen = SelectObject(hdc, pen as HGDIOBJ);
        let old_brush = SelectObject(hdc, brush as HGDIOBJ);
        Ellipse(hdc, x, y, x + width, y + height);
        SelectObject(hdc, old_pen);
        SelectObject(hdc, old_brush);
        DeleteObject(pen as HGDIOBJ);
        if fill {
            DeleteObject(brush as HGDIOBJ);
        }
        Ok(())
    }

    unsafe fn draw_line(
        hdc: HDC,
        x1: i32,
        y1: i32,
        x2: i32,
        y2: i32,
        color: &str,
        stroke_width: i32,
    ) -> Result<()> {
        let pen = CreatePen(PS_SOLID, stroke_width.max(1), parse_color(color));
        let old_pen = SelectObject(hdc, pen as HGDIOBJ);
        MoveToEx(hdc, x1, y1, null_mut());
        LineTo(hdc, x2, y2);
        SelectObject(hdc, old_pen);
        DeleteObject(pen as HGDIOBJ);
        Ok(())
    }

    unsafe fn draw_image(
        hdc: HDC,
        x: i32,
        y: i32,
        width: i32,
        height: i32,
        path: &Option<String>,
        data_base64: &Option<String>,
    ) -> Result<()> {
        let bytes = if let Some(path) = path {
            std::fs::read(path).with_context(|| format!("failed to read image: {path}"))?
        } else if let Some(data) = data_base64 {
            BASE64
                .decode(data)
                .context("failed to decode image base64")?
        } else {
            return Err(anyhow!("image item requires path or data_base64"));
        };
        let image = image::load_from_memory(&bytes)?.to_rgba8();
        let (source_width, source_height) = image.dimensions();
        let mut bgra = image.into_raw();
        for pixel in bgra.chunks_exact_mut(4) {
            pixel.swap(0, 2);
        }

        let mut info = BITMAPINFO {
            bmiHeader: BITMAPINFOHEADER {
                biSize: size_of::<BITMAPINFOHEADER>() as u32,
                biWidth: source_width as i32,
                biHeight: -(source_height as i32),
                biPlanes: 1,
                biBitCount: 32,
                biCompression: BI_RGB,
                biSizeImage: 0,
                biXPelsPerMeter: 0,
                biYPelsPerMeter: 0,
                biClrUsed: 0,
                biClrImportant: 0,
            },
            bmiColors: [RGBQUAD {
                rgbBlue: 0,
                rgbGreen: 0,
                rgbRed: 0,
                rgbReserved: 0,
            }],
        };

        StretchDIBits(
            hdc,
            x,
            y,
            width,
            height,
            0,
            0,
            source_width as i32,
            source_height as i32,
            bgra.as_ptr() as *const c_void,
            &mut info,
            DIB_RGB_COLORS,
            SRCCOPY,
        );
        Ok(())
    }

    fn wide_null(value: &str) -> Vec<u16> {
        value.encode_utf16().chain(std::iter::once(0)).collect()
    }
}

#[cfg(windows)]
fn parse_color(value: &str) -> u32 {
    let value = value.trim().trim_start_matches('#');
    if value.len() != 6 {
        return 0x00ff_ffff;
    }
    let Ok(rgb) = u32::from_str_radix(value, 16) else {
        return 0x00ff_ffff;
    };
    let r = rgb >> 16 & 0xff;
    let g = rgb >> 8 & 0xff;
    let b = rgb & 0xff;
    r | (g << 8) | (b << 16)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn overlay_config_requires_clear_after_ms() {
        let missing = serde_json::from_value::<OverlayConfig>(json!({
            "width": 100,
            "height": 100,
            "items": []
        }));
        assert!(missing.is_err());

        let present = serde_json::from_value::<OverlayConfig>(json!({
            "width": 100,
            "height": 100,
            "clear_after_ms": 1000,
            "items": []
        }));
        assert!(present.is_ok());
    }
}
