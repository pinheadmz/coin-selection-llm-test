//! P1.1 starter wallet (Rust).
//!
//! Speaks the harness protocol on stdin/stdout and implements naive
//! largest-first coin selection. It passes every sanity check and scores ~100
//! (the baseline). Your job: make `select` smarter. Debug output goes to
//! stderr (`eprintln!`), never stdout. No external crates are required.

use std::collections::{BTreeSet, HashMap};
use std::io::{self, BufRead, Write};

const INPUT_VB: u64 = 68;
const OUTPUT_VB: u64 = 31;
const OVERHEAD_VB: u64 = 11;
const MAX_INPUTS: usize = 100;

struct Utxo {
    value: u64,
    #[allow(dead_code)]
    labels: BTreeSet<String>,
}

struct Wallet {
    #[allow(dead_code)] // you will probably want this for consolidation decisions
    long_term_fee_rate: u64,
    dust_limit: u64,
    utxos: HashMap<String, Utxo>,
    /// payment id -> outpoints we proposed to spend
    pending: HashMap<String, Vec<String>>,
}

impl Wallet {
    fn new() -> Self {
        Wallet {
            long_term_fee_rate: 10,
            dust_limit: 294,
            utxos: HashMap::new(),
            pending: HashMap::new(),
        }
    }

    fn add_utxo(&mut self, outpoint: &str, value: u64, labels: &str) {
        let labels = labels.split(',').map(str::to_string).collect();
        self.utxos.insert(outpoint.to_string(), Utxo { value, labels });
    }

    fn on_result(&mut self, payment_id: &str, status: &str) {
        let inputs = self.pending.remove(payment_id).unwrap_or_default();
        match status {
            "ACCEPTED" => {
                for op in &inputs {
                    self.utxos.remove(op);
                }
                // TODO: remember which labels these inputs just linked together.
            }
            "REJECTED" => eprintln!("payment {payment_id} was rejected!"),
            _ => {}
        }
    }

    /// Returns (inputs, change) or None to skip. `priority` is one of
    /// "privacy", "balanced", "min_cost" -- this naive version ignores it.
    fn select(&self, amount: u64, fee_rate: u64, _priority: &str) -> Option<(Vec<String>, u64)> {
        let mut coins: Vec<(&String, &Utxo)> = self.utxos.iter().collect();
        // Largest first; tie-break on outpoint so runs are deterministic.
        coins.sort_by(|a, b| b.1.value.cmp(&a.1.value).then(a.0.cmp(b.0)));

        let mut chosen = Vec::new();
        let mut total = 0u64;
        let mut funded = false;
        for (op, utxo) in coins {
            chosen.push(op.clone());
            total += utxo.value;
            let n = chosen.len() as u64;
            let fee_no_change = (OVERHEAD_VB + INPUT_VB * n + OUTPUT_VB) * fee_rate;
            if total >= amount + fee_no_change {
                funded = true;
                break;
            }
        }
        if !funded || chosen.len() > MAX_INPUTS {
            return None;
        }
        let n = chosen.len() as u64;
        let fee_with_change = (OVERHEAD_VB + INPUT_VB * n + 2 * OUTPUT_VB) * fee_rate;
        // Careful: u64 subtraction must not underflow.
        let change = total.saturating_sub(amount + fee_with_change);
        let change = if change < self.dust_limit { 0 } else { change };
        Some((chosen, change))
    }
}

fn main() {
    let stdin = io::stdin();
    let stdout = io::stdout();
    let mut out = stdout.lock();
    let mut w = Wallet::new();

    for line in stdin.lock().lines() {
        let line = line.expect("failed to read stdin");
        let parts: Vec<&str> = line.split_whitespace().collect();
        if parts.is_empty() {
            continue;
        }
        match parts[0] {
            "PARAMS" => {
                w.long_term_fee_rate = parts[1].parse().unwrap();
                w.dust_limit = parts[2].parse().unwrap();
            }
            "UTXO" => w.add_utxo(parts[1], parts[2].parse().unwrap(), parts[3]),
            "PAY" => {
                let pid = parts[1];
                let amount: u64 = parts[2].parse().unwrap();
                let fee_rate: u64 = parts[3].parse().unwrap();
                let priority = parts[4];
                match w.select(amount, fee_rate, priority) {
                    None => writeln!(out, "SKIP {pid}").unwrap(),
                    Some((inputs, change)) => {
                        writeln!(out, "TX {pid} {} {change}", inputs.join(",")).unwrap();
                        w.pending.insert(pid.to_string(), inputs);
                    }
                }
                out.flush().unwrap(); // the harness waits for this line!
            }
            "RESULT" => w.on_result(parts[1], parts[2]),
            "END" => break,
            other => eprintln!("unknown message: {other}"),
        }
    }
}
