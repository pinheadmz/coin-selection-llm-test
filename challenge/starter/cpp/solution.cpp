// P1.1 starter wallet (C++17).
//
// Speaks the harness protocol on stdin/stdout and implements naive
// largest-first coin selection. It passes every sanity check and scores ~100
// (the baseline). Your job: make select() smarter. Debug output goes to
// std::cerr, never std::cout. Only the standard library is required.

#include <algorithm>
#include <cstdint>
#include <iostream>
#include <map>
#include <optional>
#include <set>
#include <sstream>
#include <string>
#include <utility>
#include <vector>

namespace {

constexpr int64_t INPUT_VB = 68;
constexpr int64_t OUTPUT_VB = 31;
constexpr int64_t OVERHEAD_VB = 11;
constexpr size_t MAX_INPUTS = 100;

struct Utxo {
    int64_t value;
    std::set<std::string> labels;
};

struct Selection {
    std::vector<std::string> inputs;
    int64_t change;
};

std::vector<std::string> split(const std::string& s, char sep) {
    std::vector<std::string> out;
    std::string item;
    std::istringstream ss(s);
    while (std::getline(ss, item, sep)) out.push_back(item);
    return out;
}

class Wallet {
public:
    int64_t long_term_fee_rate = 10;
    int64_t dust_limit = 294;

    void add_utxo(const std::string& outpoint, int64_t value, const std::string& labels) {
        auto parts = split(labels, ',');
        utxos_[outpoint] = Utxo{value, std::set<std::string>(parts.begin(), parts.end())};
    }

    void propose(const std::string& payment_id, const std::vector<std::string>& inputs) {
        pending_[payment_id] = inputs;
    }

    void on_result(const std::string& payment_id, const std::string& status) {
        auto it = pending_.find(payment_id);
        if (it == pending_.end()) return;
        if (status == "ACCEPTED") {
            for (const auto& op : it->second) utxos_.erase(op);
            // TODO: remember which labels these inputs just linked together.
        } else if (status == "REJECTED") {
            std::cerr << "payment " << payment_id << " was rejected!\n";
        }
        pending_.erase(it);
    }

    // Returns the inputs and change value, or nullopt to skip the payment.
    // `priority` is one of "privacy", "balanced", "min_cost" -- this naive
    // version ignores it.
    std::optional<Selection> select(int64_t amount, int64_t fee_rate, const std::string& /*priority*/) const {
        std::vector<std::pair<std::string, int64_t>> coins;
        for (const auto& [op, u] : utxos_) coins.emplace_back(op, u.value);
        std::sort(coins.begin(), coins.end(), [](const auto& a, const auto& b) {
            return a.second != b.second ? a.second > b.second : a.first < b.first;
        });

        Selection sel;
        int64_t total = 0;
        bool funded = false;
        for (const auto& [op, value] : coins) {
            sel.inputs.push_back(op);
            total += value;
            int64_t n = static_cast<int64_t>(sel.inputs.size());
            int64_t fee_no_change = (OVERHEAD_VB + INPUT_VB * n + OUTPUT_VB) * fee_rate;
            if (total >= amount + fee_no_change) {
                funded = true;
                break;
            }
        }
        if (!funded || sel.inputs.size() > MAX_INPUTS) return std::nullopt;

        int64_t n = static_cast<int64_t>(sel.inputs.size());
        int64_t fee_with_change = (OVERHEAD_VB + INPUT_VB * n + 2 * OUTPUT_VB) * fee_rate;
        sel.change = total - amount - fee_with_change;
        if (sel.change < dust_limit) sel.change = 0;  // leftover goes to the miner
        return sel;
    }

private:
    std::map<std::string, Utxo> utxos_;
    std::map<std::string, std::vector<std::string>> pending_;  // payment id -> proposed inputs
};

}  // namespace

int main() {
    std::ios::sync_with_stdio(false);
    Wallet w;
    std::string line;
    while (std::getline(std::cin, line)) {
        std::istringstream in(line);
        std::string cmd;
        if (!(in >> cmd)) continue;

        if (cmd == "PARAMS") {
            in >> w.long_term_fee_rate >> w.dust_limit;
        } else if (cmd == "UTXO") {
            std::string op, labels;
            int64_t value;
            in >> op >> value >> labels;
            w.add_utxo(op, value, labels);
        } else if (cmd == "PAY") {
            std::string pid, priority;
            int64_t amount, fee_rate;
            in >> pid >> amount >> fee_rate >> priority;
            auto sel = w.select(amount, fee_rate, priority);
            if (!sel) {
                std::cout << "SKIP " << pid << std::endl;  // std::endl flushes -- required!
            } else {
                std::cout << "TX " << pid << ' ';
                for (size_t i = 0; i < sel->inputs.size(); ++i) {
                    if (i) std::cout << ',';
                    std::cout << sel->inputs[i];
                }
                std::cout << ' ' << sel->change << std::endl;
                w.propose(pid, sel->inputs);
            }
        } else if (cmd == "RESULT") {
            std::string pid, status;
            in >> pid >> status;
            w.on_result(pid, status);
        } else if (cmd == "END") {
            break;
        } else {
            std::cerr << "unknown message: " << cmd << '\n';
        }
    }
    return 0;
}
