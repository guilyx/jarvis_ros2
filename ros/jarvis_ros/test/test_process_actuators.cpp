// Copyright 2026 Erwin L.
// SPDX-License-Identifier: Apache-2.0
//
// container.restart and systemd.restart, loaded through pluginlib like /jarvis/recovery does:
// against a stand-in for the Docker Engine API (a unix socket that answers like dockerd) and a
// stand-in systemctl (a script), so the tests need neither.

#include <gtest/gtest.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

#include <atomic>
#include <cstdio>
#include <filesystem>
#include <fstream>
#include <functional>
#include <memory>
#include <mutex>
#include <optional>
#include <pluginlib/class_loader.hpp>
#include <rclcpp/rclcpp.hpp>
#include <rclcpp_lifecycle/lifecycle_node.hpp>
#include <string>
#include <thread>
#include <vector>

#include "jarvis_ros/actuator.hpp"

namespace fs = std::filesystem;
using jarvis_ros::Attempt;

namespace
{

/// Answers HTTP requests on a unix socket, like dockerd: `route(method, target)` -> (status, body).
class FakeDocker
{
public:
  using Route =
    std::function<std::pair<int, std::string>(const std::string &, const std::string &)>;

  FakeDocker(std::string path, Route route) : path_(std::move(path)), route_(std::move(route))
  {
    fs::remove(path_);
    fd_ = ::socket(AF_UNIX, SOCK_STREAM, 0);
    sockaddr_un addr{};
    addr.sun_family = AF_UNIX;
    std::snprintf(addr.sun_path, sizeof(addr.sun_path), "%s", path_.c_str());
    if (
      ::bind(fd_, reinterpret_cast<sockaddr *>(&addr), sizeof(addr)) != 0 ||
      ::listen(fd_, 8) != 0) {
      throw std::runtime_error("cannot listen on " + path_);
    }
    thread_ = std::thread([this] { serve(); });
  }

  ~FakeDocker()
  {
    stop_ = true;
    ::shutdown(fd_, SHUT_RDWR);
    ::close(fd_);
    thread_.join();
    fs::remove(path_);
  }

  std::vector<std::string> requests()
  {
    std::lock_guard<std::mutex> lock(mu_);
    return requests_;
  }

private:
  void serve()
  {
    while (!stop_) {
      const int c = ::accept(fd_, nullptr, nullptr);
      if (c < 0) {
        continue;
      }
      std::string req;
      char buf[1024];
      while (req.find("\r\n\r\n") == std::string::npos) {
        const auto n = ::recv(c, buf, sizeof(buf), 0);
        if (n <= 0) {
          break;
        }
        req.append(buf, static_cast<std::size_t>(n));
      }
      const auto line = req.substr(0, req.find("\r\n"));
      const auto sp1 = line.find(' ');
      const auto sp2 = line.find(' ', sp1 + 1);
      const auto method = line.substr(0, sp1);
      const auto target = line.substr(sp1 + 1, sp2 - sp1 - 1);
      {
        std::lock_guard<std::mutex> lock(mu_);
        requests_.push_back(method + " " + target);
      }
      const auto [status, body] = route_(method, target);
      const std::string head =
        "HTTP/1.1 " + std::to_string(status) +
        " X\r\nContent-Type: application/json\r\nContent-Length: " + std::to_string(body.size()) +
        "\r\n\r\n";
      const auto out = head + body;
      ::send(c, out.data(), out.size(), MSG_NOSIGNAL);
      ::close(c);
    }
  }

  std::string path_;
  Route route_;
  int fd_{-1};
  std::atomic<bool> stop_{false};
  std::thread thread_;
  std::mutex mu_;
  std::vector<std::string> requests_;
};

std::string state_json(bool running, bool paused, int pid, const std::string & health = "")
{
  nlohmann::json state{
    {"Running", running}, {"Paused", paused}, {"Restarting", false}, {"Pid", pid}};
  if (!health.empty()) {
    state["Health"] = {{"Status", health}};
  }
  return nlohmann::json{{"Name", "/lidar-driver"}, {"State", state}}.dump();
}

}  // namespace

class ProcessActuatorTest : public ::testing::Test
{
protected:
  static void SetUpTestSuite() { rclcpp::init(0, nullptr); }
  static void TearDownTestSuite() { rclcpp::shutdown(); }

  void SetUp() override
  {
    dir_ = fs::temp_directory_path() / ("jarvis_process_" + std::to_string(::getpid()));
    fs::create_directories(dir_);
    node_ = std::make_shared<rclcpp_lifecycle::LifecycleNode>("recovery", "jarvis_test");
    executor_.add_node(node_->get_node_base_interface());
    actuator_ = loader_.createSharedInstance("jarvis_ros::ProcessActuator");
    actuator_->initialize(node_);
    node_->set_parameter(rclcpp::Parameter("containers.socket", (dir_ / "docker.sock").string()));
    node_->set_parameter(
      rclcpp::Parameter("containers.allowed", std::vector<std::string>{"lidar-*"}));
    node_->set_parameter(rclcpp::Parameter("systemd.systemctl", (dir_ / "systemctl").string()));
    node_->set_parameter(
      rclcpp::Parameter("systemd.allowed_units", std::vector<std::string>{"lidar-driver.service"}));
  }

  void TearDown() override
  {
    actuator_.reset();
    executor_.remove_node(node_->get_node_base_interface());
    fs::remove_all(dir_);
  }

  struct Outcome
  {
    bool ok;
    std::string message;
  };

  std::optional<Outcome> run(const std::string & action, nlohmann::json args, double timeout_s = 10)
  {
    Attempt a;
    a.execution_id = "exec-" + std::to_string(++n_);
    a.step_id = "restart";
    a.action = action;
    a.args = std::move(args);
    a.timeout = std::chrono::seconds(5);
    std::optional<Outcome> out;
    actuator_->start(a, [&out](bool ok, const std::string & m) { out = Outcome{ok, m}; });
    const auto deadline =
      std::chrono::steady_clock::now() + std::chrono::duration<double>(timeout_s);
    while (!out && std::chrono::steady_clock::now() < deadline) {
      executor_.spin_some(std::chrono::milliseconds(20));
    }
    return out;
  }

  /// A systemctl that logs its arguments and answers is-active from a state file.
  void systemctl(const std::string & restart_exit = "0")
  {
    const auto path = dir_ / "systemctl";
    std::ofstream(path) << "#!/bin/sh\n"
                        << "echo \"$@\" >> " << (dir_ / "calls") << "\n"
                        << "case \"$*\" in\n"
                        << "  *is-active*) s=$(cat " << (dir_ / "state") << ")\n"
                        << "    echo active > " << (dir_ / "state") << "\n"
                        << "    echo \"$s\"; [ \"$s\" = active ] && exit 0 || exit 3;;\n"
                        << "  *restart*) echo activating > " << (dir_ / "state") << "\n"
                        << "    [ " << restart_exit
                        << " = 0 ] || echo 'Job for lidar-driver.service failed.' >&2\n"
                        << "    exit " << restart_exit << ";;\n"
                        << "esac\n";
    fs::permissions(path, fs::perms::owner_all);
  }

  std::string calls()
  {
    std::ifstream in(dir_ / "calls");
    return std::string(std::istreambuf_iterator<char>(in), {});
  }

  fs::path dir_;
  pluginlib::ClassLoader<jarvis_ros::Actuator> loader_{"jarvis_ros", "jarvis_ros::Actuator"};
  std::shared_ptr<jarvis_ros::Actuator> actuator_;
  rclcpp_lifecycle::LifecycleNode::SharedPtr node_;
  rclcpp::executors::SingleThreadedExecutor executor_;
  int n_{0};
};

TEST_F(ProcessActuatorTest, AFrozenContainerIsThawedRestartedAndWaitedFor)
{
  EXPECT_EQ(
    actuator_->actions(), (std::vector<std::string>{"container.restart", "systemd.restart"}));
  std::atomic<int> inspected{0};
  std::atomic<bool> restarted{false};
  FakeDocker docker(
    (dir_ / "docker.sock").string(), [&](const std::string & method, const std::string & target) {
      if (method == "GET" && target == "/containers/lidar-driver/json") {
        if (!restarted) {
          ++inspected;
          return std::make_pair(200, state_json(true, true, 100));
        }
        // after the restart: starting, then healthy
        return std::make_pair(
          200, state_json(true, false, 4321, ++inspected < 3 ? "starting" : "healthy"));
      }
      if (method == "POST" && target == "/containers/lidar-driver/unpause") {
        return std::make_pair(204, std::string());
      }
      if (method == "POST" && target == "/containers/lidar-driver/restart?t=5") {
        restarted = true;
        return std::make_pair(204, std::string());
      }
      return std::make_pair(404, std::string(R"({"message":"page not found"})"));
    });
  const auto out = run("container.restart", {{"container", "lidar-driver"}});
  ASSERT_TRUE(out);
  EXPECT_TRUE(out->ok) << out->message;
  EXPECT_EQ(out->message, "container lidar-driver restarted: running, healthy (pid 4321)");
  const auto requests = docker.requests();
  ASSERT_GE(requests.size(), 5u);
  EXPECT_EQ(requests[0], "GET /containers/lidar-driver/json");
  EXPECT_EQ(requests[1], "POST /containers/lidar-driver/unpause") << "a paused container first";
  EXPECT_EQ(requests[2], "POST /containers/lidar-driver/restart?t=5");
  EXPECT_EQ(requests.back(), "GET /containers/lidar-driver/json") << "waits for healthy";
}

TEST_F(ProcessActuatorTest, OnlyAllowedContainersAreTouched)
{
  FakeDocker docker((dir_ / "docker.sock").string(), [](const std::string &, const std::string &) {
    return std::make_pair(404, std::string(R"({"message":"No such container: lidar-ghost"})"));
  });
  auto refused = run("container.restart", {{"container", "postgres"}});
  ASSERT_TRUE(refused);
  EXPECT_FALSE(refused->ok);
  EXPECT_EQ(
    refused->message,
    "container 'postgres' is not in containers.allowed (a parameter of /jarvis/recovery)");
  auto bad = run("container.restart", {{"container", "../../info"}});
  ASSERT_TRUE(bad);
  EXPECT_EQ(bad->message, "'../../info' is not a container name");
  EXPECT_TRUE(docker.requests().empty()) << "nothing asked of Docker";

  auto ghost = run("container.restart", {{"container", "lidar-ghost"}});
  ASSERT_TRUE(ghost);
  EXPECT_FALSE(ghost->ok);
  EXPECT_EQ(ghost->message, "no container named 'lidar-ghost'");

  // When the plans load: a literal name is checked, a template when it runs.
  EXPECT_NE(actuator_->check("container.restart", {{"container", "postgres"}}), std::nullopt);
  EXPECT_EQ(actuator_->check("container.restart", {{"container", "lidar-driver"}}), std::nullopt);
  EXPECT_EQ(
    actuator_->check("container.restart", {{"container", "{{ params.container }}"}}), std::nullopt);
  EXPECT_NE(actuator_->check("container.restart", nlohmann::json::object()), std::nullopt);
}

TEST_F(ProcessActuatorTest, NoDockerIsAFailureNotAHang)
{
  const auto out = run("container.restart", {{"container", "lidar-driver"}});
  ASSERT_TRUE(out);
  EXPECT_FALSE(out->ok);
  EXPECT_NE(out->message.find("cannot reach"), std::string::npos) << out->message;
}

TEST_F(ProcessActuatorTest, ASystemdUnitIsRestartedAndWaitedFor)
{
  systemctl();
  auto out = run("systemd.restart", {{"unit", "lidar-driver.service"}});
  ASSERT_TRUE(out);
  EXPECT_TRUE(out->ok) << out->message;
  EXPECT_EQ(out->message, "unit lidar-driver.service restarted: active");
  EXPECT_EQ(
    calls(),
    "restart -- lidar-driver.service\nis-active -- lidar-driver.service\n"
    "is-active -- lidar-driver.service\n")
    << "activating, then active";

  out = run("systemd.restart", {{"unit", "lidar-driver.service"}, {"user", "true"}});
  ASSERT_TRUE(out);
  EXPECT_NE(calls().find("--user restart -- lidar-driver.service"), std::string::npos);

  auto refused = run("systemd.restart", {{"unit", "sshd.service"}});
  ASSERT_TRUE(refused);
  EXPECT_FALSE(refused->ok);
  EXPECT_EQ(
    refused->message,
    "unit 'sshd.service' is not in systemd.allowed_units (a parameter of /jarvis/recovery)");
}

TEST_F(ProcessActuatorTest, AFailedUnitRestartSaysWhy)
{
  systemctl("1");
  const auto out = run("systemd.restart", {{"unit", "lidar-driver.service"}});
  ASSERT_TRUE(out);
  EXPECT_FALSE(out->ok);
  EXPECT_EQ(
    out->message, "systemctl restart lidar-driver.service: Job for lidar-driver.service failed.");
}
