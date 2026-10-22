//! The shell's source digest (plan Task 9, "shell freshness"). `build.rs` includes this file to
//! bake the digest of the shell it builds into the binary (`BRAINHUB_SHELL_SOURCE`); the update
//! step writes the same digest of the live copy's shell source to `update.json`
//! (`shell_source` in `app/brainhub/update_step.py`). When the two differ, the live copy has a
//! newer shell than the one running, and the tray says a widget update is available.
//!
//! The algorithm must stay exactly the Python one (its docstring): under `src-tauri`, the files
//! are `Cargo.toml`, `Cargo.lock`, `build.rs` and `tauri.conf.json` (each one that exists) and
//! every file under `src/` and `capabilities/`, at any depth. Each file, in order of its
//! `/`-separated path relative to `src-tauri` (compared as UTF-8 bytes), adds
//! `<path> NUL <byte length in decimal> NUL <bytes>` to one sha256; the result is its lowercase
//! hex digest. Like `os.walk`, a link to a folder is not followed; a link to a file is read.

use sha2::{Digest, Sha256};
use std::io;
use std::path::Path;

const FILES: [&str; 4] = ["Cargo.toml", "Cargo.lock", "build.rs", "tauri.conf.json"];
const FOLDERS: [&str; 2] = ["capabilities", "src"];

fn walk(base: &Path, rel: &str, out: &mut Vec<String>) -> io::Result<()> {
    for entry in std::fs::read_dir(base.join(rel))? {
        let entry = entry?;
        let name = entry.file_name().into_string().map_err(|_| io::Error::other("file name is not UTF-8"))?;
        let child = format!("{rel}/{name}");
        let kind = entry.file_type()?;
        let is_dir = if kind.is_symlink() {
            // os.walk lists a link to a folder with the folders but doesn't walk into it.
            std::fs::metadata(entry.path()).map(|m| m.is_dir()).unwrap_or(false)
        } else {
            kind.is_dir()
        };
        if is_dir {
            if !kind.is_symlink() {
                walk(base, &child, out)?;
            }
        } else {
            out.push(child);
        }
    }
    Ok(())
}

/// The digest of the shell source under `src_tauri` (the `app/desktop/src-tauri` folder).
pub fn digest(src_tauri: &Path) -> io::Result<String> {
    if !src_tauri.join("src").is_dir() {
        return Err(io::Error::new(io::ErrorKind::NotFound, "no src folder"));
    }
    let mut files: Vec<String> = FILES.iter().filter(|f| src_tauri.join(f).is_file()).map(|f| f.to_string()).collect();
    for top in FOLDERS {
        if src_tauri.join(top).is_dir() {
            walk(src_tauri, top, &mut files)?;
        }
    }
    files.sort(); // str order is UTF-8 byte order
    let mut h = Sha256::new();
    for rel in &files {
        let data = std::fs::read(rel.split('/').fold(src_tauri.to_path_buf(), |p, part| p.join(part)))?;
        h.update(rel.as_bytes());
        h.update(b"\0");
        h.update(data.len().to_string().as_bytes());
        h.update(b"\0");
        h.update(&data);
    }
    Ok(h.finalize().iter().map(|b| format!("{b:02x}")).collect())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;

    /// `SHELL_TREE` and `SHELL_VECTOR` in `app/tests/test_update_step.py`.
    const TREE: [(&str, &[u8]); 9] = [
        ("Cargo.toml", b"[package]\n"),
        ("Cargo.lock", b"# lock\n"),
        ("build.rs", b"fn main() {}\n"),
        ("tauri.conf.json", b"{}\n"),
        ("capabilities/widget.json", b"{\"identifier\": \"widget\"}\n"),
        ("src/main.rs", b"fn main() {}\n"),
        ("src/sub/tray.rs", b"// tray\n"),
        ("icons/icon.png", b"png"),        // not hashed
        ("README.md", b"not hashed\n"), // not hashed
    ];
    const VECTOR: &str = "b191da605ebed435390a547458d0f400d62249d6e58a43e51ebcfa89c114acb0";

    fn tree(name: &str) -> PathBuf {
        let base = std::env::temp_dir().join(format!("{name}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&base);
        for (rel, data) in TREE {
            let f = base.join(rel);
            std::fs::create_dir_all(f.parent().unwrap()).unwrap();
            std::fs::write(f, data).unwrap();
        }
        base
    }

    #[test]
    fn shell_source_digest_matches_python() {
        let base = tree("bh-shell-source");
        assert_eq!(digest(&base).unwrap(), VECTOR);
        // Other files don't count; a covered one does; no src/ is an error (None in Python).
        std::fs::write(base.join("icons").join("icon.png"), b"other").unwrap();
        assert_eq!(digest(&base).unwrap(), VECTOR);
        std::fs::write(base.join("capabilities").join("new.json"), b"{}").unwrap();
        assert_ne!(digest(&base).unwrap(), VECTOR);
        std::fs::remove_dir_all(base.join("src")).unwrap();
        assert!(digest(&base).is_err());
        let _ = std::fs::remove_dir_all(&base);
    }

    #[test]
    fn baked_digest_is_this_source() {
        // build.rs baked the digest of this very crate's source (unchanged since the build).
        let here = Path::new(env!("CARGO_MANIFEST_DIR"));
        assert_eq!(env!("BRAINHUB_SHELL_SOURCE"), digest(here).unwrap());
    }
}
