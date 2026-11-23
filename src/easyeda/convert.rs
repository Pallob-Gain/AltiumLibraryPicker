// Port of EasyEDA-Importer's converter, used with the owner's permission.
use super::geometry::{self, Point};
use serde_json::Value;
use std::collections::HashSet;

type Records = Vec<Vec<String>>;
const U: f64 = 1000.0;

macro_rules! record {
    ($out:expr, $($field:expr),+ $(,)?) => {
        $out.push(vec![$($field.to_string()),+])
    };
}

fn get<'a>(p: &[&'a str], i: usize) -> &'a str {
    p.get(i).copied().unwrap_or("")
}
fn num(p: &[&str], i: usize) -> f64 {
    geometry::number(get(p, i))
}
fn ci(n: f64) -> String {
    geometry::rounded(n)
}
fn clean(s: &str) -> String {
    s.replace(['\t', '\r', '\n'], " ").trim().to_owned()
}
fn value(v: &Value) -> &str {
    v.as_str().unwrap_or("")
}
fn field<'a>(v: &'a Value, key: &str) -> &'a str {
    value(&v[key])
}
fn dict(v: &Value) -> Value {
    if v.is_object() {
        v.clone()
    } else if let Some(s) = v.as_str() {
        serde_json::from_str::<Value>(s)
            .ok()
            .filter(Value::is_object)
            .unwrap_or(Value::Null)
    } else {
        Value::Null
    }
}
fn visible(s: &str) -> i32 {
    if ["0", "false", "hide", "none"].contains(&s.trim().to_ascii_lowercase().as_str()) {
        0
    } else {
        1
    }
}
fn attr<'a>(p: &[&'a str], i: usize, default: &'a str) -> &'a str {
    p.get(i).copied().unwrap_or(default)
}
fn shapes(ds: &Value) -> impl Iterator<Item = &str> {
    ds["shape"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
        .filter(|s| !s.is_empty())
}
fn pos(v: &Value) -> f64 {
    v.as_f64()
        .filter(|x| x.is_finite())
        .unwrap_or_else(|| geometry::number(value(v)))
}
fn origin(ds: &Value) -> Point {
    let (hx, hy) = (pos(&ds["head"]["x"]), pos(&ds["head"]["y"]));
    for s in shapes(ds) {
        if s.starts_with("P~") {
            let p: Vec<_> = s.split("^^").next().unwrap_or("").split('~').collect();
            let (px, py) = (num(&p, 4), num(&p, 5));
            return (
                px - 10.0 * ((px - hx) / 10.0 + 0.5).floor(),
                py - 10.0 * ((py - hy) / 10.0 + 0.5).floor(),
            );
        }
    }
    (hx, hy)
}

fn poly(
    out: &mut Records,
    kind: &str,
    layer: Option<&str>,
    pts: &[Point],
    closed: bool,
    fill: bool,
    ox: f64,
    oy: f64,
) {
    let mut row = vec![kind.to_owned()];
    if let Some(layer) = layer {
        row.push(layer.to_owned());
    } else {
        row.push((closed as u8).to_string());
        row.push((fill as u8).to_string());
    }
    row.push(pts.len().to_string());
    for &(x, y) in pts {
        row.push(ci((x - ox) * U));
        row.push(ci(-(y - oy) * U));
    }
    out.push(row);
}

fn symbol(ds: &Value, out: &mut Records, org: Option<Point>) {
    let (ox, oy) = org.unwrap_or_else(|| origin(ds));
    let x = |v: f64| ci((v - ox) * U);
    let y = |v: f64| ci(-(v - oy) * U);
    for s in shapes(ds) {
        let p: Vec<_> = s.split('~').collect();
        match p[0] {
            "P" => {
                let seg: Vec<Vec<&str>> = s.split("^^").map(|v| v.split('~').collect()).collect();
                let st = &seg[0];
                let (hx, hy) = (num(st, 4), num(st, 5));
                let elec = match get(st, 2) {
                    "1" => 0,
                    "2" => 2,
                    "3" => 1,
                    "4" => 7,
                    _ => 4,
                };
                let lines =
                    geometry::polylines(seg.get(2).and_then(|v| v.first()).copied().unwrap_or(""));
                let (dx, dy, mut length) =
                    if let Some((pts, _)) = lines.first().filter(|(p, _)| p.len() >= 2) {
                        let a = pts[0];
                        let b = pts[pts.len() - 1];
                        let (dx, dy) = if (a.0 - hx).hypot(a.1 - hy) <= (b.0 - hx).hypot(b.1 - hy) {
                            (b.0 - a.0, b.1 - a.1)
                        } else {
                            (a.0 - b.0, a.1 - b.1)
                        };
                        (dx, dy, dx.hypot(dy))
                    } else {
                        let (dx, dy) = match (num(st, 6) as i32).rem_euclid(360) {
                            0 => (-1.0, 0.0),
                            90 => (0.0, 1.0),
                            180 => (1.0, 0.0),
                            270 => (0.0, -1.0),
                            _ => (1.0, 0.0),
                        };
                        (dx, dy, 10.0)
                    };
                let orient = if dx.abs() >= dy.abs() {
                    if dx > 0.0 {
                        180
                    } else {
                        0
                    }
                } else if dy > 0.0 {
                    90
                } else {
                    270
                };
                if length <= 0.0 {
                    length = 10.0;
                }
                let (bx, by) = match orient {
                    0 => (hx - length, hy),
                    180 => (hx + length, hy),
                    90 => (hx, hy + length),
                    _ => (hx, hy - length),
                };
                let name = seg.get(3).map_or("", |v| get(v, 4));
                let des = seg.get(4).map_or("", |v| get(v, 4));
                let flag = |n: usize, fallback: i32| {
                    seg.get(n).map_or(fallback, |v| {
                        visible(attr(v, 0, if fallback == 1 { "1" } else { "0" }))
                    })
                };
                record!(
                    out,
                    "SPIN",
                    x(bx),
                    y(by),
                    ci(length * U),
                    orient,
                    elec,
                    flag(3, 1),
                    flag(4, 1),
                    flag(5, 0),
                    flag(6, 0),
                    clean(if des.is_empty() { get(st, 3) } else { des }),
                    clean(name)
                );
            }
            "R" => {
                let (px, py, rx, ry, w, h) = (
                    num(&p, 1),
                    num(&p, 2),
                    num(&p, 3),
                    num(&p, 4),
                    num(&p, 5),
                    num(&p, 6),
                );
                let fill =
                    !["none", ""].contains(&attr(&p, 10, "none").to_ascii_lowercase().as_str());
                record!(
                    out,
                    "SRECT",
                    x(px),
                    y(py + h),
                    x(px + w),
                    y(py),
                    fill as u8,
                    ci(rx * U),
                    ci(ry * U)
                );
            }
            "E" | "C" => {
                let (cx, cy, rx) = (num(&p, 1), num(&p, 2), num(&p, 3));
                let (ry, fill_index) = if p[0] == "C" {
                    (rx, 7)
                } else {
                    (num(&p, 4), 8)
                };
                let fill = !["none", ""]
                    .contains(&attr(&p, fill_index, "none").to_ascii_lowercase().as_str());
                record!(
                    out,
                    "SELLIPSE",
                    x(cx),
                    y(cy),
                    ci(rx * U),
                    ci(ry * U),
                    fill as u8
                );
            }
            "A" => {
                if let Some((cx, cy, r, a1, a2)) = geometry::arc(get(&p, 1)) {
                    record!(
                        out,
                        "SARC",
                        x(cx),
                        y(cy),
                        ci(r * U),
                        ci(a1 * 1000.0),
                        ci(a2 * 1000.0)
                    );
                }
            }
            "PL" | "PG" => {
                let pts = geometry::points(get(&p, 1));
                let closed = p[0] == "PG";
                let fill = closed
                    && !["none", ""].contains(&attr(&p, 5, "none").to_ascii_lowercase().as_str());
                if pts.len() >= 2 {
                    poly(out, "SPOLY", None, &pts, closed, fill, ox, oy);
                }
            }
            "PT" => {
                let fill =
                    !["none", ""].contains(&attr(&p, 5, "none").to_ascii_lowercase().as_str());
                for (pts, closed) in geometry::polylines(get(&p, 1)) {
                    if pts.len() >= 2 {
                        poly(out, "SPOLY", None, &pts, closed, fill && closed, ox, oy);
                    }
                }
            }
            "T" => {
                let text = get(&p, 12);
                if !text.is_empty() && visible(attr(&p, 13, "1")) != 0 {
                    let size: String = get(&p, 7)
                        .chars()
                        .filter(|c| c.is_ascii_digit() || *c == '.')
                        .collect();
                    let size = if size.is_empty() {
                        7.0
                    } else {
                        size.parse::<f64>().unwrap_or(7.0)
                    };
                    record!(
                        out,
                        "STEXT",
                        x(num(&p, 2)),
                        y(num(&p, 3)),
                        ci((360.0 - num(&p, 4)).rem_euclid(360.0) * 1000.0),
                        ci(size),
                        clean(text)
                    );
                }
            }
            _ => {}
        }
    }
}

fn layer(s: &str) -> Option<&'static str> {
    match s {
        "1" => Some("TOP"),
        "2" => Some("BOTTOM"),
        "3" => Some("TOPOVERLAY"),
        "4" => Some("BOTTOMOVERLAY"),
        "5" => Some("TOPPASTE"),
        "6" => Some("BOTTOMPASTE"),
        "7" => Some("TOPSOLDER"),
        "8" => Some("BOTTOMSOLDER"),
        "10" => Some("MECH1"),
        "11" => Some("MULTI"),
        "12" | "15" => Some("MECH15"),
        "13" => Some("MECH13"),
        "14" => Some("MECH14"),
        "99" => Some("MECH9"),
        "100" => Some("MECH10"),
        "101" => Some("MECH11"),
        _ => None,
    }
}

fn footprint(ds: &Value, out: &mut Records) {
    let canvas: Vec<_> = field(ds, "canvas").split('~').collect();
    let (ox, oy) = if canvas.len() > 17 && !canvas[16].is_empty() && !canvas[17].is_empty() {
        (num(&canvas, 16), num(&canvas, 17))
    } else {
        (pos(&ds["head"]["x"]), pos(&ds["head"]["y"]))
    };
    let x = |v: f64| ci((v - ox) * U);
    let y = |v: f64| ci(-(v - oy) * U);
    if shapes(ds).next().is_none() {
        record!(
            out,
            "WARN",
            "EasyEDA returned no footprint shapes for this part"
        );
    }
    for s in shapes(ds) {
        let p: Vec<_> = s.split('~').collect();
        match p[0] {
            "PAD" => {
                let (mut cx, mut cy, mut w, mut h) =
                    (num(&p, 2), num(&p, 3), num(&p, 4), num(&p, 5));
                let (shape, number, hole, rot, hole_len) = (
                    get(&p, 1),
                    get(&p, 8),
                    num(&p, 9),
                    num(&p, 11).rem_euclid(360.0),
                    num(&p, 13),
                );
                let mut lay = match get(&p, 6) {
                    "1" => "TOP",
                    "2" => "BOTTOM",
                    _ => "MULTI",
                };
                if hole > 0.0 {
                    lay = "MULTI";
                }
                let pts = geometry::points(get(&p, 10));
                let ashape = if shape == "RECT" {
                    "RECT"
                } else if shape == "POLYGON" && pts.len() >= 3 {
                    let a = rot.to_radians();
                    let local: Vec<_> = pts
                        .iter()
                        .map(|&(px, py)| {
                            let (dx, dy) = (px - cx, -(py - cy));
                            (
                                dx * (-a).cos() - dy * (-a).sin(),
                                dx * (-a).sin() + dy * (-a).cos(),
                            )
                        })
                        .collect();
                    let (xmin, xmax, ymin, ymax) = local.iter().fold(
                        (
                            f64::INFINITY,
                            f64::NEG_INFINITY,
                            f64::INFINITY,
                            f64::NEG_INFINITY,
                        ),
                        |(xmin, xmax, ymin, ymax), &(x, y)| {
                            (xmin.min(x), xmax.max(x), ymin.min(y), ymax.max(y))
                        },
                    );
                    w = xmax - xmin;
                    h = ymax - ymin;
                    let (mx, my) = ((xmax + xmin) / 2.0, (ymax + ymin) / 2.0);
                    cx += mx * a.cos() - my * a.sin();
                    cy -= mx * a.sin() + my * a.cos();
                    record!(
                        out,
                        "WARN",
                        format!(
                            "Pad {} is a custom polygon; approximated by a {} x {} mil rectangle",
                            clean(number),
                            ci(w * 10.0),
                            ci(h * 10.0)
                        )
                    );
                    "RECT"
                } else {
                    "ROUND"
                };
                let hole_d = hole * 2.0;
                let slot = if hole > 0.0 && hole_len > 0.0 && hole_len > hole_d + 1e-6 {
                    hole_len
                } else {
                    0.0
                };
                let hole_rot = if slot > 0.0 && h > w { 90000 } else { 0 };
                let plated = !attr(&p, 15, "Y")
                    .trim()
                    .to_ascii_uppercase()
                    .starts_with('N');
                record!(
                    out,
                    "FPAD",
                    clean(number),
                    ashape,
                    x(cx),
                    y(cy),
                    ci(w * U),
                    ci(h * U),
                    ci(rot * 1000.0),
                    lay,
                    ci(hole_d * U),
                    ci(slot * U),
                    hole_rot,
                    plated as u8
                );
            }
            "TRACK" => {
                if let Some(lay) = layer(get(&p, 2)) {
                    for pair in geometry::points(get(&p, 4)).windows(2) {
                        record!(
                            out,
                            "FTRACK",
                            lay,
                            x(pair[0].0),
                            y(pair[0].1),
                            x(pair[1].0),
                            y(pair[1].1),
                            ci(num(&p, 1) * U)
                        );
                    }
                }
            }
            "RECT" => {
                if let Some(lay) = layer(get(&p, 5)) {
                    let (cx, cy, w, h) = (num(&p, 1), num(&p, 2), num(&p, 3), num(&p, 4));
                    let sw = if num(&p, 8) == 0.0 { 0.6 } else { num(&p, 8) };
                    let c = [
                        (cx, cy),
                        (cx + w, cy),
                        (cx + w, cy + h),
                        (cx, cy + h),
                        (cx, cy),
                    ];
                    for pair in c.windows(2) {
                        record!(
                            out,
                            "FTRACK",
                            lay,
                            x(pair[0].0),
                            y(pair[0].1),
                            x(pair[1].0),
                            y(pair[1].1),
                            ci(sw * U)
                        );
                    }
                }
            }
            "CIRCLE" => {
                if let Some(lay) = layer(get(&p, 5)) {
                    let r = num(&p, 3);
                    if r > 0.0 {
                        record!(
                            out,
                            "FARC",
                            lay,
                            x(num(&p, 1)),
                            y(num(&p, 2)),
                            ci(r * U),
                            "0",
                            "360000",
                            ci(num(&p, 4) * U)
                        );
                    }
                }
            }
            "ARC" => {
                if let (Some(lay), Some((cx, cy, r, a1, a2))) =
                    (layer(get(&p, 2)), geometry::arc(get(&p, 4)))
                {
                    record!(
                        out,
                        "FARC",
                        lay,
                        x(cx),
                        y(cy),
                        ci(r * U),
                        ci(a1 * 1000.0),
                        ci(a2 * 1000.0),
                        ci(num(&p, 1) * U)
                    );
                }
            }
            "HOLE" => record!(
                out,
                "FHOLE",
                x(num(&p, 1)),
                y(num(&p, 2)),
                ci(2.0 * num(&p, 3) * U)
            ),
            "VIA" => record!(
                out,
                "FVIA",
                x(num(&p, 1)),
                y(num(&p, 2)),
                ci(num(&p, 3) * U),
                ci(2.0 * num(&p, 5) * U)
            ),
            "SOLIDREGION" => {
                if ["1", "2", "3", "4"].contains(&get(&p, 1)) && ["solid", ""].contains(&get(&p, 4))
                {
                    if let Some(lay) = layer(get(&p, 1)) {
                        for (pts, _) in geometry::polylines(get(&p, 3)) {
                            if pts.len() >= 3 {
                                poly(out, "FREGION", Some(lay), &pts, true, true, ox, oy);
                            }
                        }
                    }
                }
            }
            "SVGNODE" => {
                let Some(json) = s.strip_prefix("SVGNODE~") else {
                    continue;
                };
                let Ok(node) = serde_json::from_str::<Value>(json) else {
                    continue;
                };
                let attrs = &node["attrs"];
                let uuid = field(attrs, "uuid");
                if field(attrs, "c_etype") != "outline3D"
                    || uuid.is_empty()
                    || out.iter().any(|r| r[0] == "MODEL")
                {
                    continue;
                }
                let co: Vec<_> = if field(attrs, "c_origin").is_empty() {
                    "0,0"
                } else {
                    field(attrs, "c_origin")
                }
                .split(',')
                .collect();
                let (mut tx, mut ty, tz) = (
                    (num(&co, 0) - ox) * 0.254,
                    -(num(&co, 1) - oy) * 0.254,
                    pos(&attrs["z"]) * 0.254,
                );
                let pts: Vec<_> = node["childNodes"]
                    .as_array()
                    .into_iter()
                    .flatten()
                    .flat_map(|ch| geometry::points(field(&ch["attrs"], "points")))
                    .collect();
                if !pts.is_empty() {
                    let (xmin, xmax, ymin, ymax) = pts.iter().fold(
                        (
                            f64::INFINITY,
                            f64::NEG_INFINITY,
                            f64::INFINITY,
                            f64::NEG_INFINITY,
                        ),
                        |(xmin, xmax, ymin, ymax), &(px, py)| {
                            let (px, py) = ((px - ox) * 0.254, -(py - oy) * 0.254);
                            (xmin.min(px), xmax.max(px), ymin.min(py), ymax.max(py))
                        },
                    );
                    let (cx, cy) = ((xmin + xmax) / 2.0, (ymin + ymax) / 2.0);
                    if (cx - tx).abs() > 0.1 || (cy - ty).abs() > 0.1 {
                        (tx, ty) = (cx, cy);
                    }
                }
                let rot: Vec<_> = if field(attrs, "c_rotation").is_empty() {
                    "0,0,0"
                } else {
                    field(attrs, "c_rotation")
                }
                .split(',')
                .collect();
                record!(
                    out,
                    "MODEL",
                    clean(uuid),
                    clean(field(attrs, "title")),
                    format!("{tx:.4}"),
                    format!("{ty:.4}"),
                    format!("{tz:.4}"),
                    format!("{:.3}", num(&rot, 0)),
                    format!("{:.3}", num(&rot, 1)),
                    format!("{:.3}", num(&rot, 2))
                );
            }
            _ => {}
        }
    }
}

fn key(s: &str) -> String {
    s.to_ascii_uppercase()
        .chars()
        .filter(|c| c.is_ascii_uppercase() || c.is_ascii_digit())
        .collect()
}
fn add_param(out: &mut Records, seen: &mut HashSet<String>, k: &str, v: &str) {
    let norm = key(k);
    if norm.is_empty() || !seen.insert(norm) {
        return;
    }
    let v = clean(v);
    if !v.is_empty() && v != "null" && v != "-" {
        record!(out, "PARAM", clean(k), v);
    }
}
fn first<'a>(items: &[&'a str]) -> &'a str {
    items.iter().copied().find(|s| !s.is_empty()).unwrap_or("")
}

pub fn convert(result: &Value, lcsc: &str) -> Records {
    let mut out = Vec::new();
    let ds = dict(&result["dataStr"]);
    let cp = &ds["head"]["c_para"];
    let pkg = dict(&result["packageDetail"]);
    let pds = dict(&pkg["dataStr"]);
    let pc = &pds["head"]["c_para"];
    let lcsc = first(&[
        lcsc,
        field(&result["lcsc"], "number"),
        field(cp, "Supplier Part"),
    ]);
    let name = first(&[field(cp, "name"), field(result, "title"), lcsc]);
    let fallback = format!("{name}_FP");
    let fp = first(&[field(pc, "package"), field(&pkg, "title"), &fallback]);
    let prefix = first(&[field(cp, "pre"), "U?"]).replace('?', "");
    let mfr = field(cp, "Manufacturer");
    let mpn = first(&[field(cp, "Manufacturer Part"), name]);
    let desc_fallback = format!("{mfr} {mpn}").trim().to_owned();
    let desc = first(&[field(result, "description"), &desc_fallback]);
    record!(out, "META", "NAME", clean(name));
    record!(out, "META", "LCSC", clean(lcsc));
    record!(out, "META", "FOOTPRINT", clean(fp));
    record!(
        out,
        "META",
        "PREFIX",
        clean(if prefix.is_empty() { "U" } else { &prefix })
    );
    record!(out, "META", "DESC", clean(desc));
    let mut seen = HashSet::new();
    for (k, v) in [
        ("Manufacturer", mfr),
        ("Manufacturer Part", mpn),
        ("Supplier", first(&[field(cp, "Supplier"), "LCSC"])),
        ("Supplier Part", lcsc),
        ("Package", fp),
        ("Datasheet", field(&result["lcsc"], "url")),
        ("JLCPCB Part Class", field(cp, "JLCPCB Part Class")),
        ("Value", field(cp, "Value")),
    ] {
        add_param(&mut out, &mut seen, k, v);
    }
    if let Some(cp) = cp.as_object() {
        for (k, v) in cp {
            if let Some(v) = v.as_str() {
                if ![
                    "PRE",
                    "NAME",
                    "NAMEALIAS",
                    "PACKAGE",
                    "CONTRIBUTOR",
                    "SPICEPRE",
                    "SPICESYMBOLNAME",
                    "TIMESTAMP",
                    "LINK",
                ]
                .contains(&key(k).as_str())
                {
                    add_param(&mut out, &mut seen, k, v);
                }
            }
        }
    }
    let subparts: Vec<_> = result["subparts"]
        .as_array()
        .into_iter()
        .flatten()
        .map(|v| dict(&v["dataStr"]))
        .filter(|v| v["shape"].as_array().is_some_and(|s| !s.is_empty()))
        .collect();
    if subparts.is_empty() {
        symbol(&ds, &mut out, None);
    } else {
        let org = origin(&subparts[0]);
        for (i, sp) in subparts.iter().enumerate() {
            record!(out, "SPART", i + 1);
            symbol(sp, &mut out, Some(org));
        }
    }
    if !out.iter().any(|r| r[0] == "SPIN") {
        record!(out, "WARN", "EasyEDA returned no symbol pins for this part");
    }
    footprint(&pds, &mut out);
    out
}

pub fn unsupported_warnings(result: &Value) -> Vec<String> {
    let ds = dict(&result["dataStr"]);
    let pkg = dict(&result["packageDetail"]);
    let pds = dict(&pkg["dataStr"]);
    let subparts: Vec<_> = result["subparts"]
        .as_array()
        .into_iter()
        .flatten()
        .map(|v| dict(&v["dataStr"]))
        .filter(|v| shapes(v).next().is_some())
        .collect();
    let mut symbol_unknown = HashSet::new();
    let mut approximate_paths = 0;
    let mut invalid_arcs = 0;
    for data in if subparts.is_empty() {
        vec![ds]
    } else {
        subparts
    } {
        for s in shapes(&data) {
            let p: Vec<_> = s.split('~').collect();
            let kind = p[0];
            if !["P", "R", "E", "C", "A", "PL", "PG", "PT", "T"].contains(&kind) {
                symbol_unknown.insert(kind.to_owned());
            }
            if kind == "A" && geometry::arc(get(&p, 1)).is_none() {
                invalid_arcs += 1;
            }
            if kind == "PT"
                && geometry::tokens(get(&p, 1))
                    .iter()
                    .any(|token| ["C", "c", "Q", "q", "S", "s", "T", "t"].contains(token))
            {
                approximate_paths += 1;
            }
        }
    }
    let mut footprint_unknown = HashSet::new();
    let mut skipped_regions = 0;
    let mut skipped_layers = 0;
    let mut unsupported_pads = 0;
    let mut invalid_models = 0;
    for s in shapes(&pds) {
        let p: Vec<_> = s.split('~').collect();
        if p[0] == "ARC" && layer(get(&p, 2)).is_some() && geometry::arc(get(&p, 4)).is_none() {
            invalid_arcs += 1;
        }
        if p[0] == "SOLIDREGION"
            && ["1", "2", "3", "4"].contains(&get(&p, 1))
            && geometry::tokens(get(&p, 3))
                .iter()
                .any(|token| ["C", "c", "Q", "q", "S", "s", "T", "t"].contains(token))
        {
            approximate_paths += 1;
        }
        if p[0] == "SOLIDREGION"
            && (!["1", "2", "3", "4"].contains(&get(&p, 1)) || !["solid", ""].contains(&get(&p, 4)))
        {
            skipped_regions += 1;
        } else if matches!(p[0], "TRACK" | "ARC") && layer(get(&p, 2)).is_none()
            || matches!(p[0], "RECT" | "CIRCLE") && layer(get(&p, 5)).is_none()
        {
            skipped_layers += 1;
        } else if p[0] == "PAD"
            && (!["RECT", "OVAL", "ELLIPSE", "POLYGON"].contains(&get(&p, 1))
                || get(&p, 1) == "POLYGON" && geometry::points(get(&p, 10)).len() < 3)
        {
            unsupported_pads += 1;
        } else if p[0] == "SVGNODE"
            && s.strip_prefix("SVGNODE~")
                .and_then(|json| serde_json::from_str::<Value>(json).ok())
                .is_none()
        {
            invalid_models += 1;
        } else if ![
            "PAD",
            "TRACK",
            "RECT",
            "CIRCLE",
            "ARC",
            "HOLE",
            "VIA",
            "SOLIDREGION",
            "SVGNODE",
        ]
        .contains(&p[0])
        {
            footprint_unknown.insert(p[0].to_owned());
        }
    }
    let mut warnings = Vec::new();
    if skipped_regions > 0 {
        warnings.push(format!("{skipped_regions} EasyEDA footprint regions on unsupported layers or with unsupported fill types were omitted; inspect the footprint."));
    }
    if skipped_layers > 0 {
        warnings.push(format!(
            "{skipped_layers} EasyEDA footprint shapes on unsupported layers were omitted."
        ));
    }
    if unsupported_pads > 0 {
        warnings.push(format!("{unsupported_pads} EasyEDA pads had unsupported shapes and were approximated as round."));
    }
    if invalid_models > 0 {
        warnings.push(format!(
            "{invalid_models} EasyEDA SVG/model records could not be decoded and were omitted."
        ));
    }
    if invalid_arcs > 0 {
        warnings.push(format!(
            "{invalid_arcs} EasyEDA arcs could not be converted and were omitted."
        ));
    }
    if approximate_paths > 0 {
        warnings.push(format!(
            "{approximate_paths} EasyEDA paths contain curves approximated by straight segments."
        ));
    }
    for (label, kinds) in [("symbol", symbol_unknown), ("footprint", footprint_unknown)] {
        if !kinds.is_empty() {
            let mut kinds: Vec<_> = kinds.into_iter().collect();
            kinds.sort();
            warnings.push(format!(
                "Unsupported EasyEDA {label} shapes were omitted: {}.",
                kinds.join(", ")
            ));
        }
    }
    warnings
}

pub fn obj_bbox(text: &str) -> Option<[f64; 5]> {
    let mut bounds = [
        f64::INFINITY,
        f64::NEG_INFINITY,
        f64::INFINITY,
        f64::NEG_INFINITY,
        f64::INFINITY,
    ];
    let mut found = false;
    for line in text.lines().filter(|line| line.starts_with("v ")) {
        let p: Vec<_> = line.split_whitespace().collect();
        if p.len() >= 4 {
            let (x, y, z) = (num(&p, 1), num(&p, 2), num(&p, 3));
            bounds = [
                bounds[0].min(x),
                bounds[1].max(x),
                bounds[2].min(y),
                bounds[3].max(y),
                bounds[4].min(z),
            ];
            found = true;
        }
    }
    found.then_some(bounds)
}

pub fn finalize_model(lines: Records, bbox: Option<[f64; 5]>) -> Records {
    let mut out = Vec::new();
    let mut models = Vec::new();
    for line in lines {
        if line[0] == "MODEL" {
            models.push(line);
        } else {
            out.push(line);
        }
    }
    for r in models {
        let n = |i: usize| geometry::number(&r[i]);
        let (tx, ty, tz, rx, ry, rz) = (n(3), n(4), n(5), n(6), n(7), n(8));
        let (ocx, ocy, zmin) = bbox.map_or((0.0, 0.0, 0.0), |b| {
            ((b[0] + b[1]) / 2.0, (b[2] + b[3]) / 2.0, b[4])
        });
        let a = rz.to_radians();
        let mm2c = 100000.0 / 25.4;
        record!(
            out,
            "F3D",
            format!("{}.step", r[1]),
            ci((tx - ocx * a.cos() + ocy * a.sin()) * mm2c),
            ci((ty - ocx * a.sin() - ocy * a.cos()) * mm2c),
            ci((tz - zmin) * mm2c),
            ci(rx * 1000.0),
            ci(ry * 1000.0),
            ci(rz * 1000.0)
        );
    }
    out
}
