#include "arena/engine.hpp"
#include <catch2/catch_test_macros.hpp>
#include <cmath>
#include <limits>
using arena::Json;
namespace arena {
// Only this test translation unit defines the friend fixture; production tools
// expose no privileged mutation or observation capability.
class EngineTestAccess {
public:
  static void position_fixture(Engine &e, const std::vector<std::pair<int, int>> &positions) {
    REQUIRE(positions.size() == e.bots_.size());
    e.blocked_.fill(false);
    e.build_routes();
    for (std::size_t i = 0; i < positions.size(); ++i) {
      e.bots_[i].x = positions[i].first;
      e.bots_[i].y = positions[i].second;
    }
    e.reports_.clear();
    e.evidence_.clear();
    e.last_seen_.clear();
    e.permitted_evidence_.clear();
    e.evidence_sequence_ = 0;
    e.update_objectives();
  }
  static Json observe_now(Engine &e) {
    e.collect_reports();
    e.deliver_reports();
    return e.observation();
  }
};
} // namespace arena

namespace {
Json assessment(const Json &obs) {
  const int cutoff = obs.at("cutoff_tick");
  Json assignments = Json::array();
  int n = 0;
  for (const auto &b : obs.at("observation").at("friendly"))
    assignments.push_back({{"bot_id", b.at("id")}, {"objective_id", n++ % 3}});
  return {{"type", "assessment"},
          {"schema_version", 1},
          {"run_id", obs.at("run_id")},
          {"request_id", obs.at("request_id")},
          {"cutoff_tick", cutoff},
          {"expiry_tick", cutoff + 450},
          {"assessment",
           {{"hypotheses", Json::array({{{"claim", "Prior: insufficient evidence"},
                                         {"evidence_ids", Json::array()},
                                         {"alternative", "Another policy is possible"}}})},
            {"forecast", {{"horizon_tick", cutoff + 300}, {"probabilities", {.25, .25, .25, .25}}}},
            {"assignments", assignments}}}};
}
} // namespace
TEST_CASE("fixed seeds produce identical canonical state throughout a match") {
  arena::Config c;
  c.duration_ticks = 650;
  arena::Engine a(c, "a"), b(c, "b");
  REQUIRE(a.state() == b.state());
  for (int i = 0; i < 650; ++i) {
    a.step();
    b.step();
    REQUIRE(a.checksum() == b.checksum());
  }
  REQUIRE(a.finished());
  REQUIRE(a.result().at("scores") == b.result().at("scores"));
}
TEST_CASE("generated maps are symmetric and every objective reachable from every start") {
  for (std::uint64_t seed : {0, 1, 42, 999}) {
    arena::Config c;
    c.seed = seed;
    arena::Engine e(c, "map");
    const auto state = e.state();
    std::set<std::pair<int, int>> blocked;
    for (const auto &xy : state.at("blocked"))
      blocked.emplace(xy.at(0), xy.at(1));
    for (const auto &[x, y] : blocked)
      REQUIRE(blocked.count({31 - x, y}) == 1);
    for (const auto &b : e.bots())
      for (const auto &n : e.nodes())
        REQUIRE(e.reachable(b.y * 32 + b.x, n.y * 32 + n.x));
  }
}
TEST_CASE("assessment changes all friendly objectives only at next boundary") {
  arena::Config c;
  arena::Engine e(c, "atomic");
  const auto observation = e.observation();
  const auto before = e.state().at("bots");
  auto command = assessment(observation);
  REQUIRE(e.assess(command).at("accepted") == true);
  REQUIRE(e.state().at("bots") == before);
  e.step();
  for (int i = 0; i < 6; ++i)
    REQUIRE(e.bots()[i].objective_id == i % 3);
  REQUIRE(e.assess(command).at("reason") == "consumed_request");
}
TEST_CASE("invalid evidence, assignments, probabilities, cutoffs and runs cannot mutate state") {
  arena::Config c;
  arena::Engine e(c, "validation");
  const auto observation = e.observation();
  const auto good = assessment(observation);
  const auto before = e.state();
  SECTION("private evidence") {
    auto j = good;
    j["assessment"]["hypotheses"][0]["evidence_ids"] = {"private"};
    REQUIRE(e.assess(j).at("reason") == "unknown_evidence");
  }
  SECTION("empty unsupported claim") {
    auto j = good;
    j["assessment"]["hypotheses"][0]["claim"] = "Opponent is heading north";
    REQUIRE(e.assess(j).at("reason") == "unsupported_claim");
  }
  SECTION("duplicate bot") {
    auto j = good;
    j["assessment"]["assignments"][1]["bot_id"] = 0;
    REQUIRE(e.assess(j).at("reason") == "duplicate_bot");
  }
  SECTION("incomplete") {
    auto j = good;
    j["assessment"]["assignments"].erase(0);
    REQUIRE(e.assess(j).at("reason") == "incomplete_assignment");
  }
  SECTION("enemy assignment") {
    auto j = good;
    j["assessment"]["assignments"][0]["bot_id"] = 6;
    REQUIRE(e.assess(j).at("reason") == "illegal_assignment");
  }
  SECTION("wrong sum") {
    auto j = good;
    j["assessment"]["forecast"]["probabilities"] = {.3, .3, .3, .3};
    REQUIRE(e.assess(j).at("reason") == "invalid_forecast");
  }
  SECTION("nonfinite") {
    auto j = good;
    j["assessment"]["forecast"]["probabilities"][0] = std::numeric_limits<double>::infinity();
    REQUIRE(e.assess(j).at("reason") == "invalid_forecast");
  }
  SECTION("wrong cutoff") {
    auto j = good;
    j["cutoff_tick"] = 1;
    REQUIRE(e.assess(j).at("reason") == "cutoff_mismatch");
  }
  SECTION("wrong run") {
    auto j = good;
    j["run_id"] = "other";
    REQUIRE(e.assess(j).at("accepted") == false);
  }
  REQUIRE(e.state() == before);
}
TEST_CASE("late response is rejected and assignment expires to nearest fallback") {
  arena::Config c;
  arena::Engine e(c, "late");
  auto obs = e.observation();
  for (int i = 0; i < 201; ++i)
    e.step();
  REQUIRE(e.assess(assessment(obs)).at("reason") == "late_response");
  arena::Engine assigned(c, "expiry"), fallback(c, "fallback");
  auto j = assessment(assigned.observation());
  for (auto &a : j["assessment"]["assignments"])
    a["objective_id"] = 1;
  REQUIRE(assigned.assess(j).at("accepted") == true);
  for (int i = 0; i < 450; ++i)
    assigned.step();
  REQUIRE(assigned.state().at("assignment_expiry_tick") == -1);
}
TEST_CASE("filtered observations never disclose privileged fields or unseen enemy positions") {
  arena::Config c;
  c.report_drop_rate = 0;
  c.report_delay_ticks = 20;
  arena::Engine e(c, "isolation");
  auto initial = e.observation().at("observation");
  REQUIRE(initial.at("visible_opponents").empty());
  REQUIRE(initial.at("last_seen").empty());
  for (int i = 0; i < 400; ++i)
    e.step();
  const auto obs = e.observation().at("observation");
  const auto text = obs.dump();
  REQUIRE(text.find("opponent_policy") == std::string::npos);
  REQUIRE(text.find("rng_state") == std::string::npos);
  REQUIRE(obs.at("friendly").size() == 6);
  REQUIRE(obs.at("evidence").size() <= 128);
  for (const auto &report : obs.at("last_seen"))
    REQUIRE(report.at("observed_tick").get<int>() <= 380);
  for (const auto &record : obs.at("evidence"))
    if (record.at("kind") == "sighting") {
      const auto &d = record.at("data");
      REQUIRE(d.at("delivered_tick").get<int>() - d.at("observed_tick").get<int>() == 20);
    }
}
TEST_CASE("complete sensor dropout retains no manufactured sightings") {
  arena::Config c;
  c.report_drop_rate = 1;
  arena::Engine e(c, "drop");
  for (int i = 0; i < 500; ++i)
    e.step();
  auto o = e.observation().at("observation");
  REQUIRE(o.at("evidence").empty());
  REQUIRE(o.at("last_seen").empty());
}
TEST_CASE("holder and switch policies differ and mirroring swaps starting sides") {
  arena::Config c;
  c.duration_ticks = 100;
  c.opponent_policy = "switch";
  arena::Engine switched(c, "s");
  for (int i = 0; i < 50; ++i)
    switched.step();
  for (int i = 6; i < 10; ++i)
    REQUIRE(switched.bots()[i].objective_id == 1);
  c.opponent_policy = "holder";
  arena::Engine holder(c, "h");
  for (int i = 6; i < 12; ++i)
    REQUIRE(holder.bots()[i].objective_id == (i - 6) % 3);
  c.mirrored = true;
  arena::Engine mirrored(c, "m");
  REQUIRE(mirrored.bots()[0].x == 29);
  REQUIRE(mirrored.bots()[6].x == 2);
}
TEST_CASE("config rejects invalid and excessive workloads") {
  REQUIRE_THROWS(arena::Config::from_json({{"bots_per_team", 129}}));
  REQUIRE_THROWS(arena::Config::from_json({{"tick_hz", 20}}));
  REQUIRE_THROWS(arena::Config::from_json({{"planning_ticks", {30, 0}}}));
  REQUIRE_THROWS(arena::Config::from_json({{"report_drop_rate", 1.1}}));
}

TEST_CASE("unsigned seed uses complete portable 64-bit range") {
  auto c = arena::Config::from_json({{"seed", std::numeric_limits<std::uint64_t>::max()}});
  REQUIRE(c.seed == std::numeric_limits<std::uint64_t>::max());
}

TEST_CASE("later permitted observations cannot substantiate an earlier cutoff") {
  arena::Config c;
  c.report_drop_rate = 0;
  arena::Engine e(c, "cutoff-scope");
  auto initial = e.observation();
  for (int i = 0; i < 100; ++i)
    e.step();
  auto later = e.observation();
  REQUIRE_FALSE(later.at("observation").at("evidence").empty());
  auto j = assessment(initial);
  j["assessment"]["hypotheses"][0]["claim"] = "Observed opposing pressure";
  j["assessment"]["hypotheses"][0]["evidence_ids"] =
      Json::array({later.at("observation").at("evidence")[0].at("id")});
  REQUIRE(e.assess(j).at("reason") == "unknown_evidence");
}

TEST_CASE("changing an unseen opponent cannot change node reports or observations") {
  arena::Config c;
  c.bots_per_team = 1;
  c.report_delay_ticks = 0;
  c.report_drop_rate = 0;
  c.opponent_policy = "holder";
  arena::Engine a(c, "counterfactual"), b(c, "counterfactual");
  arena::EngineTestAccess::position_fixture(a, {{15, 18}, {15, 25}});
  arena::EngineTestAccess::position_fixture(b, {{15, 18}, {15, 28}});
  const auto obs_a = arena::EngineTestAccess::observe_now(a),
             obs_b = arena::EngineTestAccess::observe_now(b);
  REQUIRE(obs_a == obs_b);
  REQUIRE(obs_a.at("observation").at("visible_opponents").empty());
  REQUIRE(obs_a.at("observation").at("last_seen").empty());
  const auto &evidence = obs_a.at("observation").at("evidence");
  REQUIRE(evidence.size() == 1);
  REQUIRE(evidence[0].at("kind") == "node_status");
  REQUIRE(evidence[0].at("data").at("node_id") == 1);
  REQUIRE(evidence[0].at("data").at("opponent_count") == 0);
}
TEST_CASE("visible opponents contribute to observed node occupancy") {
  arena::Config c;
  c.bots_per_team = 1;
  c.report_delay_ticks = 0;
  c.report_drop_rate = 0;
  c.opponent_policy = "holder";
  arena::Engine e(c, "visible-occupancy");
  arena::EngineTestAccess::position_fixture(e, {{15, 19}, {15, 25}});
  const auto obs = arena::EngineTestAccess::observe_now(e);
  REQUIRE(obs.at("observation").at("visible_opponents").size() == 1);
  bool found = false;
  for (const auto &record : obs.at("observation").at("evidence"))
    if (record.at("kind") == "node_status" && record.at("data").at("node_id") == 1) {
      found = true;
      REQUIRE(record.at("data").at("opponent_count") == 1);
    }
  REQUIRE(found);
}
TEST_CASE("movement takes at most one free cardinal step only every five ticks") {
  arena::Config c;
  c.opponent_policy = "holder";
  arena::Engine e(c, "movement");
  const auto initial_state = e.state();
  std::set<std::pair<int, int>> blocked;
  for (const auto &xy : initial_state.at("blocked"))
    blocked.emplace(xy.at(0), xy.at(1));
  bool moved = false;
  for (int i = 1; i <= 300; ++i) {
    const auto previous = e.bots();
    e.step();
    for (std::size_t b = 0; b < previous.size(); ++b) {
      const auto &before = previous[b];
      const auto &after = e.bots()[b];
      const int distance = std::abs(before.x - after.x) + std::abs(before.y - after.y);
      REQUIRE(distance <= 1);
      if (i % 5 != 0)
        REQUIRE(distance == 0);
      if (distance)
        moved = true;
      REQUIRE(blocked.count({after.x, after.y}) == 0);
    }
  }
  REQUIRE(moved);
  for (const auto &bot : e.bots()) {
    const auto &node = e.nodes()[bot.objective_id];
    REQUIRE(bot.x == node.x);
    REQUIRE(bot.y == node.y);
  }
}
TEST_CASE("majority scoring occurs only every ten ticks and ties give no points") {
  arena::Config c;
  c.bots_per_team = 2;
  c.opponent_policy = "holder";
  c.report_drop_rate = 1;
  SECTION("strict majority") {
    arena::Engine e(c, "majority");
    arena::EngineTestAccess::position_fixture(e, {{7, 8}, {7, 8}, {7, 8}, {29, 31}});
    for (int tick = 1; tick <= 20; ++tick) {
      e.step();
      REQUIRE(e.state().at("scores")[0] == tick / 10);
      REQUIRE(e.state().at("scores")[1] == 0);
    }
  }
  SECTION("equal occupancy") {
    arena::Engine e(c, "tie");
    arena::EngineTestAccess::position_fixture(e, {{7, 8}, {2, 31}, {7, 8}, {29, 31}});
    for (int tick = 1; tick <= 10; ++tick) {
      e.step();
      REQUIRE(e.state().at("scores") == Json::array({0, 0}));
    }
  }
}

TEST_CASE("worker failure cancels earlier advice at next boundary and cannot cancel newer advice") {
  arena::Config c;
  c.planning_ticks = {0, 10, 20};
  arena::Engine e(c, "fallback");
  auto first = assessment(e.observation());
  for (auto &a : first["assessment"]["assignments"])
    a["objective_id"] = 1;
  REQUIRE(e.assess(first).at("accepted") == true);
  for (int i = 0; i < 10; ++i)
    e.step();
  auto current = e.observation();
  Json failure{{"type", "fallback"},   {"schema_version", 1},
               {"run_id", "fallback"}, {"request_id", current.at("request_id")},
               {"cutoff_tick", 10},    {"reason", "worker_timeout"}};
  const auto before = e.bots();
  auto result = e.fallback(failure);
  REQUIRE(result.at("accepted") == true);
  REQUIRE(result.at("application_tick") == 11);
  for (int i = 0; i < 6; ++i)
    REQUIRE(e.bots()[i].objective_id == before[i].objective_id);
  REQUIRE(e.fallback(failure).at("reason") == "consumed_request");
  e.step();
  REQUIRE(e.state().at("assignment_expiry_tick") == -1);
  for (int i = 0; i < 6; ++i)
    REQUIRE(e.bots()[i].objective_id == 0);
  for (int i = 0; i < 9; ++i)
    e.step();
  auto newest = assessment(e.observation());
  for (auto &a : newest["assessment"]["assignments"])
    a["objective_id"] = 2;
  REQUIRE(e.assess(newest).at("accepted") == true);
  const auto state = e.state();
  REQUIRE(e.fallback(failure).at("reason") == "stale_request");
  REQUIRE(e.state() == state);
  e.step();
  for (int i = 0; i < 6; ++i)
    REQUIRE(e.bots()[i].objective_id == 2);
}
TEST_CASE(
    "fallback validates cutoff and bounded reason while allowing failure after advice deadline") {
  arena::Config c;
  arena::Engine e(c, "fallback-errors");
  auto obs = e.observation();
  Json j{{"type", "fallback"},
         {"schema_version", 1},
         {"run_id", "fallback-errors"},
         {"request_id", obs.at("request_id")},
         {"cutoff_tick", 0},
         {"reason", "timeout"}};
  auto bad = j;
  bad["cutoff_tick"] = 1;
  REQUIRE(e.fallback(bad).at("reason") == "cutoff_mismatch");
  bad = j;
  bad["reason"] = std::string(257, 'x');
  REQUIRE(e.fallback(bad).at("reason") == "invalid_reason");
  for (int i = 0; i < 201; ++i)
    e.step();
  REQUIRE(e.fallback(j).at("accepted") == true);
}

TEST_CASE("opponent trajectories are invariant to friendly objective assignments") {
  for (const std::string policy : {"nearest", "holder", "switch"})
    for (bool mirrored : {false, true}) {
      arena::Config c;
      c.opponent_policy = policy;
      c.mirrored = mirrored;
      c.duration_ticks = 600;
      c.planning_ticks = {0, 300};
      arena::Engine node_zero(c, "invariance"), node_two(c, "invariance");
      auto zero = assessment(node_zero.observation()), two = assessment(node_two.observation());
      for (auto &a : zero["assessment"]["assignments"])
        a["objective_id"] = 0;
      for (auto &a : two["assessment"]["assignments"])
        a["objective_id"] = 2;
      REQUIRE(node_zero.assess(zero).at("accepted") == true);
      REQUIRE(node_two.assess(two).at("accepted") == true);
      for (int tick = 1; tick <= 600; ++tick) {
        node_zero.step();
        node_two.step();
        for (int bot = 6; bot < 12; ++bot) {
          REQUIRE(node_zero.bots()[bot].objective_id == node_two.bots()[bot].objective_id);
          REQUIRE(node_zero.bots()[bot].x == node_two.bots()[bot].x);
          REQUIRE(node_zero.bots()[bot].y == node_two.bots()[bot].y);
        }
        if (tick == 300) {
          zero = assessment(node_zero.observation());
          two = assessment(node_two.observation());
          for (auto &a : zero["assessment"]["assignments"])
            a["objective_id"] = 0;
          for (auto &a : two["assessment"]["assignments"])
            a["objective_id"] = 2;
          REQUIRE(node_zero.assess(zero).at("accepted") == true);
          REQUIRE(node_two.assess(two).at("accepted") == true);
        }
      }
      REQUIRE(node_zero.bots()[0].objective_id != node_two.bots()[0].objective_id);
    }
}
