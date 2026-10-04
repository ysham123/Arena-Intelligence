#include "arena/engine.hpp"
#include <algorithm>
#include <cctype>
#include <cmath>
#include <iomanip>
#include <limits>
#include <queue>
#include <sstream>
#include <stdexcept>
#include <tuple>

namespace arena {
namespace {
int cell(int x, int y) {
  return y * size + x;
}
int manhattan(int a, int b) {
  return std::abs(a % size - b % size) + std::abs(a / size - b / size);
}
std::vector<int> neighbors(int p) {
  std::vector<int> out;
  if (p / size > 0)
    out.push_back(p - size);
  if (p % size > 0)
    out.push_back(p - 1);
  if (p % size < size - 1)
    out.push_back(p + 1);
  if (p / size < size - 1)
    out.push_back(p + size);
  return out;
}
int integer(const Json &j, const char *key, int fallback) {
  if (!j.contains(key))
    return fallback;
  if (!j.at(key).is_number_integer() || j.at(key).is_boolean())
    throw std::invalid_argument(std::string(key) + " must be an integer");
  const auto n = j.at(key).get<std::int64_t>();
  if (n < std::numeric_limits<int>::min() || n > std::numeric_limits<int>::max())
    throw std::invalid_argument(std::string(key) + " out of range");
  return static_cast<int>(n);
}
Json bot_json(const Bot &b) {
  return {{"id", b.id}, {"team", b.team}, {"x", b.x}, {"y", b.y}, {"objective_id", b.objective_id}};
}
} // namespace
Config Config::from_json(const Json &j) {
  if (!j.is_object())
    throw std::invalid_argument("config must be an object");
  Config c;
  if (j.contains("seed")) {
    if (!j.at("seed").is_number_unsigned() && !j.at("seed").is_number_integer())
      throw std::invalid_argument("seed must be an unsigned integer");
    if (!j.at("seed").is_number_unsigned() && j.at("seed").get<std::int64_t>() < 0)
      throw std::invalid_argument("seed must be nonnegative");
    c.seed = j.at("seed").get<std::uint64_t>();
  }
  c.bots_per_team = integer(j, "bots_per_team", c.bots_per_team);
  c.duration_ticks = integer(j, "duration_ticks", c.duration_ticks);
  c.tick_hz = integer(j, "tick_hz", c.tick_hz);
  c.reasoner_team = integer(j, "reasoner_team", c.reasoner_team);
  c.report_delay_ticks = integer(j, "report_delay_ticks", c.report_delay_ticks);
  c.opponent_policy = j.value("opponent_policy", c.opponent_policy);
  c.mirrored = j.value("mirrored", c.mirrored);
  c.paced = j.value("paced", c.paced);
  c.report_drop_rate = j.value("report_drop_rate", c.report_drop_rate);
  if (c.bots_per_team < 1 || c.bots_per_team > 128)
    throw std::invalid_argument("bots_per_team must be 1..128");
  if (c.duration_ticks < 1 || c.duration_ticks > 36000)
    throw std::invalid_argument("duration_ticks must be 1..36000");
  if (c.tick_hz != 10)
    throw std::invalid_argument("protocol v1 requires tick_hz=10");
  if (c.reasoner_team != 0 && c.reasoner_team != 1)
    throw std::invalid_argument("reasoner_team must be 0 or 1");
  if (c.report_delay_ticks < 0 || c.report_delay_ticks > 300)
    throw std::invalid_argument("report_delay_ticks must be 0..300");
  if (!std::isfinite(c.report_drop_rate) || c.report_drop_rate < 0 || c.report_drop_rate > 1)
    throw std::invalid_argument("report_drop_rate must be finite and 0..1");
  if (c.opponent_policy != "nearest" && c.opponent_policy != "holder" &&
      c.opponent_policy != "switch")
    throw std::invalid_argument("unknown opponent policy");
  if (j.contains("planning_ticks")) {
    if (!j.at("planning_ticks").is_array())
      throw std::invalid_argument("planning_ticks must be an array");
    c.planning_ticks.clear();
    for (const auto &v : j.at("planning_ticks")) {
      if (!v.is_number_integer())
        throw std::invalid_argument("planning ticks must be integers");
      const auto n = v.get<std::int64_t>();
      if (n < 0 || n > c.duration_ticks)
        throw std::invalid_argument("planning tick out of range");
      c.planning_ticks.push_back(static_cast<int>(n));
    }
  } else {
    c.planning_ticks.erase(std::remove_if(c.planning_ticks.begin(), c.planning_ticks.end(),
                                          [&](int t) { return t > c.duration_ticks; }),
                           c.planning_ticks.end());
  }
  if (!std::is_sorted(c.planning_ticks.begin(), c.planning_ticks.end()) ||
      std::adjacent_find(c.planning_ticks.begin(), c.planning_ticks.end()) !=
          c.planning_ticks.end() ||
      c.planning_ticks.size() > 128)
    throw std::invalid_argument("planning ticks must be sorted, unique, and bounded");
  return c;
}
Json Config::to_json() const {
  return {{"seed", seed},
          {"bots_per_team", bots_per_team},
          {"duration_ticks", duration_ticks},
          {"tick_hz", tick_hz},
          {"planning_ticks", planning_ticks},
          {"opponent_policy", opponent_policy},
          {"reasoner_team", reasoner_team},
          {"mirrored", mirrored},
          {"report_drop_rate", report_drop_rate},
          {"report_delay_ticks", report_delay_ticks},
          {"paced", paced}};
}
Random::Random(std::uint64_t seed) : state_(seed ^ UINT64_C(0x9e3779b97f4a7c15)) {
  if (!state_)
    state_ = 1;
}
std::uint64_t Random::next() {
  state_ ^= state_ >> 12;
  state_ ^= state_ << 25;
  state_ ^= state_ >> 27;
  return state_ * UINT64_C(2685821657736338717);
}
Engine::Engine(Config config, std::string run_id)
    : config_(std::move(config)), run_id_(std::move(run_id)), random_(config_.seed) {
  for (int team = 0; team < 2; ++team)
    for (int i = 0; i < config_.bots_per_team; ++i) {
      const bool right = (team == 1) ^ config_.mirrored;
      bots_.push_back({team * config_.bots_per_team + i, team, right ? 29 : 2, 5 + i % 22, 0});
    }
  generate_map();
  build_routes();
  update_objectives();
  collect_reports();
  deliver_reports();
}
bool Engine::reachable(int from, int to) const {
  if (from < 0 || to < 0 || from >= size * size || to >= size * size || blocked_[from] ||
      blocked_[to])
    return false;
  std::array<bool, size * size> seen{};
  std::queue<int> q;
  q.push(from);
  seen[from] = true;
  while (!q.empty()) {
    int p = q.front();
    q.pop();
    if (p == to)
      return true;
    for (int n : neighbors(p))
      if (!blocked_[n] && !seen[n]) {
        seen[n] = true;
        q.push(n);
      }
  }
  return false;
}
void Engine::generate_map() {
  for (int attempt = 0; attempt < 100; ++attempt) {
    const int x = 4 + static_cast<int>(random_.next() % 12),
              y = 2 + static_cast<int>(random_.next() % 28), mirror = size - 1 - x;
    bool protected_cell = false;
    for (const auto &n : nodes_)
      if (manhattan(cell(x, y), cell(n.x, n.y)) <= 3 ||
          manhattan(cell(mirror, y), cell(n.x, n.y)) <= 3)
        protected_cell = true;
    if (protected_cell || blocked_[cell(x, y)])
      continue;
    blocked_[cell(x, y)] = blocked_[cell(mirror, y)] = true;
    // Keep every free cell connected, including all team starts and objectives.
    std::array<bool, size * size> seen{};
    std::queue<int> q;
    q.push(cell(2, 5));
    seen[cell(2, 5)] = true;
    while (!q.empty()) {
      int p = q.front();
      q.pop();
      for (int n : neighbors(p))
        if (!blocked_[n] && !seen[n]) {
          seen[n] = true;
          q.push(n);
        }
    }
    bool connected = true;
    for (int p = 0; p < size * size; ++p)
      if (!blocked_[p] && !seen[p]) {
        connected = false;
        break;
      }
    if (!connected)
      blocked_[cell(x, y)] = blocked_[cell(mirror, y)] = false;
  }
}
int Engine::astar_first_step(int start, int goal) const {
  if (start == goal)
    return start;
  using Entry = std::tuple<int, int, int>;
  std::priority_queue<Entry, std::vector<Entry>, std::greater<Entry>> open;
  std::array<int, size * size> g, parent;
  g.fill(100000);
  parent.fill(-1);
  g[start] = 0;
  open.emplace(manhattan(start, goal), manhattan(start, goal), start);
  while (!open.empty()) {
    auto [f, h, p] = open.top();
    open.pop();
    (void)h;
    if (f != g[p] + manhattan(p, goal))
      continue;
    if (p == goal) {
      while (parent[p] != start && parent[p] != -1)
        p = parent[p];
      return parent[p] == -1 ? start : p;
    }
    for (int n : neighbors(p))
      if (!blocked_[n] && g[p] + 1 < g[n]) {
        g[n] = g[p] + 1;
        parent[n] = p;
        const int nh = manhattan(n, goal);
        open.emplace(g[n] + nh, nh, n);
      }
  }
  return start;
}
void Engine::build_routes() {
  for (const auto &node : nodes_) {
    auto &distances = distance_[node.id];
    distances.fill(100000);
    const int goal = cell(node.x, node.y);
    std::queue<int> q;
    q.push(goal);
    distances[goal] = 0;
    while (!q.empty()) {
      int p = q.front();
      q.pop();
      for (int n : neighbors(p))
        if (!blocked_[n] && distances[n] > distances[p] + 1) {
          distances[n] = distances[p] + 1;
          q.push(n);
        }
    }
    for (int p = 0; p < size * size; ++p)
      next_step_[node.id][p] = blocked_[p] ? p : astar_first_step(p, goal);
  }
}
int Engine::nearest_node(const Bot &bot) const {
  int best = 0;
  for (int n = 1; n < 3; ++n)
    if (distance_[n][cell(bot.x, bot.y)] < distance_[best][cell(bot.x, bot.y)])
      best = n;
  return best;
}
void Engine::update_objectives() {
  const bool assigned = assignment_expiry_ > tick_;
  for (auto &b : bots_) {
    if (b.team == config_.reasoner_team) {
      if (!assigned)
        b.objective_id = nearest_node(b);
      continue;
    }
    const int local = b.id % config_.bots_per_team;
    if (config_.opponent_policy == "nearest" ||
        (config_.opponent_policy == "switch" && tick_ < config_.duration_ticks / 2))
      b.objective_id = nearest_node(b);
    else if (config_.opponent_policy == "holder")
      b.objective_id = local % 3;
    else
      b.objective_id = local < ((config_.bots_per_team * 2 + 2) / 3) ? 1 : local % 3;
  }
}
bool Engine::visible_to_reasoner(const Bot &bot) const {
  for (const auto &friendly : bots_)
    if (friendly.team == config_.reasoner_team &&
        manhattan(cell(friendly.x, friendly.y), cell(bot.x, bot.y)) <= 6)
      return true;
  return false;
}
void Engine::collect_reports() {
  const std::uint64_t drop =
      static_cast<std::uint64_t>(std::llround(config_.report_drop_rate * 1000000.0));
  auto report = [&](std::string kind, Json data) {
    if (random_.next() % 1000000 >= drop)
      reports_.push_back({tick_ + config_.report_delay_ticks, std::move(kind), std::move(data)});
  };
  for (const auto &opponent : bots_)
    if (opponent.team != config_.reasoner_team) {
      if (visible_to_reasoner(opponent))
        report("sighting", {{"bot_id", opponent.id},
                            {"x", opponent.x},
                            {"y", opponent.y},
                            {"observed_tick", tick_},
                            {"delivered_tick", tick_ + config_.report_delay_ticks}});
    }
  for (const auto &node : nodes_) {
    bool seen = false;
    int friendly_count = 0, opponent_count = 0;
    for (const auto &b : bots_) {
      const int d = manhattan(cell(b.x, b.y), cell(node.x, node.y));
      if (b.team == config_.reasoner_team && d <= 6)
        seen = true;
      if (d <= 2) {
        if (b.team == config_.reasoner_team)
          ++friendly_count;
        else if (visible_to_reasoner(b))
          ++opponent_count;
      }
    }
    if (seen)
      report("node_status", {{"node_id", node.id},
                             {"friendly_count", friendly_count},
                             {"opponent_count", opponent_count},
                             {"observed_tick", tick_},
                             {"delivered_tick", tick_ + config_.report_delay_ticks}});
  }
}
void Engine::deliver_reports() {
  while (!reports_.empty() && reports_.front().delivery_tick <= tick_) {
    Report r = std::move(reports_.front());
    reports_.pop_front();
    const std::string id = "e" + std::to_string(evidence_sequence_++);
    Json record{
        {"id", id}, {"tick", r.data.at("observed_tick")}, {"kind", r.kind}, {"data", r.data}};
    evidence_.push_back(record);
    if (evidence_.size() > 128)
      evidence_.pop_front();
    if (r.kind == "sighting") {
      const int bot = r.data.at("bot_id");
      last_seen_[bot] = {{"id", bot},
                         {"team", 1 - config_.reasoner_team},
                         {"x", r.data.at("x")},
                         {"y", r.data.at("y")},
                         {"observed_tick", r.data.at("observed_tick")},
                         {"evidence_id", id}};
    }
  }
}
bool Engine::scheduled() const {
  return std::binary_search(config_.planning_ticks.begin(), config_.planning_ticks.end(), tick_);
}
void Engine::step() {
  if (finished())
    return;
  ++tick_;
  if (pending_assignment_ && pending_assignment_->application_tick == tick_) {
    for (auto &b : bots_)
      if (b.team == config_.reasoner_team)
        b.objective_id = pending_assignment_->objectives[b.id % config_.bots_per_team];
    assignment_expiry_ = pending_assignment_->expiry_tick;
    pending_assignment_.reset();
  }
  if (assignment_expiry_ <= tick_)
    assignment_expiry_ = -1;
  if (tick_ % 5 == 0) {
    update_objectives();
    for (auto &b : bots_) {
      const int p = next_step_[b.objective_id][cell(b.x, b.y)];
      b.x = p % size;
      b.y = p / size;
    }
    collect_reports();
  }
  deliver_reports();
  if (tick_ % 10 == 0)
    for (const auto &node : nodes_) {
      std::array<int, 2> count{};
      for (const auto &b : bots_)
        if (manhattan(cell(b.x, b.y), cell(node.x, node.y)) <= 2)
          count[b.team]++;
      if (count[0] > count[1])
        scores_[0]++;
      else if (count[1] > count[0])
        scores_[1]++;
    }
}
Json Engine::envelope(const std::string &type) const {
  return {{"type", type}, {"schema_version", 1}, {"run_id", run_id_}};
}
Json Engine::observation() {
  Json j = envelope("observation");
  const std::string req = "r" + std::to_string(request_sequence_++);
  requests_[req] = {tick_, false, {}};
  j["request_id"] = req;
  j["cutoff_tick"] = tick_;
  Json o{{"tick", tick_},
         {"team", config_.reasoner_team},
         {"size", size},
         {"scores", scores_},
         {"nodes", Json::array()},
         {"blocked", Json::array()},
         {"friendly", Json::array()},
         {"visible_opponents", Json::array()},
         {"last_seen", Json::array()},
         {"evidence", Json::array()}};
  for (const auto &n : nodes_)
    o["nodes"].push_back({{"id", n.id}, {"x", n.x}, {"y", n.y}});
  for (int p = 0; p < size * size; ++p)
    if (blocked_[p])
      o["blocked"].push_back({p % size, p / size});
  for (const auto &b : bots_)
    if (b.team == config_.reasoner_team)
      o["friendly"].push_back(bot_json(b));
  for (const auto &[id, record] : last_seen_) {
    (void)id;
    o["last_seen"].push_back(record);
    if (tick_ - record.at("observed_tick").get<int>() <= config_.report_delay_ticks + 5)
      o["visible_opponents"].push_back(record);
    permitted_evidence_.insert(record.at("evidence_id").get<std::string>());
  }
  for (const auto &e : evidence_) {
    o["evidence"].push_back(e);
    permitted_evidence_.insert(e.at("id").get<std::string>());
  }
  requests_[req].evidence_ids = permitted_evidence_;
  j["observation"] = std::move(o);
  return j;
}
Json Engine::assess(const Json &j) {
  Json out = envelope("assessment_result");
  out["request_id"] = j.is_object() && j.contains("request_id") && j.at("request_id").is_string()
                          ? j.at("request_id")
                          : Json("");
  out["accepted"] = false;
  out["application_tick"] = nullptr;
  out["remaining_horizon_ticks"] = 0;
  auto reject = [&](const std::string &reason) {
    out["reason"] = reason;
    return out;
  };
  try {
    if (!j.is_object() || j.at("type") != "assessment" || j.at("schema_version") != 1 ||
        j.at("run_id") != run_id_)
      return reject("invalid_envelope");
    const std::string req = j.at("request_id").get<std::string>();
    auto it = requests_.find(req);
    if (it == requests_.end())
      return reject("unknown_request");
    if (it->second.consumed)
      return reject("consumed_request");
    const int cutoff = integer(j, "cutoff_tick", -1);
    if (cutoff != it->second.cutoff)
      return reject("cutoff_mismatch");
    if (tick_ > cutoff + 200)
      return reject("late_response");
    if (finished())
      return reject("run_finished");
    if (integer(j, "expiry_tick", -1) != cutoff + 450)
      return reject("expiry_mismatch");
    const auto &a = j.at("assessment");
    const auto &h = a.at("hypotheses");
    if (!h.is_array() || h.empty() || h.size() > 3)
      return reject("invalid_hypotheses");
    for (const auto &hypothesis : h) {
      const auto claim = hypothesis.at("claim").get<std::string>(),
                 alternative = hypothesis.at("alternative").get<std::string>();
      if (claim.empty() || claim.size() > 2000 || alternative.empty() || alternative.size() > 2000)
        return reject("invalid_claim");
      const auto &ids = hypothesis.at("evidence_ids");
      if (!ids.is_array() || ids.size() > 128)
        return reject("invalid_evidence");
      if (ids.empty()) {
        std::string lower = claim;
        std::transform(lower.begin(), lower.end(), lower.begin(),
                       [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
        if (lower.find("prior") == std::string::npos &&
            lower.find("insufficient evidence") == std::string::npos)
          return reject("unsupported_claim");
      }
      for (const auto &id : ids)
        if (!id.is_string() || !it->second.evidence_ids.count(id.get<std::string>()))
          return reject("unknown_evidence");
    }
    const auto &f = a.at("forecast");
    if (integer(f, "horizon_tick", -1) != cutoff + 300)
      return reject("horizon_mismatch");
    if (cutoff + 300 <= tick_ || cutoff + 450 <= tick_)
      return reject("expired_response");
    const auto &probs = f.at("probabilities");
    if (!probs.is_array() || probs.size() != 4)
      return reject("invalid_forecast");
    double sum = 0;
    for (const auto &p : probs) {
      if (!p.is_number())
        return reject("invalid_forecast");
      const double n = p.get<double>();
      if (!std::isfinite(n) || n < 0 || n > 1)
        return reject("invalid_forecast");
      sum += n;
    }
    if (std::abs(sum - 1) > 1e-6)
      return reject("invalid_forecast");
    const auto &assignments = a.at("assignments");
    if (!assignments.is_array() ||
        assignments.size() != static_cast<std::size_t>(config_.bots_per_team))
      return reject("incomplete_assignment");
    std::vector<int> targets(config_.bots_per_team, -1);
    for (const auto &entry : assignments) {
      int id = integer(entry, "bot_id", -1), objective = integer(entry, "objective_id", -1);
      if (id < 0 || id >= static_cast<int>(bots_.size()) ||
          bots_[id].team != config_.reasoner_team || objective < 0 || objective > 2)
        return reject("illegal_assignment");
      const int local = id % config_.bots_per_team;
      if (targets[local] != -1)
        return reject("duplicate_bot");
      targets[local] = objective;
    }
    if (pending_assignment_)
      return reject("pending_application");
    pending_assignment_ = Assignment{tick_ + 1, cutoff + 450, std::move(targets)};
    it->second.consumed = true;
    out["accepted"] = true;
    out["application_tick"] = tick_ + 1;
    out["reason"] = "accepted";
    out["remaining_horizon_ticks"] = cutoff + 300 - (tick_ + 1);
    return out;
  } catch (const std::exception &) {
    return reject("malformed_assessment");
  }
}
Json Engine::fallback(const Json &j) {
  Json out = envelope("fallback_result");
  out["request_id"] = j.is_object() && j.contains("request_id") && j.at("request_id").is_string()
                          ? j.at("request_id")
                          : Json("");
  out["accepted"] = false;
  out["application_tick"] = nullptr;
  auto reject = [&](const std::string &reason) {
    out["reason"] = reason;
    return out;
  };
  try {
    if (!j.is_object() || j.at("type") != "fallback" ||
        !j.at("schema_version").is_number_integer() || j.at("schema_version") != 1 ||
        j.at("run_id") != run_id_)
      return reject("invalid_envelope");
    const std::string req = j.at("request_id").get<std::string>();
    auto it = requests_.find(req);
    if (it == requests_.end())
      return reject("unknown_request");
    if (req != "r" + std::to_string(request_sequence_ - 1))
      return reject("stale_request");
    if (it->second.consumed)
      return reject("consumed_request");
    if (integer(j, "cutoff_tick", -1) != it->second.cutoff)
      return reject("cutoff_mismatch");
    if (finished())
      return reject("run_finished");
    const auto reason = j.at("reason").get<std::string>();
    if (reason.empty() || reason.size() > 256)
      return reject("invalid_reason");
    std::vector<int> targets(config_.bots_per_team, -1);
    for (const auto &bot : bots_)
      if (bot.team == config_.reasoner_team)
        targets[bot.id % config_.bots_per_team] = nearest_node(bot);
    pending_assignment_ = Assignment{tick_ + 1, tick_ + 1, std::move(targets)};
    it->second.consumed = true;
    out["accepted"] = true;
    out["application_tick"] = tick_ + 1;
    out["reason"] = "accepted";
    return out;
  } catch (const std::exception &) {
    return reject("malformed_fallback");
  }
}
Json Engine::state() const {
  Json s{{"tick", tick_},
         {"size", size},
         {"bots", Json::array()},
         {"nodes", Json::array()},
         {"blocked", Json::array()},
         {"scores", scores_},
         {"rng_state", random_.state()},
         {"assignment_expiry_tick", assignment_expiry_},
         {"pending_reports", Json::array()},
         {"last_seen", Json::array()},
         {"evidence", Json::array()},
         {"evidence_sequence", evidence_sequence_}};
  for (const auto &b : bots_)
    s["bots"].push_back(bot_json(b));
  for (const auto &n : nodes_)
    s["nodes"].push_back({{"id", n.id}, {"x", n.x}, {"y", n.y}});
  for (int p = 0; p < size * size; ++p)
    if (blocked_[p])
      s["blocked"].push_back({p % size, p / size});
  for (const auto &r : reports_)
    s["pending_reports"].push_back(
        {{"delivery_tick", r.delivery_tick}, {"kind", r.kind}, {"data", r.data}});
  for (const auto &[id, r] : last_seen_) {
    (void)id;
    s["last_seen"].push_back(r);
  }
  for (const auto &e : evidence_)
    s["evidence"].push_back(e);
  if (pending_assignment_)
    s["pending_assignment"] = {{"application_tick", pending_assignment_->application_tick},
                               {"expiry_tick", pending_assignment_->expiry_tick},
                               {"objectives", pending_assignment_->objectives}};
  else
    s["pending_assignment"] = nullptr;
  return s;
}
std::string checksum_of(const Json &value) {
  const auto bytes = value.dump();
  std::uint64_t hash = UINT64_C(14695981039346656037);
  for (unsigned char c : bytes) {
    hash ^= c;
    hash *= UINT64_C(1099511628211);
  }
  std::ostringstream out;
  out << std::hex << std::setfill('0') << std::setw(16) << hash;
  return out.str();
}
std::string Engine::checksum() const {
  return checksum_of(state());
}
Json Engine::result(const std::string &status) const {
  return {
      {"status", status},
      {"tick", tick_},
      {"scores", scores_},
      {"winner", scores_[0] == scores_[1] ? Json(nullptr) : Json(scores_[0] > scores_[1] ? 0 : 1)},
      {"checksum", checksum()},
      {"engine_version", engine_version}};
}
} // namespace arena
