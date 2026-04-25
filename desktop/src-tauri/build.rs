use std::process::Command;

fn main() {
    // Build-version string embedded into the tray menu so the user can
    // verify the installed .app really is the one they just built.
    // Format: 1.0.0+YYYYMMDD-HHMM.<short-sha>[-dirty]. Matches vite.config.ts.
    let pkg_version = env!("CARGO_PKG_VERSION");

    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    // Local time via UTC offset is not worth a chrono dep here; the
    // frontend's stamp already uses local time, and a matching UTC stamp
    // in the tray is fine for the "did I update?" question.
    let stamp = {
        // Inlined seconds-to-YYYYMMDD-HHMM using standard UNIX epoch math.
        let secs = now as i64;
        let days = secs / 86_400;
        let sod = secs - days * 86_400;
        let (hh, mm) = ((sod / 3_600) % 24, (sod / 60) % 60);
        let (y, mo, d) = civil_from_days(days);
        format!("{:04}{:02}{:02}-{:02}{:02}", y, mo, d, hh, mm)
    };

    let sha = Command::new("git")
        .args(["rev-parse", "--short", "HEAD"])
        .output()
        .ok()
        .and_then(|o| {
            if o.status.success() {
                Some(o.stdout)
            } else {
                None
            }
        })
        .map(|b| String::from_utf8_lossy(&b).trim().to_string())
        .unwrap_or_else(|| "local".to_string());

    // `trim_ascii()` requires Rust 1.80+, but our MSRV is 1.77.2 (set
    // in Cargo.toml). Use the parsed-string trim() instead — same
    // behaviour for our purposes since git output is always UTF-8.
    let dirty = Command::new("git")
        .args(["status", "--porcelain"])
        .output()
        .ok()
        .map(|o| !String::from_utf8_lossy(&o.stdout).trim().is_empty())
        .unwrap_or(false);
    let dirty_tag = if dirty { "-dirty" } else { "" };

    let build_version = format!("{}+{}.{}{}", pkg_version, stamp, sha, dirty_tag);
    println!("cargo:rustc-env=HOLONS_BUILD_VERSION={}", build_version);
    // Force a rebuild every invocation so the stamp isn't cached.
    println!("cargo:rerun-if-changed=build.rs");
    println!("cargo:rerun-if-changed=/dev/null");

    tauri_build::build()
}

// Howard Hinnant's days-from-civil (public domain) — spares us a chrono dep.
fn civil_from_days(z: i64) -> (i32, u32, u32) {
    let z = z + 719_468;
    let era = if z >= 0 { z } else { z - 146_096 } / 146_097;
    let doe = (z - era * 146_097) as u64;
    let yoe = (doe - doe / 1460 + doe / 36524 - doe / 146_096) / 365;
    let y = yoe as i64 + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = (doy - (153 * mp + 2) / 5 + 1) as u32;
    let m = (if mp < 10 { mp + 3 } else { mp - 9 }) as u32;
    let year = (y + (if m <= 2 { 1 } else { 0 })) as i32;
    (year, m, d)
}
