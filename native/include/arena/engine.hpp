#pragma once
#include <array>
#include <cstdint>
#include <deque>
#include <map>
#include <nlohmann/json.hpp>
#include <optional>
#include <set>
#include <string>
#include <vector>

namespace arena {
using Json = nlohmann::json;
inline constexpr int size = 32;
inline constexpr const char *engine_version = "0.1.0";
struct Config {
  std::uint64_t seed = 42;
  int bots_per_team = 6;
  int duration_ticks = 1800;
  int tick_hz = 10;
  std::vector<int> planning_ticks{0, 300, 600, 900, 1200, 1500};
  std::string opponent_policy = "switch";
  int reasoner_team = 0;
  bool mirrored = false;
  double report_drop_rate = 0.1;
  int report_delay_ticks = 20;
  bool paced = false;
  static Config from_json(const Json &value);
  Json to_json() const;
};
struct Bot {
  int id, team, x, y, objective_id;
};
struct Node {
  int id, x, y;
};
class Random {
public:
  explicit Random(std::uint64_t seed);
  std::uint64_t next();
  std::uint64_t state() const {
    return state_;
  }

private:
  std::uint64_t state_;
};
class Engine {
public:
  Engine(Config config, std::string run_id);
  const Config &config() const {
    return config_;
  }
  const std::string &run_id() const {
    return run_id_;
  }
  int tick() const {
    return tick_;
  }
  bool finished() const {
    return tick_ >= config_.duration_ticks;
  }
  bool scheduled() const;
  void step();
  Json observation();
  Json assess(const Json &envelope);
  Json fallback(const Json &envelope);
  Json state() const;
  std::string checksum() const;
  Json result(const std::string &status = "completed") const;
  const std::vector<Bot> &bots() const {
    return bots_;
  }
  const std::array<Node, 3> &nodes() const {
    return nodes_;
  }
  bool reachable(int from, int to) const;

private:
  friend class EngineTestAccess;
  bool visible_to_reasoner(const Bot &bot) const;
  struct Report {
    int delivery_tick;
    std::string kind;
    Json data;
  };
  struct Assignment {
    int application_tick, expiry_tick;
    std::vector<int> objectives;
  };
  struct Request {
    int cutoff;
    bool consumed;
    std::set<std::string> evidence_ids;
  };
  Config config_;
  std::string run_id_;
  Random random_;
  int tick_ = 0;
  std::vector<Bot> bots_;
  std::array<Node, 3> nodes_{{{0, 7, 8}, {1, 15, 24}, {2, 24, 8}}};
  std::array<bool, size * size> blocked_{};
  std::array<std::array<int, size * size>, 3> next_step_{};
  std::array<std::array<int, size * size>, 3> distance_{};
  std::array<int, 2> scores_{};
  std::deque<Report> reports_;
  std::deque<Json> evidence_;
  std::set<std::string> permitted_evidence_;
  std::map<int, Json> last_seen_;
  std::map<std::string, Request> requests_;
  std::uint64_t evidence_sequence_ = 0, request_sequence_ = 0;
  std::optional<Assignment> pending_assignment_;
  int assignment_expiry_ = -1;
  void generate_map();
  void build_routes();
  int astar_first_step(int start, int goal) const;
  int nearest_node(const Bot &bot) const;
  void update_objectives();
  void collect_reports();
  void deliver_reports();
  Json envelope(const std::string &type) const;
};
std::string checksum_of(const Json &value);
} // namespace arena
