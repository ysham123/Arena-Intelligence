#include "arena/engine.hpp"
#include <algorithm>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <csignal>
#include <deque>
#include <fcntl.h>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <memory>
#include <mutex>
#include <poll.h>
#include <stdexcept>
#include <sys/resource.h>
#include <sys/utsname.h>
#include <thread>
#include <unistd.h>

namespace {
using arena::Json;
using Clock = std::chrono::steady_clock;
namespace fs = std::filesystem;
constexpr std::size_t max_frame = 65536;
template <class T> class Queue {
public:
  explicit Queue(std::size_t limit) : limit_(limit) {}
  bool push(T item) {
    std::lock_guard lock(mutex_);
    if (closed_ || items_.size() >= limit_)
      return false;
    items_.push_back(std::move(item));
    cv_.notify_all();
    return true;
  }
  bool push_wait(T item, const std::atomic<bool> &stop) {
    std::unique_lock lock(mutex_);
    cv_.wait_for(lock, std::chrono::milliseconds(50),
                 [&] { return closed_ || items_.size() < limit_ || stop.load(); });
    while (!closed_ && !stop.load() && items_.size() >= limit_)
      cv_.wait_for(lock, std::chrono::milliseconds(50));
    if (closed_ || stop.load())
      return false;
    items_.push_back(std::move(item));
    cv_.notify_all();
    return true;
  }
  std::optional<T> pop() {
    std::lock_guard lock(mutex_);
    if (items_.empty())
      return std::nullopt;
    T out = std::move(items_.front());
    items_.pop_front();
    cv_.notify_all();
    return out;
  }
  std::optional<T> wait_pop() {
    std::unique_lock lock(mutex_);
    cv_.wait_for(lock, std::chrono::milliseconds(50), [&] { return closed_ || !items_.empty(); });
    if (items_.empty())
      return std::nullopt;
    T out = std::move(items_.front());
    items_.pop_front();
    cv_.notify_all();
    return out;
  }
  bool done() {
    std::lock_guard lock(mutex_);
    return closed_ && items_.empty();
  }
  bool empty() {
    std::lock_guard lock(mutex_);
    return items_.empty();
  }
  void close() {
    std::lock_guard lock(mutex_);
    closed_ = true;
    cv_.notify_all();
  }

private:
  std::size_t limit_;
  std::deque<T> items_;
  bool closed_ = false;
  std::mutex mutex_;
  std::condition_variable cv_;
};
class Transport {
public:
  Queue<Json> incoming{64};
  Transport() {
    std::signal(SIGPIPE, SIG_IGN);
    original_flags_ = fcntl(STDOUT_FILENO, F_GETFL, 0);
    if (original_flags_ >= 0)
      fcntl(STDOUT_FILENO, F_SETFL, original_flags_ | O_NONBLOCK);
    reader_ = std::thread([this] { read_loop(); });
    writer_ = std::thread([this] { write_loop(); });
  }
  ~Transport() {
    shutdown();
    if (original_flags_ >= 0)
      fcntl(STDOUT_FILENO, F_SETFL, original_flags_);
  }
  bool emit(const Json &value) {
    auto line = value.dump() + "\n";
    if (line.size() > max_frame || !outgoing_.push(std::move(line))) {
      failed_ = true;
      return false;
    }
    return true;
  }
  bool failed() const {
    return failed_;
  }
  bool eof() const {
    return eof_;
  }
  void shutdown() {
    if (shutdown_done_)
      return;
    shutdown_done_ = true;
    reader_stop_ = true;
    incoming.close();
    if (reader_.joinable())
      reader_.join();
    outgoing_.close();
    if (writer_.joinable())
      writer_.join();
  }

private:
  Queue<std::string> outgoing_{256};
  std::atomic<bool> reader_stop_{false}, failed_{false}, eof_{false};
  std::thread reader_, writer_;
  bool shutdown_done_ = false;
  int original_flags_ = -1;
  void read_loop() {
    std::string buffer;
    bool oversized = false;
    char chunk[4096];
    while (!reader_stop_) {
      pollfd p{STDIN_FILENO, POLLIN, 0};
      const int ready = poll(&p, 1, 50);
      if (ready < 0) {
        if (errno == EINTR)
          continue;
        failed_ = true;
        break;
      }
      if (!ready)
        continue;
      const ssize_t n = read(STDIN_FILENO, chunk, sizeof(chunk));
      if (n == 0) {
        eof_ = true;
        break;
      }
      if (n < 0) {
        if (errno == EINTR || errno == EAGAIN)
          continue;
        failed_ = true;
        break;
      }
      for (ssize_t i = 0; i < n; ++i) {
        char c = chunk[i];
        if (c == '\n') {
          if (oversized) {
            if (!incoming.push_wait({{"type", "_parse_error"}, {"reason", "frame_too_large"}},
                                    reader_stop_))
              return;
          } else if (!buffer.empty()) {
            auto parsed = Json::parse(buffer, nullptr, false);
            if (parsed.is_discarded())
              parsed = {{"type", "_parse_error"}, {"reason", "invalid_json"}};
            if (!incoming.push_wait(std::move(parsed), reader_stop_))
              return;
          }
          buffer.clear();
          oversized = false;
        } else if (!oversized) {
          if (buffer.size() >= max_frame - 1) {
            buffer.clear();
            oversized = true;
          } else
            buffer.push_back(c);
        }
      }
    }
    if (!reader_stop_ && !buffer.empty() && !oversized) {
      auto parsed = Json::parse(buffer, nullptr, false);
      if (!parsed.is_discarded())
        incoming.push_wait(std::move(parsed), reader_stop_);
    }
  }
  void write_loop() {
    while (!outgoing_.done()) {
      auto item = outgoing_.wait_pop();
      if (!item)
        continue;
      std::size_t sent = 0;
      auto deadline = Clock::now() + std::chrono::seconds(2);
      while (sent < item->size()) {
        pollfd p{STDOUT_FILENO, POLLOUT, 0};
        int ready = poll(&p, 1, 50);
        if (ready < 0 && errno == EINTR)
          continue;
        if (ready < 0 || p.revents & (POLLERR | POLLHUP | POLLNVAL) || Clock::now() > deadline) {
          failed_ = true;
          return;
        }
        if (!ready)
          continue;
        const ssize_t n = write(STDOUT_FILENO, item->data() + sent, item->size() - sent);
        if (n > 0)
          sent += static_cast<std::size_t>(n);
        else if (n < 0 && errno != EAGAIN && errno != EINTR) {
          failed_ = true;
          return;
        }
      }
    }
  }
};
class Records {
public:
  Records(const fs::path &directory, const arena::Engine &engine) : directory_(directory) {
    if (!directory.is_absolute())
      throw std::invalid_argument("record_dir must be absolute");
    if (fs::exists(directory / "manifest.json"))
      throw std::invalid_argument("record_dir already contains a run; choose a fresh directory");
    fs::create_directories(directory);
    commands_.open(directory / "commands.ndjson");
    states_.open(directory / "states.ndjson");
    observations_.open(directory / "observations.ndjson");
    if (!commands_ || !states_ || !observations_)
      throw std::runtime_error("cannot open record files");
    std::ofstream manifest(directory / "manifest.json");
    if (!manifest)
      throw std::runtime_error("cannot write manifest");
    manifest << Json{{"schema_version", 1},
                     {"engine_version", arena::engine_version},
                     {"run_id", engine.run_id()},
                     {"config", engine.config().to_json()}}
                    .dump(2)
             << "\n";
  }
  void state(const arena::Engine &e) {
    Json s = e.state();
    states_ << Json{{"tick", e.tick()},
                    {"checksum", arena::checksum_of(s)},
                    {"state", std::move(s)}}
                   .dump()
            << "\n";
    check();
  }
  void observation(const Json &o) {
    observations_ << o.dump() << "\n";
    check();
  }
  void command(int tick, const Json &command, const Json &result) {
    commands_ << Json{{"receipt_tick", tick}, {"command", command}, {"result", result}}.dump()
              << "\n";
    check();
  }
  void flush() {
    commands_.flush();
    states_.flush();
    observations_.flush();
    check();
  }
  void finish(const arena::Engine &e, const std::string &status) {
    flush();
    std::ofstream out(directory_ / "results.json");
    if (!out)
      throw std::runtime_error("cannot write results");
    out << e.result(status).dump(2) << "\n";
  }

private:
  fs::path directory_;
  std::ofstream commands_, states_, observations_;
  void check() {
    if (!commands_ || !states_ || !observations_)
      throw std::runtime_error("record write failed");
  }
};
Json basic(const std::string &type, const std::string &run) {
  return {{"type", type}, {"schema_version", 1}, {"run_id", run}};
}
std::optional<Json> read_next(std::ifstream &in) {
  std::string line;
  while (std::getline(in, line)) {
    if (line.size() > 1048576)
      throw std::runtime_error("replay record exceeds 1 MiB");
    if (!line.empty())
      return Json::parse(line);
  }
  if (!in.eof())
    throw std::runtime_error("replay record read failed");
  return std::nullopt;
}
int replay(const fs::path &directory) {
  std::ifstream mf(directory / "manifest.json");
  if (!mf)
    throw std::runtime_error("missing manifest");
  Json manifest;
  mf >> manifest;
  if (manifest.at("schema_version") != 1 || manifest.at("engine_version") != arena::engine_version)
    throw std::runtime_error("unsupported replay version");
  arena::Engine engine(arena::Config::from_json(manifest.at("config")), manifest.at("run_id"));
  std::ifstream states(directory / "states.ndjson"), commands(directory / "commands.ndjson"),
      observations(directory / "observations.ndjson");
  if (!states || !commands || !observations)
    throw std::runtime_error("missing replay record file");
  auto command = read_next(commands), observation = read_next(observations),
       expected = read_next(states);
  if (!expected)
    throw std::runtime_error("empty state log");
  std::size_t state_count = 0, command_count = 0, observation_count = 0;
  while (expected) {
    if (state_count)
      engine.step();
    if (state_count == 0 || engine.scheduled()) {
      const auto actual = engine.observation();
      if (!observation || actual != *observation) {
        std::cout
            << Json{{"ok", false}, {"mismatch", "observation"}, {"tick", engine.tick()}}.dump()
            << "\n";
        return 2;
      }
      ++observation_count;
      observation = read_next(observations);
    }
    if (expected->at("tick") != engine.tick() || expected->at("checksum") != engine.checksum() ||
        expected->at("state") != engine.state()) {
      std::cout << Json{{"ok", false}, {"mismatch", "state"}, {"tick", engine.tick()}}.dump()
                << "\n";
      return 2;
    }
    ++state_count;
    while (command && command->at("receipt_tick") == engine.tick()) {
      const auto &message = command->at("command");
      const auto actual =
          message.at("type") == "fallback" ? engine.fallback(message) : engine.assess(message);
      if (actual != command->at("result")) {
        std::cout
            << Json{{"ok", false}, {"mismatch", "command_result"}, {"tick", engine.tick()}}.dump()
            << "\n";
        return 2;
      }
      ++command_count;
      command = read_next(commands);
    }
    if (command && command->at("receipt_tick").get<int>() < engine.tick())
      throw std::runtime_error("unordered command log");
    expected = read_next(states);
  }
  std::ifstream result_file(directory / "results.json");
  if (!result_file)
    throw std::runtime_error("missing final result");
  Json saved_result;
  result_file >> saved_result;
  const auto status = saved_result.at("status").get<std::string>();
  if ((status == "completed" && !engine.finished()) || engine.result(status) != saved_result) {
    std::cout << Json{{"ok", false}, {"mismatch", "final_result"}, {"tick", engine.tick()}}.dump()
              << "\n";
    return 2;
  }
  const bool ok = !command && !observation;
  std::cout << Json{{"ok", ok},
                    {"states_verified", state_count},
                    {"commands_verified", command_count},
                    {"observations_verified", observation_count},
                    {"final_tick", engine.tick()},
                    {"network_calls", 0}}
                   .dump()
            << "\n";
  return ok ? 0 : 2;
}
double percentile(const std::vector<double> &sorted, double p) {
  return sorted[static_cast<std::size_t>((sorted.size() - 1) * p)];
}
int benchmark() {
  Json workloads = Json::array();
  for (int count : {12, 64, 256}) {
    arena::Config c;
    c.bots_per_team = count / 2;
    c.duration_ticks = 1200;
    c.planning_ticks.clear();
    arena::Engine e(c, "benchmark");
    std::vector<double> times;
    const auto workload_start = Clock::now();
    for (int i = 0; i < 1200; ++i) {
      auto start = Clock::now();
      e.step();
      const auto elapsed = std::chrono::duration<double, std::milli>(Clock::now() - start).count();
      if (i >= 100)
        times.push_back(elapsed);
    }
    const auto elapsed_seconds =
        std::chrono::duration<double>(Clock::now() - workload_start).count();
    rusage usage{};
    getrusage(RUSAGE_SELF, &usage);
#ifdef __APPLE__
    const auto rss_bytes = usage.ru_maxrss;
#else
    const auto rss_bytes = usage.ru_maxrss * 1024;
#endif
    std::sort(times.begin(), times.end());
    workloads.push_back({{"total_bots", count},
                         {"samples", times.size()},
                         {"tick_us",
                          {{"p50", percentile(times, .5) * 1000},
                           {"p95", percentile(times, .95) * 1000},
                           {"p99", percentile(times, .99) * 1000},
                           {"max", times.back() * 1000}}},
                         {"elapsed_seconds", elapsed_seconds},
                         {"process_peak_rss_bytes", rss_bytes},
                         {"p99_under_5ms", percentile(times, .99) <= 5.0}});
  }
  utsname system{};
  uname(&system);
  std::cout << Json{{"type", "benchmark"},
                    {"engine_version", arena::engine_version},
                    {"compiler", __VERSION__},
                    {"hardware",
                     {{"system", system.sysname},
                      {"architecture", system.machine},
                      {"logical_cores", std::thread::hardware_concurrency()}}},
                    {"build_mode",
#ifdef NDEBUG
                     "release"
#else
                     "debug"
#endif
                    },
                    {"measurement",
                     "Engine::step only; excludes startup, IPC, records, and LLM latency"},
                    {"workloads", workloads}}
                   .dump(2)
            << "\n";
  return 0;
}
int serve() {
  Transport io;
  std::unique_ptr<arena::Engine> engine;
  std::unique_ptr<Records> records;
  auto origin = Clock::now();
  bool stopping = false;
  std::string status = "stopped";
  auto emit_observation = [&] {
    auto o = engine->observation();
    if (!io.emit(o))
      throw std::runtime_error("output queue unavailable");
    records->observation(o);
  };
  auto advance_one = [&] {
    engine->step();
    if (engine->scheduled())
      emit_observation();
    records->state(*engine);
  };
  auto finish = [&](const std::string &type, const std::string &final_status) {
    records->finish(*engine, final_status);
    Json out = basic(type, engine->run_id());
    auto result = engine->result(final_status);
    out["tick"] = result.at("tick");
    out["scores"] = result.at("scores");
    out["winner"] = result.at("winner");
    io.emit(out);
    status = final_status;
    stopping = true;
  };
  while (!stopping && !io.failed()) {
    int received = 0;
    while (received++ < 32) {
      auto incoming = io.incoming.pop();
      if (!incoming)
        break;
      const auto &j = *incoming;
      const std::string type = j.is_object() && j.contains("type") && j.at("type").is_string()
                                   ? j.at("type").get<std::string>()
                                   : "invalid";
      if ((type == "assessment" || type == "fallback") && engine) {
        const auto result = type == "fallback" ? engine->fallback(j) : engine->assess(j);
        records->command(engine->tick(), j, result);
        records->flush();
        io.emit(result);
        continue;
      }
      if (type == "start" && !engine) {
        try {
          if (j.at("schema_version") != 1 || !j.at("run_id").is_string() ||
              j.at("run_id").get<std::string>().empty() ||
              j.at("run_id").get<std::string>().size() > 128)
            throw std::invalid_argument("invalid start envelope");
          const auto config = arena::Config::from_json(j.value("config", Json::object()));
          auto candidate =
              std::make_unique<arena::Engine>(config, j.at("run_id").get<std::string>());
          auto logs = std::make_unique<Records>(j.at("record_dir").get<std::string>(), *candidate);
          engine = std::move(candidate);
          records = std::move(logs);
          origin = Clock::now();
          auto ready = basic("ready", engine->run_id());
          ready["tick"] = 0;
          ready["tick_hz"] = 10;
          ready["duration_ticks"] = config.duration_ticks;
          ready["bots_per_team"] = config.bots_per_team;
          io.emit(ready);
          emit_observation();
          records->state(*engine);
          records->flush();
        } catch (const std::exception &e) {
          Json error = basic("error", j.contains("run_id") && j.at("run_id").is_string()
                                          ? j.at("run_id").get<std::string>()
                                          : "");
          error["reason"] = std::string("start_failed: ") + e.what();
          io.emit(error);
        }
        continue;
      }
      if (!engine) {
        Json error = basic("error", "");
        error["reason"] = "start_required";
        io.emit(error);
        continue;
      }
      if (!j.is_object() || !j.contains("schema_version") || j.at("schema_version") != 1 ||
          !j.contains("run_id") || j.at("run_id") != engine->run_id()) {
        auto error = basic("error", engine->run_id());
        error["reason"] = "invalid_envelope";
        io.emit(error);
        continue;
      }
      if (type == "stop") {
        finish("stopped", "stopped");
        break;
      }
      if (type == "advance") {
        if (engine->config().paced) {
          auto error = basic("error", engine->run_id());
          error["reason"] = "paced_run_cannot_advance";
          io.emit(error);
          continue;
        }
        if (!j.contains("ticks") || !j.at("ticks").is_number_integer() ||
            j.at("ticks").get<std::int64_t>() <= 0 || j.at("ticks").get<std::int64_t>() > 36000) {
          auto error = basic("error", engine->run_id());
          error["reason"] = "advance_ticks_must_be_1_to_36000";
          io.emit(error);
          continue;
        }
        const int target =
            std::min(engine->config().duration_ticks, engine->tick() + j.at("ticks").get<int>());
        while (engine->tick() < target)
          advance_one();
        records->flush();
        auto out = basic("advanced", engine->run_id());
        out["tick"] = engine->tick();
        io.emit(out);
        if (engine->finished())
          finish("finished", "completed");
        continue;
      }
      auto error = basic("error", engine->run_id());
      error["reason"] = type == "_parse_error" ? j.value("reason", std::string("invalid_json"))
                                               : "unknown_command";
      io.emit(error);
    }
    if (stopping)
      break;
    if (engine && engine->config().paced) {
      const int due = static_cast<int>(
          std::chrono::duration<double>(Clock::now() - origin).count() * engine->config().tick_hz);
      int steps = 0;
      while (engine->tick() < due && !engine->finished() && steps++ < 8)
        advance_one();
      if (engine->finished()) {
        finish("finished", "completed");
        break;
      }
    }
    if (io.eof() && io.incoming.empty()) {
      if (engine)
        finish("stopped", "eof");
      else
        stopping = true;
      break;
    }
    std::this_thread::sleep_for(std::chrono::milliseconds(1));
  }
  if (io.failed()) {
    std::cerr << "transport failed or bounded output queue exceeded\n";
    if (engine && records)
      records->finish(*engine, "transport_failed");
    io.shutdown();
    return 2;
  }
  (void)status;
  io.shutdown();
  if (io.failed()) {
    if (engine && records)
      records->finish(*engine, "transport_failed");
    return 2;
  }
  return 0;
}
} // namespace
int main(int argc, char **argv) {
  try {
    if (argc == 3 && std::string(argv[1]) == "--replay")
      return replay(argv[2]);
    if (argc == 2 && std::string(argv[1]) == "--benchmark")
      return benchmark();
    if (argc == 2 && std::string(argv[1]) == "--help") {
      std::cout << "arena-sim [--replay RUN_DIR | --benchmark]\nDefault: bounded NDJSON protocol "
                   "on stdin/stdout. See docs/protocol.md.\n";
      return 0;
    }
    if (argc != 1)
      throw std::invalid_argument("usage: arena-sim [--replay RUN_DIR | --benchmark]");
    return serve();
  } catch (const std::exception &e) {
    std::cerr << "arena-sim: " << e.what() << "\n";
    return 1;
  }
}
