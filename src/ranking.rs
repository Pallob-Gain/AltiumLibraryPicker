use std::collections::BTreeSet;

use crate::models::{Availability, Match, SearchResult};

pub fn normalize_mpn(mpn: &str) -> String {
    mpn.chars()
        .filter(|c| !c.is_whitespace())
        .flat_map(char::to_uppercase)
        .collect()
}

pub fn classify(query: &str, mpn: &str) -> Match {
    if normalize_mpn(query) == normalize_mpn(mpn) {
        Match::Exact
    } else {
        Match::Similar
    }
}

pub fn rank(query: &str, mut results: Vec<SearchResult>) -> Vec<SearchResult> {
    for result in &mut results {
        result.match_status = classify(query, &result.mpn);
        let mut score = if result.match_status == Match::Exact {
            1000
        } else {
            0
        };
        score += match result.source_kind.as_str() {
            "api" => 30,
            "package" => 20,
            _ => 0,
        };
        if result.altium_native == Availability::Yes {
            score += 20;
        }
        for availability in [&result.symbol, &result.footprint, &result.model3d] {
            if *availability == Availability::Yes {
                score += 10;
            }
        }
        if result.download == Availability::Yes {
            score += 5;
        }
        if !result.datasheet_url.is_empty() {
            score += 5;
        }
        result.score = score;
    }
    results.sort_by(|a, b| {
        b.score
            .cmp(&a.score)
            .then_with(|| a.source.cmp(&b.source))
            .then_with(|| a.mpn.cmp(&b.mpn))
    });
    results
}

pub fn warnings(query: &str, ranked: &[SearchResult]) -> Vec<String> {
    let mut out = Vec::new();
    let exact: Vec<_> = ranked
        .iter()
        .filter(|r| r.match_status == Match::Exact)
        .collect();
    let makers: BTreeSet<String> = exact
        .iter()
        .filter(|r| !r.manufacturer.trim().is_empty())
        .map(|r| r.manufacturer.trim().to_owned())
        .collect();
    if makers.len() > 1 {
        out.push(format!("Manufacturer ambiguous: the exact MPN is listed by {}. Verify the manufacturer before importing.", makers.into_iter().collect::<Vec<_>>().join(", ")));
    }
    let variants: BTreeSet<String> = ranked
        .iter()
        .filter(|r| r.match_status == Match::Similar)
        .map(|r| r.mpn.clone())
        .collect();
    if !variants.is_empty() {
        let mut shown: Vec<_> = variants.iter().take(8).cloned().collect();
        if variants.len() > 8 {
            shown.push("...".to_owned());
        }
        out.push(format!("{} similar part number(s) found ({}). These are different parts or package/packaging variants and must not be substituted.", variants.len(), shown.join(", ")));
    }
    let _ = query;
    out
}
