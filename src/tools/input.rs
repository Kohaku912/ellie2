use anyhow::{anyhow, Result};
use enigo::{
    Button, Coordinate,
    Direction::{Click, Press, Release},
    Enigo, Key, Keyboard, Mouse, Settings,
};
use serde_json::{json, Value};

pub fn mouse_move(args: Value) -> Result<Value> {
    let mut enigo = make_enigo()?;
    let x = required_i32(&args, "x")?;
    let y = required_i32(&args, "y")?;
    let absolute = args
        .get("absolute")
        .and_then(Value::as_bool)
        .unwrap_or(true);
    let coordinate = if absolute {
        Coordinate::Abs
    } else {
        Coordinate::Rel
    };
    enigo.move_mouse(x, y, coordinate)?;
    Ok(json!({ "success": true }))
}

pub fn mouse_click(args: Value) -> Result<Value> {
    let mut enigo = make_enigo()?;
    if let (Some(x), Some(y)) = (
        args.get("x").and_then(Value::as_i64),
        args.get("y").and_then(Value::as_i64),
    ) {
        enigo.move_mouse(x as i32, y as i32, Coordinate::Abs)?;
    }

    let button = match args
        .get("button")
        .and_then(Value::as_str)
        .unwrap_or("left")
        .to_lowercase()
        .as_str()
    {
        "right" => Button::Right,
        "middle" => Button::Middle,
        _ => Button::Left,
    };
    let clicks = if args.get("double").and_then(Value::as_bool).unwrap_or(false) {
        2
    } else {
        1
    };
    for _ in 0..clicks {
        enigo.button(button, Click)?;
    }
    Ok(json!({ "success": true }))
}

pub fn mouse_scroll(args: Value) -> Result<Value> {
    let mut enigo = make_enigo()?;
    let amount = args
        .get("y")
        .or_else(|| args.get("amount"))
        .and_then(Value::as_i64)
        .unwrap_or(0) as i32;
    let axis = match args
        .get("axis")
        .and_then(Value::as_str)
        .unwrap_or("vertical")
    {
        "horizontal" => enigo::Axis::Horizontal,
        _ => enigo::Axis::Vertical,
    };
    enigo.scroll(amount, axis)?;
    Ok(json!({ "success": true }))
}

pub fn keyboard_type(args: Value) -> Result<Value> {
    let text = args
        .get("text")
        .and_then(Value::as_str)
        .ok_or_else(|| anyhow!("text is required"))?;
    let mut enigo = make_enigo()?;
    enigo.text(text)?;
    Ok(json!({ "success": true }))
}

pub fn keyboard_shortcut(args: Value) -> Result<Value> {
    let key_values = args
        .get("keys")
        .and_then(Value::as_array)
        .ok_or_else(|| anyhow!("keys is required"))?;
    let keys = key_values
        .iter()
        .filter_map(Value::as_str)
        .map(parse_key)
        .collect::<Result<Vec<_>>>()?;
    if keys.is_empty() {
        return Err(anyhow!("no keys were provided"));
    }

    let mut enigo = make_enigo()?;
    for key in &keys {
        enigo.key(*key, Press)?;
    }
    for key in keys.iter().rev() {
        enigo.key(*key, Release)?;
    }
    Ok(json!({ "success": true }))
}

pub fn media_key(args: Value) -> Result<Value> {
    let action = args
        .get("action")
        .and_then(Value::as_str)
        .ok_or_else(|| anyhow!("action is required"))?;
    let key = match action {
        "volume_up" => Key::VolumeUp,
        "volume_down" => Key::VolumeDown,
        "mute" => Key::VolumeMute,
        "play_pause" => Key::MediaPlayPause,
        "next" => Key::MediaNextTrack,
        "prev" => Key::MediaPrevTrack,
        other => return Err(anyhow!("unknown media action: {other}")),
    };
    let mut enigo = make_enigo()?;
    enigo.key(key, Click)?;
    Ok(json!({ "success": true }))
}

fn make_enigo() -> Result<Enigo> {
    Enigo::new(&Settings::default()).map_err(|error| anyhow!(error.to_string()))
}

fn parse_key(value: &str) -> Result<Key> {
    Ok(match value.to_lowercase().as_str() {
        "ctrl" | "control" => Key::Control,
        "alt" => Key::Alt,
        "shift" => Key::Shift,
        "super" | "win" | "meta" => Key::Meta,
        "tab" => Key::Tab,
        "enter" | "return" => Key::Return,
        "escape" | "esc" => Key::Escape,
        "space" => Key::Space,
        "backspace" => Key::Backspace,
        "delete" | "del" => Key::Delete,
        "home" => Key::Home,
        "end" => Key::End,
        "pageup" => Key::PageUp,
        "pagedown" => Key::PageDown,
        "up" => Key::UpArrow,
        "down" => Key::DownArrow,
        "left" => Key::LeftArrow,
        "right" => Key::RightArrow,
        "f1" => Key::F1,
        "f2" => Key::F2,
        "f3" => Key::F3,
        "f4" => Key::F4,
        "f5" => Key::F5,
        "f6" => Key::F6,
        "f7" => Key::F7,
        "f8" => Key::F8,
        "f9" => Key::F9,
        "f10" => Key::F10,
        "f11" => Key::F11,
        "f12" => Key::F12,
        single if single.chars().count() == 1 => Key::Unicode(single.chars().next().unwrap()),
        other => return Err(anyhow!("unknown key: {other}")),
    })
}

fn required_i32(args: &Value, key: &str) -> Result<i32> {
    args.get(key)
        .and_then(Value::as_i64)
        .and_then(|value| i32::try_from(value).ok())
        .ok_or_else(|| anyhow!("{key} is required as i32"))
}
