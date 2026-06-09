use anyhow::Result;
use serde_json::{json, Value};
use sysinfo::{Components, Disks, Networks, System, Users};

use crate::platform;

pub fn system_snapshot() -> Result<Value> {
    Ok(json!({
        "system": get_system_info(),
        "hardware": get_hardware_info()?,
        "processes": get_processes()?,
        "active_window": platform::active_window_snapshot()?,
    }))
}

pub fn get_system() -> Result<Value> {
    Ok(get_system_info())
}

pub fn get_os_info() -> Result<Value> {
    Ok(get_system_info()["os"].clone())
}

pub fn get_uptime() -> Result<Value> {
    Ok(uptime_info())
}

pub fn get_users() -> Result<Value> {
    let users = Users::new_with_refreshed_list();
    Ok(json!(users
        .iter()
        .map(|user| {
            json!({
                "name": user.name(),
                "groups": user.groups().iter().map(|group| group.name()).collect::<Vec<_>>(),
            })
        })
        .collect::<Vec<_>>()))
}

pub fn get_battery() -> Result<Value> {
    Ok(json!(battery_info()))
}

pub fn get_processes() -> Result<Value> {
    let sys = refreshed_system();
    let processes: Vec<Value> = sys
        .processes()
        .iter()
        .map(|(pid, process)| {
            json!({
                "pid": pid.as_u32(),
                "name": process.name(),
                "memory_bytes": process.memory(),
                "virtual_memory_bytes": process.virtual_memory(),
                "cpu_usage": process.cpu_usage(),
                "status": format!("{:?}", process.status()),
                "exe": process.exe().map(|path| path.to_string_lossy().to_string()),
            })
        })
        .collect();
    Ok(json!(processes))
}

pub fn get_startup_programs() -> Result<Value> {
    #[cfg(windows)]
    {
        let output = std::process::Command::new("powershell")
            .args([
                "-NoProfile",
                "-Command",
                "$paths = @('HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Run','HKLM:\\Software\\Microsoft\\Windows\\CurrentVersion\\Run'); foreach ($p in $paths) { if (Test-Path $p) { Get-ItemProperty $p | Select-Object * -ExcludeProperty PSPath,PSParentPath,PSChildName,PSDrive,PSProvider } } | ConvertTo-Json -Depth 4",
            ])
            .output()?;
        let stdout = String::from_utf8_lossy(&output.stdout);
        let value = serde_json::from_str::<Value>(&stdout).unwrap_or_else(|_| json!([]));
        return Ok(value);
    }

    #[cfg(not(windows))]
    {
        Ok(json!([]))
    }
}

pub fn get_hardware_info() -> Result<Value> {
    let sys = refreshed_system();
    let components = Components::new_with_refreshed_list();
    let disks = Disks::new_with_refreshed_list();
    let networks = Networks::new_with_refreshed_list();

    Ok(json!({
        "cpu": cpu_info(&sys, &components),
        "memory": memory_info(&sys),
        "disks": disk_info(&disks),
        "network": network_info(&networks),
    }))
}

pub fn get_cpu() -> Result<Value> {
    let sys = refreshed_system();
    let components = Components::new_with_refreshed_list();
    Ok(cpu_info(&sys, &components))
}

pub fn get_memory() -> Result<Value> {
    let sys = refreshed_system();
    Ok(memory_info(&sys))
}

pub fn get_disks() -> Result<Value> {
    let disks = Disks::new_with_refreshed_list();
    Ok(json!(disk_info(&disks)))
}

pub fn get_network() -> Result<Value> {
    let networks = Networks::new_with_refreshed_list();
    Ok(json!(network_info(&networks)))
}

pub fn get_active_window() -> Result<Value> {
    Ok(json!(platform::active_window_snapshot()?))
}

fn get_system_info() -> Value {
    let users = Users::new_with_refreshed_list();
    json!({
        "os": {
            "os_name": System::name().unwrap_or_else(|| "Unknown".to_string()),
            "kernel_version": System::kernel_version().unwrap_or_else(|| "Unknown".to_string()),
            "os_version": System::os_version().unwrap_or_else(|| "Unknown".to_string()),
            "hostname": System::host_name().unwrap_or_else(|| "Unknown".to_string()),
        },
        "uptime": uptime_info(),
        "users": users.iter().map(|user| {
            json!({
                "name": user.name(),
                "groups": user.groups().iter().map(|group| group.name()).collect::<Vec<_>>(),
            })
        }).collect::<Vec<_>>(),
        "battery": battery_info(),
    })
}

fn refreshed_system() -> System {
    let mut sys = System::new_all();
    sys.refresh_all();
    sys
}

fn uptime_info() -> Value {
    let secs = System::uptime();
    let days = secs / 86_400;
    let hours = (secs % 86_400) / 3_600;
    let minutes = (secs % 3_600) / 60;
    let seconds = secs % 60;
    json!({
        "uptime_secs": secs,
        "uptime_human": format!("{days}d {hours}h {minutes}m {seconds}s"),
    })
}

fn battery_info() -> Vec<Value> {
    let manager = match battery::Manager::new() {
        Ok(manager) => manager,
        Err(_) => return vec![],
    };
    let batteries = match manager.batteries() {
        Ok(batteries) => batteries,
        Err(_) => return vec![],
    };

    batteries
        .filter_map(|battery| battery.ok())
        .map(|battery| {
            use battery::State;
            let state = match battery.state() {
                State::Charging => "Charging",
                State::Discharging => "Discharging",
                State::Empty => "Empty",
                State::Full => "Full",
                _ => "Unknown",
            };
            json!({
                "percentage": battery.state_of_charge().value * 100.0,
                "is_charging": matches!(battery.state(), State::Charging | State::Full),
                "state": state,
                "time_to_full": battery.time_to_full().map(|value| value.value as u64),
                "time_to_empty": battery.time_to_empty().map(|value| value.value as u64),
            })
        })
        .collect()
}

fn cpu_info(sys: &System, components: &Components) -> Value {
    let cpus = sys.cpus();
    let brand = cpus
        .first()
        .map(|cpu| cpu.brand().to_string())
        .unwrap_or_else(|| "Unknown".to_string());
    let cores: Vec<Value> = cpus
        .iter()
        .map(|cpu| {
            json!({
                "name": cpu.name(),
                "usage": cpu.cpu_usage(),
                "frequency_mhz": cpu.frequency(),
            })
        })
        .collect();
    let temperature_celsius = components
        .iter()
        .find(|component| {
            let label = component.label().to_lowercase();
            label.contains("cpu") || label.contains("core 0") || label.contains("package")
        })
        .map(|component| component.temperature());

    json!({
        "brand": brand,
        "physical_cores": sys.physical_core_count().unwrap_or(cores.len()),
        "logical_cores": cores.len(),
        "global_usage": sys.global_cpu_info().cpu_usage(),
        "cores": cores,
        "temperature_celsius": temperature_celsius,
    })
}

fn memory_info(sys: &System) -> Value {
    let total = sys.total_memory();
    let used = sys.used_memory();
    let usage_percent = if total > 0 {
        used as f32 / total as f32 * 100.0
    } else {
        0.0
    };
    json!({
        "total_bytes": total,
        "used_bytes": used,
        "free_bytes": sys.free_memory(),
        "usage_percent": usage_percent,
        "swap_total_bytes": sys.total_swap(),
        "swap_used_bytes": sys.used_swap(),
    })
}

fn disk_info(disks: &Disks) -> Vec<Value> {
    disks
        .iter()
        .map(|disk| {
            let total = disk.total_space();
            let available = disk.available_space();
            let used = total.saturating_sub(available);
            let usage_percent = if total > 0 {
                used as f32 / total as f32 * 100.0
            } else {
                0.0
            };
            json!({
                "name": disk.name().to_string_lossy(),
                "mount_point": disk.mount_point().to_string_lossy(),
                "file_system": disk.file_system().to_string_lossy(),
                "total_bytes": total,
                "available_bytes": available,
                "used_bytes": used,
                "usage_percent": usage_percent,
                "is_removable": disk.is_removable(),
            })
        })
        .collect()
}

fn network_info(networks: &Networks) -> Vec<Value> {
    let local_ip = local_ip_address::local_ip().ok().map(|ip| ip.to_string());
    networks
        .iter()
        .map(|(name, data)| {
            json!({
                "name": name,
                "local_ip": local_ip.clone(),
                "mac_address": data.mac_address().to_string(),
                "received_bytes": data.total_received(),
                "transmitted_bytes": data.total_transmitted(),
                "received_per_sec": data.received(),
                "transmitted_per_sec": data.transmitted(),
            })
        })
        .collect()
}
