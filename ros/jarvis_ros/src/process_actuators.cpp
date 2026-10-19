// Copyright 2026 Erwin L.
// SPDX-License-Identifier: Apache-2.0
//
// Restart what is not a lifecycle node (docs/design/05-rules-and-recovery.md §8): a driver in a
// container, a daemon under systemd.
//
//   - id: restart
//     action: container.restart          # Docker Engine API (Podman's compatible socket works too)
//     with: { container: lidar-driver, stop_timeout_s: 5 }
//
//   - id: restart
//     action: systemd.restart            # systemctl restart, then waits for `active`
//     with: { unit: lidar-driver.service, user: false }
//
// Nothing is restartable until the recovery node allows it: `containers.allowed` and
// `systemd.allowed_units` (globs, empty by default) on /jarvis/recovery. A plan that names a
// container or unit outside them fails to load; a templated name is checked when it runs.
//
// The Docker API and systemctl are called from a worker thread, so the recovery node never
// blocks; the step's timeout bounds the wait for the container or unit to be up again.

#include <fcntl.h>
#include <fnmatch.h>
#include <poll.h>
#include <signal.h>
#include <spawn.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <sys/wait.h>
#include <unistd.h>

#include <algorithm>
#include <atomic>
#include <cctype>
#include <cstring>
#include <pluginlib/class_list_macros.hpp>
#include <regex>
#include <thread>

#include "jarvis_ros/actuator.hpp"

extern char ** environ;

namespace jarvis_ros
{

namespace
{

using Clock = std::chrono::steady_clock;
constexpr auto kDefaultWait = std::chrono::seconds(60);
constexpr auto kPoll = std::chrono::milliseconds(250);

struct Fd
{
  int fd{-1};
  ~Fd()
  {
    if (fd >= 0) {
      ::close(fd);
    }
  }
};

struct Http
{
  int status{0};
  std::string body;
};

/// An HTTP/1.1 request over a unix socket (the Docker Engine API), or the error.
std::optional<Http> unix_http(
  const std::string & socket_path, const std::string & method, const std::string & target,
  std::string & error, std::chrono::seconds timeout = std::chrono::seconds(30))
{
  sockaddr_un addr{};
  if (socket_path.size() >= sizeof(addr.sun_path)) {
    error = "socket path too long: " + socket_path;
    return std::nullopt;
  }
  Fd s{::socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0)};
  if (s.fd < 0) {
    error = std::string("socket: ") + std::strerror(errno);
    return std::nullopt;
  }
  timeval tv{static_cast<time_t>(timeout.count()), 0};
  ::setsockopt(s.fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));
  ::setsockopt(s.fd, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof(tv));
  addr.sun_family = AF_UNIX;
  std::memcpy(addr.sun_path, socket_path.c_str(), socket_path.size());
  if (::connect(s.fd, reinterpret_cast<sockaddr *>(&addr), sizeof(addr)) != 0) {
    error = "cannot reach " + socket_path + ": " + std::strerror(errno);
    return std::nullopt;
  }
  const std::string request = method + " " + target +
                              " HTTP/1.1\r\nHost: docker\r\nUser-Agent: jarvis\r\n"
                              "Content-Length: 0\r\nConnection: close\r\n\r\n";
  for (std::size_t sent = 0; sent < request.size();) {
    const auto n = ::send(s.fd, request.data() + sent, request.size() - sent, MSG_NOSIGNAL);
    if (n <= 0) {
      error = std::string("send: ") + std::strerror(errno);
      return std::nullopt;
    }
    sent += static_cast<std::size_t>(n);
  }
  std::string raw;
  char buf[8192];
  for (;;) {
    const auto n = ::recv(s.fd, buf, sizeof(buf), 0);
    if (n == 0) {
      break;
    }
    if (n < 0) {
      error = std::string("no answer from ") + socket_path + ": " + std::strerror(errno);
      return std::nullopt;
    }
    raw.append(buf, static_cast<std::size_t>(n));
    if (raw.size() > (4u << 20)) {
      error = "answer larger than 4 MiB";
      return std::nullopt;
    }
  }
  const auto head_end = raw.find("\r\n\r\n");
  if (raw.rfind("HTTP/1.", 0) != 0 || head_end == std::string::npos) {
    error = "not an HTTP answer";
    return std::nullopt;
  }
  Http out;
  out.status = std::atoi(raw.c_str() + raw.find(' ') + 1);
  std::string head = raw.substr(0, head_end);
  std::transform(head.begin(), head.end(), head.begin(), [](unsigned char c) {
    return static_cast<char>(std::tolower(c));
  });
  std::string body = raw.substr(head_end + 4);
  if (head.find("transfer-encoding: chunked") != std::string::npos) {
    std::string decoded;
    std::size_t pos = 0;
    for (;;) {
      const auto eol = body.find("\r\n", pos);
      if (eol == std::string::npos) {
        break;
      }
      const auto size = std::strtoul(body.substr(pos, eol - pos).c_str(), nullptr, 16);
      if (size == 0 || eol + 2 + size > body.size()) {
        break;
      }
      decoded += body.substr(eol + 2, size);
      pos = eol + 2 + size + 2;
    }
    body = std::move(decoded);
  }
  out.body = std::move(body);
  return out;
}

/// Docker's `{"message": ...}`, or the status.
std::string docker_error(const Http & r)
{
  const auto j = nlohmann::json::parse(r.body, nullptr, false);
  if (j.is_object() && j.contains("message") && j["message"].is_string()) {
    return j["message"].get<std::string>();
  }
  return "HTTP " + std::to_string(r.status);
}

/// Runs a program (no shell) and returns its exit status and output, or -1 when it could not run
/// or ran past `timeout` (then it is killed).
std::pair<int, std::string> run(const std::vector<std::string> & argv, std::chrono::seconds timeout)
{
  int out[2];
  if (::pipe2(out, O_CLOEXEC) != 0) {
    return {-1, std::string("pipe: ") + std::strerror(errno)};
  }
  posix_spawn_file_actions_t fa;
  posix_spawn_file_actions_init(&fa);
  posix_spawn_file_actions_adddup2(&fa, out[1], STDOUT_FILENO);
  posix_spawn_file_actions_adddup2(&fa, out[1], STDERR_FILENO);
  std::vector<char *> args;
  for (const auto & a : argv) {
    args.push_back(const_cast<char *>(a.c_str()));
  }
  args.push_back(nullptr);
  pid_t pid = 0;
  const int rc = ::posix_spawnp(&pid, args[0], &fa, nullptr, args.data(), environ);
  posix_spawn_file_actions_destroy(&fa);
  ::close(out[1]);
  Fd reader{out[0]};
  if (rc != 0) {
    return {-1, "cannot run " + argv[0] + ": " + std::strerror(rc)};
  }
  std::string text;
  const auto deadline = Clock::now() + timeout;
  bool timed_out = false;
  for (;;) {
    const auto left =
      std::chrono::duration_cast<std::chrono::milliseconds>(deadline - Clock::now());
    if (left.count() <= 0) {
      timed_out = true;
      break;
    }
    pollfd p{reader.fd, POLLIN, 0};
    if (::poll(&p, 1, static_cast<int>(left.count())) <= 0) {
      continue;
    }
    char buf[4096];
    const auto n = ::read(reader.fd, buf, sizeof(buf));
    if (n <= 0) {
      break;
    }
    if (text.size() < (64u << 10)) {
      text.append(buf, static_cast<std::size_t>(n));
    }
  }
  if (timed_out) {
    ::kill(pid, SIGKILL);
  }
  int status = 0;
  ::waitpid(pid, &status, 0);
  while (!text.empty() && std::isspace(static_cast<unsigned char>(text.back()))) {
    text.pop_back();
  }
  if (timed_out) {
    return {-1, argv[0] + " took longer than " + std::to_string(timeout.count()) + " s"};
  }
  return {WIFEXITED(status) ? WEXITSTATUS(status) : -1, text};
}

std::string last_line(const std::string & text)
{
  const auto nl = text.rfind('\n');
  return nl == std::string::npos ? text : text.substr(nl + 1);
}

bool allowed(const std::vector<std::string> & globs, const std::string & name)
{
  return std::any_of(globs.begin(), globs.end(), [&name](const std::string & g) {
    return ::fnmatch(g.c_str(), name.c_str(), 0) == 0;
  });
}

bool templated(const std::string & s) { return s.find("{{") != std::string::npos; }

const std::regex kContainer{"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$"};
const std::regex kUnit{"^[A-Za-z0-9:_.@\\\\-]{1,256}$"};

/// What a worker thread found; read on the executor once `done`.
struct Job
{
  std::atomic<bool> done{false};
  bool ok{false};
  std::string message;
};

/// A container's state from GET /containers/{name}/json.
struct ContainerState
{
  bool running{false};
  bool paused{false};
  bool restarting{false};
  std::string health;  // empty: no healthcheck
  long pid{0};
};

ContainerState container_state(const nlohmann::json & j)
{
  ContainerState s;
  const auto & st = j.value("State", nlohmann::json::object());
  s.running = st.value("Running", false);
  s.paused = st.value("Paused", false);
  s.restarting = st.value("Restarting", false);
  s.pid = st.value("Pid", 0L);
  if (st.contains("Health") && st["Health"].is_object()) {
    s.health = st["Health"].value("Status", "");
  }
  return s;
}

std::pair<bool, std::string> restart_container(
  const std::string & socket, const std::string & name, int stop_timeout_s,
  Clock::time_point deadline)
{
  std::string error;
  const std::string path = "/containers/" + name;
  auto inspect = unix_http(socket, "GET", path + "/json", error);
  if (!inspect) {
    return {false, error};
  }
  if (inspect->status == 404) {
    return {false, "no container named '" + name + "'"};
  }
  if (inspect->status != 200) {
    return {false, "container " + name + ": " + docker_error(*inspect)};
  }
  const auto before = container_state(nlohmann::json::parse(inspect->body, nullptr, false));
  if (before.paused) {  // a frozen container: thaw it, or restart refuses
    auto unpaused = unix_http(socket, "POST", path + "/unpause", error);
    if (!unpaused || unpaused->status >= 300) {
      return {
        false, "container " + name + " is paused: " + (unpaused ? docker_error(*unpaused) : error)};
    }
  }
  auto restarted =
    unix_http(socket, "POST", path + "/restart?t=" + std::to_string(stop_timeout_s), error);
  if (!restarted) {
    return {false, error};
  }
  if (restarted->status >= 300) {
    return {false, "container " + name + ": " + docker_error(*restarted)};
  }
  std::string seen = "not running";
  while (Clock::now() < deadline) {
    auto now = unix_http(socket, "GET", path + "/json", error, std::chrono::seconds(5));
    if (now && now->status == 200) {
      const auto s = container_state(nlohmann::json::parse(now->body, nullptr, false));
      const bool up = s.running && !s.paused && !s.restarting;
      if (up && (s.health.empty() || s.health == "healthy")) {
        return {
          true, "container " + name + " restarted: running" +
                  (s.health.empty() ? "" : ", healthy") + " (pid " + std::to_string(s.pid) + ")"};
      }
      seen = !up ? "not running" : "running, " + s.health;
    }
    std::this_thread::sleep_for(kPoll);
  }
  return {false, "container " + name + " restarted, but still " + seen};
}

std::pair<bool, std::string> restart_unit(
  const std::string & systemctl, const std::string & unit, bool user, Clock::time_point deadline)
{
  std::vector<std::string> base{systemctl};
  if (user) {
    base.push_back("--user");
  }
  auto cmd = base;
  cmd.insert(cmd.end(), {"restart", "--", unit});
  const auto [rc, out] = run(cmd, std::chrono::seconds(60));
  if (rc != 0) {
    return {false, "systemctl restart " + unit + ": " + (out.empty() ? "failed" : last_line(out))};
  }
  std::string state = "unknown";
  while (Clock::now() < deadline) {
    auto check = base;
    check.insert(check.end(), {"is-active", "--", unit});
    const auto [code, text] = run(check, std::chrono::seconds(10));
    state = last_line(text);
    if (code == 0 && state == "active") {
      return {true, "unit " + unit + " restarted: active"};
    }
    if (state == "failed") {
      return {false, "unit " + unit + " failed after the restart"};
    }
    std::this_thread::sleep_for(kPoll);
  }
  return {false, "unit " + unit + " restarted, but still " + state};
}

}  // namespace

class ProcessActuator : public Actuator
{
public:
  void initialize(const rclcpp_lifecycle::LifecycleNode::SharedPtr & node) override
  {
    Actuator::initialize(node);
    declare(
      node, "containers.allowed", std::vector<std::string>{},
      "Containers container.restart may restart (globs); none by default");
    declare(
      node, "containers.socket", std::string("/var/run/docker.sock"),
      "The Docker Engine API socket (Podman's compatible one works too)");
    declare(
      node, "systemd.allowed_units", std::vector<std::string>{},
      "Units systemd.restart may restart (globs); none by default");
    declare(node, "systemd.systemctl", std::string("systemctl"), "The systemctl to run");
  }

  std::vector<std::string> actions() const override
  {
    return {"container.restart", "systemd.restart"};
  }

  std::optional<std::string> check(
    const std::string & action, const nlohmann::json & with) const override
  {
    std::string name;
    const bool container = action == "container.restart";
    if (auto err = string_arg(with, container ? "container" : "unit", name)) {
      return err;
    }
    if (templated(name)) {
      return std::nullopt;  // checked when it runs
    }
    return refused(container, name);
  }

  void start(const Attempt & attempt, Done done) override
  {
    const bool container = attempt.action == "container.restart";
    std::string name;
    if (auto err = string_arg(attempt.args, container ? "container" : "unit", name)) {
      done(false, *err);
      return;
    }
    if (auto why = refused(container, name)) {
      done(false, *why);
      return;
    }
    const auto deadline =
      Clock::now() + (attempt.timeout.count() > 0
                        ? std::chrono::duration_cast<Clock::duration>(attempt.timeout)
                        : std::chrono::duration_cast<Clock::duration>(kDefaultWait));
    std::function<std::pair<bool, std::string>()> work;
    if (container) {
      const auto socket = node()->get_parameter("containers.socket").as_string();
      int stop_timeout = 5;
      if (const auto it = attempt.args.find("stop_timeout_s"); it != attempt.args.end()) {
        stop_timeout = it->is_number() ? it->get<int>() : std::atoi(it->get<std::string>().c_str());
      }
      work = [socket, name, stop_timeout, deadline]() {
        return restart_container(socket, name, stop_timeout, deadline);
      };
    } else {
      const auto systemctl = node()->get_parameter("systemd.systemctl").as_string();
      bool user = false;
      if (const auto it = attempt.args.find("user"); it != attempt.args.end()) {
        user = it->is_boolean() ? it->get<bool>() : it->get<std::string>() == "true";
      }
      work = [systemctl, name, user, deadline]() {
        return restart_unit(systemctl, name, user, deadline);
      };
    }
    auto [token, once] = track(attempt, std::move(done));
    auto job = std::make_shared<Job>();
    std::thread([job, work = std::move(work)]() {
      auto [ok, message] = work();
      job->ok = ok;
      job->message = std::move(message);
      job->done.store(true, std::memory_order_release);
    }).detach();
    when(
      attempt, token, [job]() { return job->done.load(std::memory_order_acquire); },
      [job, once]() { once(job->ok, job->message); });
  }

private:
  template <typename T>
  static void declare(
    const rclcpp_lifecycle::LifecycleNode::SharedPtr & node, const std::string & name, T value,
    const std::string & description)
  {
    if (!node->has_parameter(name)) {
      rcl_interfaces::msg::ParameterDescriptor d;
      d.description = description;
      node->declare_parameter(name, value, d);
    }
  }

  std::optional<std::string> refused(bool container, const std::string & name) const
  {
    if (!std::regex_match(name, container ? kContainer : kUnit)) {
      return "'" + name + "' is not a " + (container ? "container" : "unit") + " name";
    }
    const std::string param = container ? "containers.allowed" : "systemd.allowed_units";
    if (!allowed(node()->get_parameter(param).as_string_array(), name)) {
      return std::string(container ? "container" : "unit") + " '" + name + "' is not in " + param +
             " (a parameter of /jarvis/recovery)";
    }
    return std::nullopt;
  }
};

}  // namespace jarvis_ros

PLUGINLIB_EXPORT_CLASS(jarvis_ros::ProcessActuator, jarvis_ros::Actuator)
