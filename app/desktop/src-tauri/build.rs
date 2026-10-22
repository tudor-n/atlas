//! Tauri's build step, plus the shell-source digest (plan Task 9): the digest of this crate's own
//! source, computed exactly as the update step's `shell_source` does for the live copy, is baked
//! in as `BRAINHUB_SHELL_SOURCE`. The shell compares the two to notice a newer shell in the live
//! copy ("widget update available").

#[path = "src/shell_source.rs"]
mod shell_source;

fn main() {
    let here = std::path::PathBuf::from(std::env::var_os("CARGO_MANIFEST_DIR").expect("CARGO_MANIFEST_DIR"));
    let digest = shell_source::digest(&here).unwrap_or_else(|e| {
        // An empty digest means "unknown": the shell then never says an update is available.
        println!("cargo:warning=shell source digest failed: {e}");
        String::new()
    });
    println!("cargo:rustc-env=BRAINHUB_SHELL_SOURCE={digest}");
    for path in ["Cargo.toml", "Cargo.lock", "build.rs", "tauri.conf.json", "src", "capabilities"] {
        println!("cargo:rerun-if-changed={path}");
    }
    tauri_build::build()
}
