//! The shell's source digest (plan Task 9, "shell freshness"). `build.rs` includes this file to
//! bake the digest of the shell it builds into the binary (`ATLAS_SHELL_SOURCE`); the update
//! step writes the same digest of the live copy's shell source to `update.json`
//! (`shell_source` in `app/atlas/update_step.py`). When the two differ, the live copy has a
//! newer shell than the one running, and the tray says a widget update is available.
//!
//! The algorithm must stay exactly the Python one (its docstring): under `src-tauri`, the files
//! are `Cargo.toml`, `Cargo.lock`, `build.rs` and `tauri.conf.json` (each one that exists) and
//! every file under `src/`, `capabilities/` and `../dist/` (the starting pages, phase 4.5), at any
//! depth. Each file, in order of its `/`-separated path relative to `src-tauri` (compared as
//! bytes; a starting page's path is `../dist/<name>`), adds
//! `<path> NUL <byte length in decimal> NUL <bytes>` to one sha256; the result is its lowercase
//! hex digest. Like `os.walk`, a link to a folder is not followed; a link to a file is read.
//!
//! The path's bytes are its UTF-8 bytes. On unix a name that isn't UTF-8 counts with its raw
//! bytes, as the Python side's `encode("utf-8", "surrogateescape")` gives them under a UTF-8
//! locale (what `os.fsencode` gives there). On Windows such a name (a lone surrogate) is an error,
//! so an empty digest, which never claims an update (ruling D12).

use sha2::{Digest, Sha256};
use std::ffi::OsStr;
use std::io;
use std::path::{Path, PathBuf};

const FILES: [&str; 4] = ["Cargo.toml", "Cargo.lock", "build.rs", "tauri.conf.json"];
/// `../dist` is the starting pages (`frontendDist`), beside `src-tauri`; its files count as
/// `../dist/<name>`, the path Python's `os.path.relpath` gives them.
const FOLDERS: [&str; 3] = ["../dist", "capabilities", "src"];

/// A file name's bytes, as the Python side hashes them (see the module doc).
fn name_bytes(name: &OsStr) -> io::Result<&[u8]> {
    #[cfg(unix)]
    {
        use std::os::unix::ffi::OsStrExt;
        Ok(name.as_bytes())
    }
    #[cfg(not(unix))]
    {
        name.to_str().map(str::as_bytes).ok_or_else(|| io::Error::other("file name is not Unicode"))
    }
}

/// Each file under `dir` (whose path relative to `src-tauri` is `rel`), as (its relative path's
/// bytes, its path).
fn walk(dir: &Path, rel: &[u8], out: &mut Vec<(Vec<u8>, PathBuf)>) -> io::Result<()> {
    for entry in std::fs::read_dir(dir)? {
        let entry = entry?;
        let name = entry.file_name();
        let child = [rel, b"/", name_bytes(&name)?].concat();
        let kind = entry.file_type()?;
        let path = entry.path();
        let is_dir = if kind.is_symlink() {
            // os.walk lists a link to a folder with the folders but doesn't walk into it.
            std::fs::metadata(&path).map(|m| m.is_dir()).unwrap_or(false)
        } else {
            kind.is_dir()
        };
        if is_dir {
            if !kind.is_symlink() {
                walk(&path, &child, out)?;
            }
        } else {
            out.push((child, path));
        }
    }
    Ok(())
}

/// The digest of the shell source under `src_tauri` (the `app/desktop/src-tauri` folder).
pub fn digest(src_tauri: &Path) -> io::Result<String> {
    if !src_tauri.join("src").is_dir() {
        return Err(io::Error::new(io::ErrorKind::NotFound, "no src folder"));
    }
    let mut files: Vec<(Vec<u8>, PathBuf)> = FILES
        .iter()
        .map(|f| (f.as_bytes().to_vec(), src_tauri.join(f)))
        .filter(|(_, path)| path.is_file())
        .collect();
    for top in FOLDERS {
        let dir = src_tauri.join(top);
        if dir.is_dir() {
            walk(&dir, top.as_bytes(), &mut files)?;
        }
    }
    files.sort(); // by the path's bytes (each is unique)
    let mut h = Sha256::new();
    for (rel, path) in &files {
        let data = std::fs::read(path)?;
        h.update(rel);
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

    /// `SHELL_TREE_DIST` and `SHELL_VECTOR_DIST`: the starting pages beside `src-tauri`.
    const DIST: [(&str, &[u8]); 3] = [
        ("../dist/index.html", b"<p>widget</p>\n"),
        ("../dist/main.html", b"<p>main</p>\n"),
        ("../README.md", b"not hashed\n"), // beside dist/, not in it
    ];
    const VECTOR_DIST: &str = "abd1a8ac9744ae9d72c269f9488be0b036d83090bebdad8a8bd29b3731076a21";

    /// `<temp>/<name>-<pid>/src-tauri` with `TREE` in it (and `../dist` beside it when asked).
    fn tree_with(name: &str, extra: &[(&str, &[u8])]) -> PathBuf {
        let top = std::env::temp_dir().join(format!("{name}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&top);
        let base = top.join("src-tauri");
        for (rel, data) in TREE.iter().chain(extra) {
            let f = base.join(rel);
            std::fs::create_dir_all(f.parent().unwrap()).unwrap();
            std::fs::write(f, data).unwrap();
        }
        base
    }

    fn tree(name: &str) -> PathBuf {
        tree_with(name, &[])
    }

    fn remove(base: &Path) {
        let _ = std::fs::remove_dir_all(base.parent().unwrap());
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
        remove(&base);
    }

    #[test]
    fn starting_pages_count_as_dot_dot_dist() {
        let base = tree_with("bh-shell-source-dist", &DIST);
        assert_eq!(digest(&base).unwrap(), VECTOR_DIST);
        // A file beside dist/ doesn't count; a new or changed starting page does.
        std::fs::write(base.parent().unwrap().join("README.md"), b"other").unwrap();
        assert_eq!(digest(&base).unwrap(), VECTOR_DIST);
        std::fs::write(base.join("../dist/main.html"), b"<p>changed</p>\n").unwrap();
        let changed = digest(&base).unwrap();
        assert_ne!(changed, VECTOR_DIST);
        std::fs::create_dir_all(base.join("../dist/sub")).unwrap();
        std::fs::write(base.join("../dist/sub/new.css"), b"").unwrap();
        assert_ne!(digest(&base).unwrap(), changed);
        // No dist/ at all is the old digest.
        std::fs::remove_dir_all(base.join("../dist")).unwrap();
        assert_eq!(digest(&base).unwrap(), VECTOR);
        remove(&base);
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn name_not_utf8_counts_with_its_raw_bytes() {
        use std::os::unix::ffi::OsStrExt;
        let base = tree("bh-shell-source-bytes");
        std::fs::write(base.join("src").join(OsStr::from_bytes(b"caf\xe9.rs")), b"// not UTF-8\n").unwrap();
        // `SHELL_VECTOR_NOT_UTF8` in app/tests/test_update_step.py: the update step's digest.
        assert_eq!(digest(&base).unwrap(), "a173d86857aa60db6a95b186b37800a8f0413f89f70053eb317a6bdcec3e4270");
        remove(&base);
    }

    #[test]
    fn baked_digest_is_this_source() {
        // build.rs baked the digest of this very crate's source (unchanged since the build).
        let here = Path::new(env!("CARGO_MANIFEST_DIR"));
        assert_eq!(env!("ATLAS_SHELL_SOURCE"), digest(here).unwrap());
    }
}
