// Copyright 2026 Erwin L.
// SPDX-License-Identifier: Apache-2.0

#include "jarvis_ros/state_file.hpp"

#include <fcntl.h>
#include <unistd.h>

#include <cerrno>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <sstream>
#include <stdexcept>

namespace jarvis_ros
{

namespace
{

[[noreturn]] void fail(const std::string & what)
{
  throw std::runtime_error(what + ": " + std::strerror(errno));
}

void write_all(int fd, const std::string & data, const std::string & path)
{
  std::size_t written = 0;
  while (written < data.size()) {
    const ssize_t n = ::write(fd, data.data() + written, data.size() - written);
    if (n < 0) {
      if (errno == EINTR) {
        continue;
      }
      ::close(fd);
      fail("cannot write " + path);
    }
    written += static_cast<std::size_t>(n);
  }
}

}  // namespace

StateFile::StateFile(std::string directory, std::string name) : directory_(std::move(directory))
{
  if (!directory_.empty()) {
    path_ = (std::filesystem::path(directory_) / name).string();
  }
}

std::optional<nlohmann::json> StateFile::load() const
{
  if (!enabled() || !std::filesystem::exists(path_)) {
    return std::nullopt;
  }
  std::ifstream in(path_);
  if (!in) {
    fail("cannot open " + path_);
  }
  std::stringstream buffer;
  buffer << in.rdbuf();
  try {
    return nlohmann::json::parse(buffer.str());
  } catch (const nlohmann::json::exception & e) {
    throw std::runtime_error(path_ + " is not valid JSON: " + e.what());
  }
}

void StateFile::save(const nlohmann::json & state) const
{
  if (!enabled()) {
    return;
  }
  std::error_code ec;
  std::filesystem::create_directories(directory_, ec);
  if (ec) {
    throw std::runtime_error("cannot create " + directory_ + ": " + ec.message());
  }
  const std::string tmp = path_ + ".tmp";
  const int fd = ::open(tmp.c_str(), O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0644);
  if (fd < 0) {
    fail("cannot create " + tmp);
  }
  write_all(fd, state.dump(2) + "\n", tmp);
  if (::fsync(fd) != 0) {
    ::close(fd);
    fail("cannot fsync " + tmp);
  }
  ::close(fd);
  if (::rename(tmp.c_str(), path_.c_str()) != 0) {
    fail("cannot rename " + tmp + " to " + path_);
  }
  // Make the rename itself durable.
  const int dir = ::open(directory_.c_str(), O_RDONLY | O_DIRECTORY | O_CLOEXEC);
  if (dir >= 0) {
    ::fsync(dir);
    ::close(dir);
  }
}

}  // namespace jarvis_ros
