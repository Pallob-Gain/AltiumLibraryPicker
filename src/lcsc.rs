use std::time::Duration;

use reqwest::blocking::Client;
use serde::Deserialize;
use serde_json::json;

const SEARCH_URL: &str =
    "https://jlcpcb.com/api/overseas-pcb-order/v1/shoppingCart/smtGood/selectSmtComponentList";

#[derive(Clone, Debug, thiserror::Error)]
pub enum LcscError {
    #[error("{0}")]
    Message(String),
}

#[derive(Clone, Debug)]
pub struct LcscPart {
    pub code: String,
    pub mpn: String,
    pub manufacturer: String,
    pub package: String,
    pub stock: i64,
    pub description: String,
    pub datasheet_url: String,
    pub url: String,
}

#[derive(Deserialize)]
struct Reply {
    code: i32,
    message: Option<String>,
    data: Option<ReplyData>,
}

#[derive(Deserialize)]
struct ReplyData {
    #[serde(rename = "componentPageInfo")]
    component_page_info: Option<ComponentPage>,
}

#[derive(Deserialize)]
struct ComponentPage {
    list: Option<Vec<serde_json::Value>>,
}

fn https(value: Option<&serde_json::Value>) -> String {
    let value = value.and_then(|v| v.as_str()).unwrap_or_default();
    if value.starts_with("https://") && !value.contains(' ') {
        value.to_owned()
    } else {
        String::new()
    }
}

fn clean_brand(name: &str) -> String {
    if let Some(open) = name.find('(') {
        if name.ends_with(')') && open > 0 {
            let inner = &name[open + 1..name.len() - 1];
            if !inner.is_empty() {
                return inner.trim().to_owned();
            }
        }
    }
    name.trim().to_owned()
}

pub fn search(mpn: &str) -> Result<Vec<LcscPart>, LcscError> {
    let client = Client::builder()
        .timeout(Duration::from_secs(15))
        .build()
        .map_err(|e| LcscError::Message(format!("Could not build HTTP client: {e}")))?;
    let response = client
        .post(SEARCH_URL)
        .header("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64)")
        .header("Content-Type", "application/json")
        .json(&json!({"keyword": mpn.trim(), "currentPage": 1, "pageSize": 100}))
        .send()
        .map_err(|e| LcscError::Message(format!("LCSC parts search is not reachable ({e}).")))?;
    if !response.status().is_success() {
        return Err(LcscError::Message(format!(
            "LCSC parts search returned HTTP {}.",
            response.status()
        )));
    }
    let reply: Reply = response.json().map_err(|_| {
        LcscError::Message("LCSC parts search sent an unexpected reply.".to_owned())
    })?;
    if reply.code != 200 {
        return Err(LcscError::Message(format!(
            "LCSC parts search answered: {}",
            reply.message.unwrap_or_else(|| reply.code.to_string())
        )));
    }
    let mut parts = Vec::new();
    if let Some(data) = reply.data {
        if let Some(page) = data.component_page_info {
            if let Some(list) = page.list {
                for item in list {
                    let code = item
                        .get("componentCode")
                        .and_then(|v| v.as_str())
                        .unwrap_or_default()
                        .trim();
                    let model = item
                        .get("componentModelEn")
                        .and_then(|v| v.as_str())
                        .unwrap_or_default()
                        .trim();
                    if code.is_empty() || !code.starts_with('C') || model.is_empty() {
                        continue;
                    }
                    let stock = item
                        .get("stockCount")
                        .and_then(|v| v.as_i64())
                        .unwrap_or_default();
                    let spec = item
                        .get("componentSpecificationEn")
                        .and_then(|v| v.as_str())
                        .unwrap_or_default();
                    parts.push(LcscPart {
                        code: code.to_owned(),
                        mpn: model.to_owned(),
                        manufacturer: clean_brand(
                            item.get("componentBrandEn")
                                .and_then(|v| v.as_str())
                                .unwrap_or_default(),
                        ),
                        package: if spec == "-" {
                            String::new()
                        } else {
                            spec.trim().to_owned()
                        },
                        stock,
                        description: item
                            .get("describe")
                            .and_then(|v| v.as_str())
                            .unwrap_or_default()
                            .to_owned(),
                        datasheet_url: https(
                            item.get("dataManualOfficialLink")
                                .or_else(|| item.get("dataManualUrl")),
                        ),
                        url: https(item.get("lcscGoodsUrl")),
                    });
                }
            }
        }
    }
    Ok(parts)
}
