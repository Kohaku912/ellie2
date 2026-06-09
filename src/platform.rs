use anyhow::Result;
use serde::Serialize;

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct ActiveWindowInfo {
    pub title: String,
    pub app_name: String,
    pub process_id: u32,
}

#[derive(Clone, Debug, Serialize)]
pub struct WindowInfo {
    pub hwnd: i64,
    pub title: String,
    pub class_name: String,
    pub app_name: String,
    pub process_id: u32,
    pub x: i32,
    pub y: i32,
    pub width: i32,
    pub height: i32,
    pub visible: bool,
    pub minimized: bool,
    pub maximized: bool,
}

#[derive(Clone, Copy, Debug)]
pub enum WindowShowCommand {
    Hide,
    Show,
    Minimize,
    Maximize,
    Restore,
}

#[cfg(windows)]
pub fn active_window_snapshot() -> Result<Option<ActiveWindowInfo>> {
    windows::active_window_snapshot()
}

#[cfg(not(windows))]
pub fn active_window_snapshot() -> Result<Option<ActiveWindowInfo>> {
    Ok(None)
}

#[cfg(windows)]
pub fn list_windows() -> Result<Vec<WindowInfo>> {
    windows::list_windows()
}

#[cfg(not(windows))]
pub fn list_windows() -> Result<Vec<WindowInfo>> {
    Ok(vec![])
}

#[cfg(windows)]
pub fn focus_window(hwnd: i64) -> Result<()> {
    windows::focus_window(hwnd)
}

#[cfg(not(windows))]
pub fn focus_window(_hwnd: i64) -> Result<()> {
    Err(anyhow!("window operations are only implemented on Windows"))
}

#[cfg(windows)]
pub fn move_resize_window(hwnd: i64, x: i32, y: i32, width: i32, height: i32) -> Result<()> {
    windows::move_resize_window(hwnd, x, y, width, height)
}

#[cfg(not(windows))]
pub fn move_resize_window(_hwnd: i64, _x: i32, _y: i32, _width: i32, _height: i32) -> Result<()> {
    Err(anyhow!("window operations are only implemented on Windows"))
}

#[cfg(windows)]
pub fn show_window(hwnd: i64, command: WindowShowCommand) -> Result<()> {
    windows::show_window(hwnd, command)
}

#[cfg(not(windows))]
pub fn show_window(_hwnd: i64, _command: WindowShowCommand) -> Result<()> {
    Err(anyhow!("window operations are only implemented on Windows"))
}

#[cfg(windows)]
pub fn close_window(hwnd: i64) -> Result<()> {
    windows::close_window(hwnd)
}

#[cfg(not(windows))]
pub fn close_window(_hwnd: i64) -> Result<()> {
    Err(anyhow!("window operations are only implemented on Windows"))
}

#[cfg(windows)]
pub fn launch_application(target: &str) -> Result<()> {
    windows::launch_application(target)
}

#[cfg(not(windows))]
pub fn launch_application(target: &str) -> Result<()> {
    std::process::Command::new(target).spawn()?;
    Ok(())
}

#[cfg(windows)]
mod windows {
    use std::{path::Path, ptr::null};

    use anyhow::{anyhow, Result};
    use windows_sys::Win32::{
        Foundation::{CloseHandle, BOOL, HWND, LPARAM, RECT},
        System::Threading::{
            OpenProcess, QueryFullProcessImageNameW, PROCESS_QUERY_LIMITED_INFORMATION,
        },
        UI::{
            Shell::ShellExecuteW,
            WindowsAndMessaging::{
                EnumWindows, GetClassNameW, GetForegroundWindow, GetWindowRect,
                GetWindowTextLengthW, GetWindowTextW, GetWindowThreadProcessId, IsIconic,
                IsWindowVisible, IsZoomed, MoveWindow, PostMessageW, SetForegroundWindow,
                ShowWindow, SHOW_WINDOW_CMD, SW_HIDE, SW_MAXIMIZE, SW_MINIMIZE, SW_RESTORE,
                SW_SHOW, SW_SHOWNORMAL, WM_CLOSE,
            },
        },
    };

    use super::{ActiveWindowInfo, WindowInfo, WindowShowCommand};

    pub fn launch_application(target: &str) -> Result<()> {
        let operation = wide_null("open");
        let file = wide_null(target);

        let result = unsafe {
            ShellExecuteW(
                0 as HWND,
                operation.as_ptr(),
                file.as_ptr(),
                null(),
                null(),
                SW_SHOWNORMAL as SHOW_WINDOW_CMD,
            )
        } as isize;

        if result <= 32 {
            return Err(anyhow!("ShellExecuteW failed with code {result}"));
        }

        Ok(())
    }

    pub fn active_window_snapshot() -> Result<Option<ActiveWindowInfo>> {
        let hwnd = unsafe { GetForegroundWindow() };
        if hwnd == 0 {
            return Ok(None);
        }

        let title = window_title(hwnd);
        let process_id = process_id_for_window(hwnd);
        let process_path = process_id
            .and_then(query_process_image_name)
            .unwrap_or_default();
        let app_name = Path::new(&process_path)
            .file_name()
            .and_then(|name| name.to_str())
            .filter(|name| !name.is_empty())
            .unwrap_or("unknown")
            .to_string();

        Ok(Some(ActiveWindowInfo {
            title,
            app_name,
            process_id: process_id.unwrap_or_default(),
        }))
    }

    pub fn list_windows() -> Result<Vec<WindowInfo>> {
        let mut windows = Vec::<WindowInfo>::new();
        unsafe {
            EnumWindows(Some(enum_windows_proc), &mut windows as *mut _ as LPARAM);
        }
        Ok(windows)
    }

    pub fn focus_window(hwnd: i64) -> Result<()> {
        let hwnd = hwnd_from_i64(hwnd)?;
        let ok = unsafe { SetForegroundWindow(hwnd) };
        if ok == 0 {
            return Err(anyhow!("SetForegroundWindow failed"));
        }
        Ok(())
    }

    pub fn move_resize_window(hwnd: i64, x: i32, y: i32, width: i32, height: i32) -> Result<()> {
        if width <= 0 || height <= 0 {
            return Err(anyhow!("width and height must be positive"));
        }
        let hwnd = hwnd_from_i64(hwnd)?;
        let ok = unsafe { MoveWindow(hwnd, x, y, width, height, 1) };
        if ok == 0 {
            return Err(anyhow!("MoveWindow failed"));
        }
        Ok(())
    }

    pub fn show_window(hwnd: i64, command: WindowShowCommand) -> Result<()> {
        let hwnd = hwnd_from_i64(hwnd)?;
        let cmd = match command {
            WindowShowCommand::Hide => SW_HIDE,
            WindowShowCommand::Show => SW_SHOW,
            WindowShowCommand::Minimize => SW_MINIMIZE,
            WindowShowCommand::Maximize => SW_MAXIMIZE,
            WindowShowCommand::Restore => SW_RESTORE,
        };
        unsafe {
            ShowWindow(hwnd, cmd);
        }
        Ok(())
    }

    pub fn close_window(hwnd: i64) -> Result<()> {
        let hwnd = hwnd_from_i64(hwnd)?;
        let ok = unsafe { PostMessageW(hwnd, WM_CLOSE, 0, 0) };
        if ok == 0 {
            return Err(anyhow!("PostMessageW(WM_CLOSE) failed"));
        }
        Ok(())
    }

    unsafe extern "system" fn enum_windows_proc(hwnd: HWND, lparam: LPARAM) -> BOOL {
        let windows = &mut *(lparam as *mut Vec<WindowInfo>);
        if let Some(info) = window_info(hwnd) {
            windows.push(info);
        }
        1
    }

    fn window_info(hwnd: HWND) -> Option<WindowInfo> {
        let title = window_title(hwnd);
        let visible = unsafe { IsWindowVisible(hwnd) != 0 };
        if title.is_empty() && !visible {
            return None;
        }

        let process_id = process_id_for_window(hwnd).unwrap_or_default();
        let process_path = query_process_image_name(process_id).unwrap_or_default();
        let app_name = Path::new(&process_path)
            .file_name()
            .and_then(|name| name.to_str())
            .filter(|name| !name.is_empty())
            .unwrap_or("unknown")
            .to_string();
        let rect = window_rect(hwnd).unwrap_or(RECT {
            left: 0,
            top: 0,
            right: 0,
            bottom: 0,
        });

        Some(WindowInfo {
            hwnd: hwnd as i64,
            title,
            class_name: window_class_name(hwnd),
            app_name,
            process_id,
            x: rect.left,
            y: rect.top,
            width: rect.right.saturating_sub(rect.left),
            height: rect.bottom.saturating_sub(rect.top),
            visible,
            minimized: unsafe { IsIconic(hwnd) != 0 },
            maximized: unsafe { IsZoomed(hwnd) != 0 },
        })
    }

    fn window_rect(hwnd: HWND) -> Option<RECT> {
        let mut rect = RECT {
            left: 0,
            top: 0,
            right: 0,
            bottom: 0,
        };
        let ok = unsafe { GetWindowRect(hwnd, &mut rect) };
        (ok != 0).then_some(rect)
    }

    fn window_class_name(hwnd: HWND) -> String {
        let mut buffer = vec![0_u16; 256];
        let copied = unsafe { GetClassNameW(hwnd, buffer.as_mut_ptr(), buffer.len() as i32) };
        if copied <= 0 {
            return String::new();
        }
        String::from_utf16_lossy(&buffer[..copied as usize])
    }

    fn query_process_image_name(process_id: u32) -> Option<String> {
        let handle = unsafe { OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, process_id) };
        if handle == 0 {
            return None;
        }

        let mut buffer = vec![0_u16; 32_768];
        let mut size = buffer.len() as u32;
        let ok = unsafe { QueryFullProcessImageNameW(handle, 0, buffer.as_mut_ptr(), &mut size) };
        unsafe {
            CloseHandle(handle);
        }

        if ok == 0 || size == 0 {
            return None;
        }

        Some(String::from_utf16_lossy(&buffer[..size as usize]))
    }

    fn window_title(hwnd: HWND) -> String {
        let len = unsafe { GetWindowTextLengthW(hwnd) };
        if len <= 0 {
            return String::new();
        }

        let mut buffer = vec![0_u16; len as usize + 1];
        let copied = unsafe { GetWindowTextW(hwnd, buffer.as_mut_ptr(), buffer.len() as i32) };
        if copied <= 0 {
            return String::new();
        }

        String::from_utf16_lossy(&buffer[..copied as usize])
    }

    fn process_id_for_window(hwnd: HWND) -> Option<u32> {
        let mut process_id = 0_u32;
        unsafe {
            GetWindowThreadProcessId(hwnd, &mut process_id);
        }

        (process_id != 0).then_some(process_id)
    }

    fn wide_null(value: &str) -> Vec<u16> {
        value.encode_utf16().chain(std::iter::once(0)).collect()
    }

    fn hwnd_from_i64(hwnd: i64) -> Result<HWND> {
        if hwnd == 0 {
            return Err(anyhow!("hwnd must be non-zero"));
        }
        Ok(hwnd as HWND)
    }
}
