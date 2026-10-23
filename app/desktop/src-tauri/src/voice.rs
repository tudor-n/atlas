//! The tray's "Pause voice" (phase 4 design §7.2): one small JSON POST to the hub's
//! `/api/voice/pause`. Plain std TCP, no HTTP crate: the hub is on loopback, the request is a
//! few bytes, and the hub's `local_only` checks (Host, JSON content type) are met as written.
use std::io::{Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::time::Duration;

/// The menu item ids, with the body each one sends.
pub const ITEMS: [(&str, &str, &str); 3] = [
    ("voice_hour", "For 1 hour", r#"{"minutes": 60}"#),
    ("voice_morning", "Until morning", r#"{"until": "morning"}"#),
    ("voice_resume", "Resume", r#"{"resume": true}"#),
];

pub fn body_for(id: &str) -> Option<&'static str> {
    ITEMS.iter().find(|(i, _, _)| *i == id).map(|(_, _, b)| *b)
}

/// The raw request: Host is what `local_only` allows, and the body is JSON (rule 5).
pub fn request(port: u16, body: &str) -> String {
    format!(
        "POST /api/voice/pause HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nContent-Type: application/json\r\n\
         Content-Length: {}\r\nConnection: close\r\n\r\n{body}",
        body.len()
    )
}

/// The status code from a response's first line, e.g. "HTTP/1.1 200 OK" -> 200.
pub fn status_of(response: &str) -> Option<u16> {
    response.lines().next()?.split_whitespace().nth(1)?.parse().ok()
}

/// Send it; the hub's answer's status code. Never blocks for long: 2 s to connect, 5 s to answer.
pub fn post_pause(port: u16, body: &str) -> std::io::Result<u16> {
    let addr = SocketAddr::from(([127, 0, 0, 1], port));
    let mut stream = TcpStream::connect_timeout(&addr, Duration::from_secs(2))?;
    stream.set_read_timeout(Some(Duration::from_secs(5)))?;
    stream.set_write_timeout(Some(Duration::from_secs(5)))?;
    stream.write_all(request(port, body).as_bytes())?;
    let mut buf = Vec::new();
    stream.take(64 * 1024).read_to_end(&mut buf)?;
    status_of(&String::from_utf8_lossy(&buf))
        .ok_or_else(|| std::io::Error::new(std::io::ErrorKind::InvalidData, "not an HTTP answer"))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::net::TcpListener;

    #[test]
    fn bodies_for_each_item() {
        assert_eq!(body_for("voice_hour"), Some(r#"{"minutes": 60}"#));
        assert_eq!(body_for("voice_morning"), Some(r#"{"until": "morning"}"#));
        assert_eq!(body_for("voice_resume"), Some(r#"{"resume": true}"#));
        assert_eq!(body_for("quit"), None);
    }

    #[test]
    fn the_request_meets_local_only() {
        let r = request(8090, r#"{"minutes": 60}"#);
        assert!(r.starts_with("POST /api/voice/pause HTTP/1.1\r\n"));
        assert!(r.contains("\r\nHost: 127.0.0.1:8090\r\n"));
        assert!(r.contains("\r\nContent-Type: application/json\r\n"));
        assert!(r.contains("\r\nContent-Length: 15\r\n"));
        assert!(r.ends_with("\r\n\r\n{\"minutes\": 60}"));
        assert!(!r.contains("Origin"));
    }

    #[test]
    fn status_line_parsing() {
        assert_eq!(status_of("HTTP/1.1 200 OK\r\n\r\n"), Some(200));
        assert_eq!(status_of("HTTP/1.1 503 Service Unavailable\r\n"), Some(503));
        assert_eq!(status_of("garbage"), None);
        assert_eq!(status_of(""), None);
    }

    #[test]
    fn posts_to_a_listening_hub() {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        let server = std::thread::spawn(move || {
            let (mut s, _) = listener.accept().unwrap();
            let mut buf = [0u8; 1024];
            let n = s.read(&mut buf).unwrap();
            s.write_all(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n{}").unwrap();
            String::from_utf8_lossy(&buf[..n]).to_string()
        });
        assert_eq!(post_pause(port, r#"{"resume": true}"#).unwrap(), 200);
        assert!(server.join().unwrap().contains("{\"resume\": true}"));
    }

    #[test]
    fn no_hub_is_an_error_not_a_hang() {
        let port = {
            let l = TcpListener::bind("127.0.0.1:0").unwrap();
            l.local_addr().unwrap().port()
        };
        assert!(post_pause(port, "{}").is_err());
    }
}
