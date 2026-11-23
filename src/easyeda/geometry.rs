use std::f64::consts::PI;
use std::sync::OnceLock;

use regex::Regex;

pub type Point = (f64, f64);

pub fn number(s: &str) -> f64 {
    s.parse::<f64>()
        .ok()
        .filter(|n| n.is_finite())
        .unwrap_or(0.0)
}

pub fn rounded(n: f64) -> String {
    ((n + 0.5).floor() as i64).to_string()
}

pub fn points(s: &str) -> Vec<Point> {
    let values: Vec<_> = s
        .split(|c: char| c == ',' || c.is_whitespace())
        .filter(|s| !s.is_empty())
        .map(number)
        .collect();
    values.chunks_exact(2).map(|p| (p[0], p[1])).collect()
}

pub fn tokens(path: &str) -> Vec<&str> {
    static PATH: OnceLock<Regex> = OnceLock::new();
    PATH.get_or_init(|| Regex::new(r"[MmLlHhVvAaZzCcQqSsTt]|-?\d*\.?\d+(?:[eE][-+]?\d+)?").unwrap())
        .find_iter(path)
        .map(|m| m.as_str())
        .collect()
}

pub fn center(
    start: Point,
    end: Point,
    rx: f64,
    ry: f64,
    large: bool,
    sweep: bool,
) -> Option<(f64, f64, f64)> {
    if rx == 0.0 || ry == 0.0 {
        return None;
    }
    let (dx, dy) = ((start.0 - end.0) / 2.0, (start.1 - end.1) / 2.0);
    let d2 = dx * dx + dy * dy;
    if d2 == 0.0 {
        return None;
    }
    let r = rx.abs().max(d2.sqrt());
    let sign = if large == sweep { -1.0 } else { 1.0 };
    let coef = sign * ((r * r - d2) / d2).max(0.0).sqrt();
    Some((
        coef * dy + (start.0 + end.0) / 2.0,
        -coef * dx + (start.1 + end.1) / 2.0,
        r,
    ))
}

pub fn arc(path: &str) -> Option<(f64, f64, f64, f64, f64)> {
    let t = tokens(path);
    let mi = t
        .iter()
        .position(|v| *v == "M")
        .or_else(|| t.iter().position(|v| *v == "m"))?;
    let ai = t
        .iter()
        .position(|v| *v == "A")
        .or_else(|| t.iter().position(|v| *v == "a"))?;
    let n = |i: usize| t.get(i)?.parse::<f64>().ok().filter(|v| v.is_finite());
    let (sx, sy) = (n(mi + 1)?, n(mi + 2)?);
    let (rx, ry) = (n(ai + 1)?, n(ai + 2)?);
    let (large, sweep) = (n(ai + 4)? != 0.0, n(ai + 5)? != 0.0);
    let (mut ex, mut ey) = (n(ai + 6)?, n(ai + 7)?);
    if t[ai] == "a" {
        ex += sx;
        ey += sy;
    }
    let (cx, cy, r) = center((sx, sy), (ex, ey), rx, ry, large, sweep)?;
    let a1 = (-(sy - cy)).atan2(sx - cx).to_degrees().rem_euclid(360.0);
    let a2 = (-(ey - cy)).atan2(ex - cx).to_degrees().rem_euclid(360.0);
    let (a1, a2) = if sweep { (a2, a1) } else { (a1, a2) };
    Some((cx, cy, r, a1, a2))
}

pub fn polylines(path: &str) -> Vec<(Vec<Point>, bool)> {
    let toks = tokens(path);
    let mut out = Vec::new();
    let mut pts = Vec::new();
    let (mut cx, mut cy) = (0.0, 0.0);
    let mut start = (0.0, 0.0);
    let mut i = 0;
    let mut cmd = ' ';
    while i < toks.len() {
        if toks[i].len() == 1 && toks[i].as_bytes()[0].is_ascii_alphabetic() {
            cmd = toks[i].chars().next().unwrap();
            i += 1;
            if matches!(cmd, 'Z' | 'z') {
                if !pts.is_empty() {
                    out.push((std::mem::take(&mut pts), true));
                }
                (cx, cy) = start;
                cmd = ' ';
                continue;
            }
        }
        let count = match cmd.to_ascii_uppercase() {
            'M' | 'L' | 'T' => 2,
            'H' | 'V' => 1,
            'A' => 7,
            'C' => 6,
            'Q' | 'S' => 4,
            _ => {
                i += 1;
                continue;
            }
        };
        if i + count > toks.len() {
            break;
        }
        let values = toks[i..i + count]
            .iter()
            .map(|s| s.parse::<f64>())
            .collect::<Result<Vec<_>, _>>();
        let Ok(v) = values else {
            break;
        };
        if v.iter().any(|n| !n.is_finite()) {
            break;
        }
        i += count;
        let relative = cmd.is_ascii_lowercase();
        match cmd.to_ascii_uppercase() {
            'M' | 'L' => {
                let (x, y) = (
                    v[0] + if relative { cx } else { 0.0 },
                    v[1] + if relative { cy } else { 0.0 },
                );
                if cmd.eq_ignore_ascii_case(&'M') {
                    if pts.len() > 1 {
                        out.push((std::mem::take(&mut pts), false));
                    }
                    pts.clear();
                    start = (x, y);
                    cmd = if relative { 'l' } else { 'L' };
                }
                pts.push((x, y));
                (cx, cy) = (x, y);
            }
            'H' => {
                cx = v[0] + if relative { cx } else { 0.0 };
                pts.push((cx, cy));
            }
            'V' => {
                cy = v[0] + if relative { cy } else { 0.0 };
                pts.push((cx, cy));
            }
            'A' => {
                let (x, y) = (
                    v[5] + if relative { cx } else { 0.0 },
                    v[6] + if relative { cy } else { 0.0 },
                );
                if let Some((ccx, ccy, r)) =
                    center((cx, cy), (x, y), v[0], v[1], v[3] != 0.0, v[4] != 0.0)
                {
                    let a1 = (cy - ccy).atan2(cx - ccx);
                    let a2 = (y - ccy).atan2(x - ccx);
                    let mut da = a2 - a1;
                    if v[4] != 0.0 {
                        while da <= 0.0 {
                            da += 2.0 * PI;
                        }
                    } else {
                        while da >= 0.0 {
                            da -= 2.0 * PI;
                        }
                    }
                    let n = ((da.abs() / (2.0 * PI) * 36.0) as usize).max(2);
                    for k in 1..=n {
                        let a = a1 + da * k as f64 / n as f64;
                        pts.push((ccx + r * a.cos(), ccy + r * a.sin()));
                    }
                } else {
                    pts.push((x, y));
                }
                (cx, cy) = (x, y);
            }
            _ => {
                let (x, y) = (
                    v[count - 2] + if relative { cx } else { 0.0 },
                    v[count - 1] + if relative { cy } else { 0.0 },
                );
                pts.push((x, y));
                (cx, cy) = (x, y);
            }
        }
    }
    if pts.len() > 1 {
        out.push((pts, false));
    }
    out
}
