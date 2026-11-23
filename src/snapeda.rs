use std::io::{Cursor, Read};
use std::path::{Path, PathBuf};
use std::time::Duration;

use regex::Regex;
use reqwest::blocking::{Client, Response};
use reqwest::header::{ACCEPT, COOKIE, ORIGIN, REFERER, SET_COOKIE};
use serde_json::Value;

use crate::ranking::normalize_mpn;

const BASE: &str = "https://www.snapeda.com";
const MAX_PAGE: u64 = 5 * 1024 * 1024;
const MAX_FILE: u64 = 150 * 1024 * 1024;
const USER_AGENT: &str = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36";

fn text(value: Option<&Value>) -> String {
    match value {
        Some(Value::String(s)) => s.trim().to_owned(),
        Some(Value::Number(n)) => n.to_string(),
        Some(Value::Bool(b)) => b.to_string(),
        _ => String::new(),
    }
}

fn flag(value: Option<&Value>) -> bool {
    ["1", "true", "yes"].contains(&text(value).to_ascii_lowercase().as_str())
}

fn select_part(data: &Value, mpn: &str, manufacturer: &str) -> Result<u64, String> {
    let rows = data
        .get("results")
        .and_then(Value::as_array)
        .ok_or("SnapEDA returned an unexpected search response.")?;
    let matches: Vec<_> = rows
        .iter()
        .filter(|row| {
            normalize_mpn(&text(row.get("part_number"))) == normalize_mpn(mpn)
                && flag(row.get("has_symbol"))
                && flag(row.get("has_footprint"))
                && (manufacturer.is_empty()
                    || text(row.get("manufacturer")).eq_ignore_ascii_case(manufacturer.trim()))
        })
        .filter_map(|row| text(row.get("unipart_id")).parse::<u64>().ok())
        .collect();
    match matches.as_slice() {
        [id] => Ok(*id),
        [] => Err(
            "SnapEDA has no exact model with symbol and footprint for this part and manufacturer."
                .into(),
        ),
        _ => Err(
            "More than one SnapEDA model matches; choose a manufacturer or download manually."
                .into(),
        ),
    }
}

fn form_token(html: &str) -> Result<String, String> {
    let input = Regex::new(r#"(?is)<input\b[^>]*>"#).expect("constant regex");
    let attribute = Regex::new(r#"(?i)([a-z_]+)\s*=\s*['"]([^'"]*)['"]"#).expect("constant regex");
    for tag in input.find_iter(html) {
        let mut name = "";
        let mut value = "";
        for captures in attribute.captures_iter(tag.as_str()) {
            if captures[1].eq_ignore_ascii_case("name") {
                name = captures.get(2).unwrap().as_str();
            }
            if captures[1].eq_ignore_ascii_case("value") {
                value = captures.get(2).unwrap().as_str();
            }
        }
        if name == "csrfmiddlewaretoken"
            && !value.is_empty()
            && value.chars().all(|c| c.is_ascii_alphanumeric())
        {
            return Ok(value.to_owned());
        }
    }
    Err("SnapEDA did not provide a valid login form token.".into())
}

fn download_path(details: &Value) -> Result<String, String> {
    let path = text(details.get("download_url"));
    let parts: Vec<_> = path.split('/').collect();
    if parts.len() != 7
        || parts[0] != ""
        || parts[1] != "parts"
        || parts[2] != "snapapi"
        || parts[3] != "download-component"
        || parts[4].is_empty()
        || parts[5].is_empty()
        || !parts[4].chars().all(|c| c.is_ascii_digit())
        || !parts[5].chars().all(|c| c.is_ascii_digit())
        || parts[6] != "%s"
    {
        return Err("SnapEDA returned an invalid download path.".into());
    }
    Ok(path.replace("%s", "altium_native"))
}

fn read_limited(mut response: Response, limit: u64) -> Result<Vec<u8>, String> {
    if response.content_length().is_some_and(|len| len > limit) {
        return Err("SnapEDA response is too large.".into());
    }
    let mut body = Vec::new();
    response
        .by_ref()
        .take(limit + 1)
        .read_to_end(&mut body)
        .map_err(|_| "Could not read the SnapEDA response.")?;
    if body.len() as u64 > limit {
        return Err("SnapEDA response is too large.".into());
    }
    Ok(body)
}

struct SnapClient {
    client: Client,
    csrf: String,
    session: String,
    #[cfg(test)]
    base: String,
}

impl SnapClient {
    fn new() -> Result<Self, String> {
        let client = Client::builder().timeout(Duration::from_secs(60));
        #[cfg(not(test))]
        let client = client.https_only(true);
        let client = client
            .redirect(reqwest::redirect::Policy::none())
            .user_agent(USER_AGENT)
            .build()
            .map_err(|_| "Could not initialize the SnapEDA HTTP client.")?;
        Ok(Self {
            client,
            csrf: String::new(),
            session: String::new(),
            #[cfg(test)]
            base: BASE.to_owned(),
        })
    }

    fn send(
        &mut self,
        method: reqwest::Method,
        path: &str,
        form: Option<&[(&str, &str)]>,
        extra_csrf: bool,
    ) -> Result<(reqwest::StatusCode, String, Vec<u8>), String> {
        if !path.starts_with('/') || path.starts_with("//") || path.contains(['\r', '\n']) {
            return Err("Refusing to send the SnapEDA session to an invalid path.".into());
        }
        #[cfg(not(test))]
        let base = BASE;
        #[cfg(test)]
        let base = self.base.as_str();
        let url = format!("{base}{path}");
        let referer = if path.starts_with("/account/login/") {
            format!("{base}/account/login/")
        } else if path.starts_with("/api/v1/search_local_internal") {
            format!("{base}/search/")
        } else {
            format!("{base}/")
        };
        let mut request = self
            .client
            .request(method, &url)
            .header(ACCEPT, "application/json, text/html, */*")
            .header(REFERER, referer)
            .header(ORIGIN, base);
        let mut cookies = Vec::new();
        if !self.csrf.is_empty() {
            cookies.push(format!("csrftoken={}", self.csrf));
        }
        if !self.session.is_empty() {
            cookies.push(format!("sessionid={}", self.session));
        }
        if !cookies.is_empty() {
            request = request.header(COOKIE, cookies.join("; "));
        }
        if extra_csrf {
            request = request
                .header("X-CSRFToken", &self.csrf)
                .header("X-Requested-With", "XMLHttpRequest");
        }
        if let Some(form) = form {
            request = request.form(form);
        }
        let response = request
            .send()
            .map_err(|_| "Network error contacting SnapEDA.".to_owned())?;
        let status = response.status();
        let location = response
            .headers()
            .get(reqwest::header::LOCATION)
            .and_then(|v| v.to_str().ok())
            .unwrap_or("")
            .to_owned();
        for cookie in response.headers().get_all(SET_COOKIE) {
            if let Ok(cookie) = cookie.to_str() {
                if let Some((name, value)) = cookie.split(';').next().unwrap_or("").split_once('=')
                {
                    if value.chars().all(|c| c.is_ascii_alphanumeric()) {
                        match name {
                            "csrftoken" => self.csrf = value.to_owned(),
                            "sessionid" => self.session = value.to_owned(),
                            _ => {}
                        }
                    }
                }
            }
        }
        if status.as_u16() == 429 {
            return Err("SnapEDA rate-limited the request; wait and try again.".into());
        }
        if status.as_u16() == 403 || status.as_u16() == 503 {
            return Err(format!("SnapEDA refused the request (HTTP {status}); use the browser if its protection blocks the app."));
        }
        Ok((status, location, read_limited(response, MAX_PAGE)?))
    }
}

fn json(bytes: &[u8]) -> Result<Value, String> {
    serde_json::from_slice(bytes).map_err(|_| "SnapEDA did not return valid JSON.".into())
}

trait Session {
    fn send(
        &mut self,
        method: reqwest::Method,
        path: &str,
        form: Option<&[(&str, &str)]>,
        extra_csrf: bool,
    ) -> Result<(reqwest::StatusCode, String, Vec<u8>), String>;
    fn csrf(&self) -> &str;
    fn session(&self) -> &str;
}

impl Session for SnapClient {
    fn send(
        &mut self,
        method: reqwest::Method,
        path: &str,
        form: Option<&[(&str, &str)]>,
        extra_csrf: bool,
    ) -> Result<(reqwest::StatusCode, String, Vec<u8>), String> {
        SnapClient::send(self, method, path, form, extra_csrf)
    }

    fn csrf(&self) -> &str {
        &self.csrf
    }
    fn session(&self) -> &str {
        &self.session
    }
}

fn file_url(
    snap: &mut impl Session,
    mpn: &str,
    manufacturer: &str,
    username: &str,
    password: &str,
) -> Result<(u64, url::Url), String> {
    let (status, _, page) = snap.send(reqwest::Method::GET, "/account/login/", None, false)?;
    if !status.is_success() || snap.csrf().is_empty() {
        return Err(format!(
            "SnapEDA login page failed (HTTP {status}) or did not provide a CSRF cookie."
        ));
    }
    let token = form_token(&String::from_utf8_lossy(&page))?;
    let (status, _, search) = snap.send(
        reqwest::Method::POST,
        "/api/v1/search_local_internal",
        Some(&[("q", mpn.trim())]),
        true,
    )?;
    if !status.is_success() {
        return Err(format!("SnapEDA search failed (HTTP {status})."));
    }
    let id = select_part(&json(&search)?, mpn, manufacturer)?;
    let (status, _, details) = snap.send(
        reqwest::Method::GET,
        &format!("/api/get_part_for_unipart/{id}"),
        None,
        false,
    )?;
    if !status.is_success() {
        return Err(format!("SnapEDA part details failed (HTTP {status})."));
    }
    let path = download_path(&json(&details)?)?;
    let previous_session = snap.session().to_owned();
    let (status, location, _) = snap.send(
        reqwest::Method::POST,
        "/account/login/?next=/home/",
        Some(&[
            ("csrfmiddlewaretoken", &token),
            ("next", "/home/"),
            ("username", username),
            ("password", password),
            ("plugin", "None"),
            ("ref_email", ""),
        ]),
        false,
    )?;
    if !matches!(status.as_u16(), 301..=303)
        || location.is_empty()
        || location.contains("/account/login/")
        || snap.session().is_empty()
        || snap.session() == previous_session
    {
        return Err("SnapEDA sign-in failed; check the username and password.".into());
    }
    let (status, _, response) = snap.send(reqwest::Method::GET, &path, None, true)?;
    if !status.is_success() {
        return Err(format!("SnapEDA download request failed (HTTP {status})."));
    }
    let response = json(&response)?;
    if let Some(error) = response.get("error").and_then(Value::as_str) {
        let error = error
            .replace(password, "[redacted]")
            .replace(username, "[redacted]");
        return Err(format!(
            "SnapEDA: {}",
            error.chars().take(200).collect::<String>()
        ));
    }
    let url = url::Url::parse(&text(response.get("url")))
        .map_err(|_| "SnapEDA did not return a valid download URL.")?;
    if url.scheme() != "https"
        || url.host_str().is_none()
        || !url.username().is_empty()
        || url.password().is_some()
    {
        return Err("SnapEDA returned an unsafe download URL.".into());
    }
    Ok((id, url))
}

fn file_extension(bytes: &[u8], url: &url::Url) -> Result<String, String> {
    if bytes.starts_with(&[0xd0, 0xcf, 0x11, 0xe0, 0xa1, 0xb1, 0x1a, 0xe1]) {
        let ext = Path::new(url.path())
            .extension()
            .and_then(|s| s.to_str())
            .unwrap_or("")
            .to_ascii_lowercase();
        if !["intlib", "schlib", "pcblib"].contains(&ext.as_str()) {
            return Err("SnapEDA returned an Altium file of unknown type.".into());
        }
        Ok(ext)
    } else {
        let mut zip = zip::ZipArchive::new(Cursor::new(bytes))
            .map_err(|_| "SnapEDA did not return a valid Altium ZIP or library.")?;
        let found = (0..zip.len()).any(|i| {
            zip.by_index(i).is_ok_and(|entry| {
                let name = entry.name().to_ascii_lowercase();
                entry.is_file()
                    && [".schlib", ".pcblib", ".intlib", ".libpkg"]
                        .iter()
                        .any(|e| name.ends_with(e))
            })
        });
        if !found {
            return Err("The SnapEDA ZIP has no Altium library.".into());
        }
        Ok("zip".into())
    }
}

pub fn download(
    mpn: &str,
    manufacturer: &str,
    username: &str,
    password: &str,
    work_dir: &Path,
) -> Result<PathBuf, String> {
    if mpn.trim().is_empty() || username.is_empty() || password.is_empty() {
        return Err("Enter a part number and SnapEDA username and password.".into());
    }
    let mut snap = SnapClient::new()?;
    let (id, url) = file_url(&mut snap, mpn, manufacturer, username, password)?;
    let plain = Client::builder()
        .timeout(Duration::from_secs(60))
        .https_only(true)
        .redirect(reqwest::redirect::Policy::none())
        .user_agent(USER_AGENT)
        .build()
        .map_err(|_| "Could not initialize the download client.")?;
    let file = plain
        .get(url.clone())
        .send()
        .map_err(|_| "Network error downloading the SnapEDA model.")?;
    if !file.status().is_success() {
        return Err(format!(
            "SnapEDA model download failed (HTTP {}).",
            file.status()
        ));
    }
    let bytes = read_limited(file, MAX_FILE)?;
    let extension = file_extension(&bytes, &url)?;
    let root = work_dir.join("downloads");
    std::fs::create_dir_all(&root).map_err(|e| format!("Cannot create download cache: {e}"))?;
    let folder = root.join(format!(
        "snapeda-{}-{}",
        std::process::id(),
        chrono::Utc::now()
            .timestamp_nanos_opt()
            .ok_or("Invalid timestamp")?
    ));
    std::fs::create_dir(&folder).map_err(|e| format!("Cannot create download folder: {e}"))?;
    let path = folder.join(format!("SnapEDA_{id}.{extension}"));
    std::fs::write(&path, bytes).map_err(|e| format!("Cannot save SnapEDA download: {e}"))?;
    Ok(path)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;
    use std::net::TcpListener;

    struct MockSession {
        count: usize,
        csrf: String,
        session: String,
        login_status: u16,
        login_location: &'static str,
        change_session: bool,
        url: &'static str,
    }

    impl Default for MockSession {
        fn default() -> Self {
            Self {
                count: 0,
                csrf: String::new(),
                session: String::new(),
                login_status: 302,
                login_location: "/home/",
                change_session: true,
                url: "https://cdn.example/part.zip",
            }
        }
    }

    impl Session for MockSession {
        fn send(
            &mut self,
            method: reqwest::Method,
            path: &str,
            form: Option<&[(&str, &str)]>,
            extra_csrf: bool,
        ) -> Result<(reqwest::StatusCode, String, Vec<u8>), String> {
            self.count += 1;
            let (status, location, body) = match self.count {
                1 => {
                    assert_eq!((method, path), (reqwest::Method::GET, "/account/login/"));
                    self.csrf = "csrf".into();
                    self.session = "anonymous".into();
                    (
                        200,
                        "",
                        "<input name='csrfmiddlewaretoken' value='Tok123'>".to_owned(),
                    )
                }
                2 => {
                    assert_eq!(
                        (method, path),
                        (reqwest::Method::POST, "/api/v1/search_local_internal")
                    );
                    assert!(extra_csrf);
                    assert_eq!(form.unwrap(), &[("q", "NE555DR")]);
                    (200, "", r#"{"results":[{"unipart_id":2,"part_number":"NE555DR","manufacturer":"TI","has_symbol":true,"has_footprint":true}]}"#.to_owned())
                }
                3 => {
                    assert_eq!(
                        (method, path),
                        (reqwest::Method::GET, "/api/get_part_for_unipart/2")
                    );
                    (
                        200,
                        "",
                        r#"{"download_url":"/parts/snapapi/download-component/1/2/%s"}"#.to_owned(),
                    )
                }
                4 => {
                    assert_eq!(
                        (method, path),
                        (reqwest::Method::POST, "/account/login/?next=/home/")
                    );
                    let form = form.unwrap();
                    assert!(form.contains(&("username", "user")));
                    assert!(form.contains(&("password", "example-secret")));
                    assert!(form.contains(&("csrfmiddlewaretoken", "Tok123")));
                    if self.change_session {
                        self.session = "authenticated".into();
                    }
                    (self.login_status, self.login_location, String::new())
                }
                5 => {
                    assert_eq!(
                        (method, path),
                        (
                            reqwest::Method::GET,
                            "/parts/snapapi/download-component/1/2/altium_native"
                        )
                    );
                    assert_eq!(self.session, "authenticated");
                    (200, "", serde_json::json!({"url": self.url}).to_string())
                }
                _ => panic!("unexpected request"),
            };
            Ok((
                reqwest::StatusCode::from_u16(status).unwrap(),
                location.into(),
                body.into_bytes(),
            ))
        }

        fn csrf(&self) -> &str {
            &self.csrf
        }
        fn session(&self) -> &str {
            &self.session
        }
    }

    #[test]
    fn matching_requires_exact_part_and_manufacturer() {
        let data = serde_json::json!({"results":[
            {"unipart_id":"2","part_number":"NE555DR","manufacturer":"TI","has_symbol":1,"has_footprint":1},
            {"unipart_id":"3","part_number":"NE555DRG4","manufacturer":"TI","has_symbol":1,"has_footprint":1},
            {"unipart_id":"4","part_number":"NE555DR","manufacturer":"Other","has_symbol":1,"has_footprint":1}
        ]});
        assert_eq!(select_part(&data, "ne555dr", "TI").unwrap(), 2);
        assert!(select_part(&data, "NE555DR", "").is_err());
        assert!(select_part(&data, "NE555DR", "missing").is_err());
    }
    #[test]
    fn validates_login_token_and_download_path() {
        assert_eq!(
            form_token("<input value='Tok123' name='csrfmiddlewaretoken' type='hidden'>").unwrap(),
            "Tok123"
        );
        assert!(form_token("<input name='csrfmiddlewaretoken' value='bad;cookie'>").is_err());
        assert_eq!(
            download_path(
                &serde_json::json!({"download_url":"/parts/snapapi/download-component/1/2/%s"})
            )
            .unwrap(),
            "/parts/snapapi/download-component/1/2/altium_native"
        );
        for path in [
            "https://example.com/parts/snapapi/download-component/1/2/%s",
            "/parts/snapapi/download-component/1/2/%s/../../x",
        ] {
            assert!(download_path(&serde_json::json!({"download_url":path})).is_err());
        }
    }

    #[test]
    fn authenticated_workflow_requires_new_session_and_https_file_url() {
        let mut session = MockSession::default();
        let (id, url) = file_url(&mut session, "NE555DR", "TI", "user", "example-secret").unwrap();
        assert_eq!(id, 2);
        assert_eq!(url.as_str(), "https://cdn.example/part.zip");
        assert_eq!(session.count, 5);
        for mut rejected in [
            MockSession {
                login_status: 200,
                ..Default::default()
            },
            MockSession {
                login_location: "/account/login/",
                ..Default::default()
            },
            MockSession {
                change_session: false,
                ..Default::default()
            },
            MockSession {
                url: "http://cdn.example/part.zip",
                ..Default::default()
            },
            MockSession {
                url: "https://user:example-secret@cdn.example/part.zip",
                ..Default::default()
            },
        ] {
            let error =
                file_url(&mut rejected, "NE555DR", "TI", "user", "example-secret").unwrap_err();
            assert!(!error.contains("example-secret"));
        }
    }

    #[test]
    fn accepts_only_recognized_altium_downloads() {
        let url = url::Url::parse("https://cdn.example/part.zip").unwrap();
        let mut writer = zip::ZipWriter::new(Cursor::new(Vec::new()));
        writer
            .start_file("component.SchLib", zip::write::SimpleFileOptions::default())
            .unwrap();
        writer.write_all(b"library").unwrap();
        let zip = writer.finish().unwrap().into_inner();
        assert_eq!(file_extension(&zip, &url).unwrap(), "zip");
        assert!(file_extension(b"not a zip", &url).is_err());
        let ole = [0xd0, 0xcf, 0x11, 0xe0, 0xa1, 0xb1, 0x1a, 0xe1];
        assert!(file_extension(&ole, &url).is_err());
        let intlib = url::Url::parse("https://cdn.example/part.IntLib").unwrap();
        assert_eq!(file_extension(&ole, &intlib).unwrap(), "intlib");
        let mut writer = zip::ZipWriter::new(Cursor::new(Vec::new()));
        writer
            .start_file("readme.txt", zip::write::SimpleFileOptions::default())
            .unwrap();
        let zip = writer.finish().unwrap().into_inner();
        assert!(file_extension(&zip, &url).is_err());
    }

    #[test]
    fn authenticated_http_requests_do_not_follow_redirects() {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let address = listener.local_addr().unwrap();
        let server = std::thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            let mut request = [0; 4096];
            let count = stream.read(&mut request).unwrap();
            let request = String::from_utf8_lossy(&request[..count]).to_ascii_lowercase();
            assert!(request.contains("cookie: csrftoken=csrf; sessionid=session"));
            stream.write_all(b"HTTP/1.1 302 Found\r\nLocation: http://127.0.0.1:1/\r\nContent-Length: 0\r\nConnection: close\r\n\r\n").unwrap();
        });
        let mut client = SnapClient::new().unwrap();
        client.base = format!("http://{address}");
        client.csrf = "csrf".into();
        client.session = "session".into();
        let (status, location, _) = client
            .send(reqwest::Method::GET, "/test", None, false)
            .unwrap();
        assert_eq!(status.as_u16(), 302);
        assert_eq!(location, "http://127.0.0.1:1/");
        server.join().unwrap();
    }
}
