// Copyright 2026 Erwin L.
// SPDX-License-Identifier: Apache-2.0

#include <gtest/gtest.h>

#include <filesystem>
#include <fstream>

#include "jarvis_ros/state_file.hpp"

namespace fs = std::filesystem;

class StateFileTest : public ::testing::Test
{
protected:
  void SetUp() override
  {
    dir_ =
      fs::temp_directory_path() / ("jarvis_state_" + std::to_string(::getpid()) + "_" +
                                   ::testing::UnitTest::GetInstance()->current_test_info()->name());
    fs::remove_all(dir_);
  }
  void TearDown() override { fs::remove_all(dir_); }
  fs::path dir_;
};

TEST_F(StateFileTest, MissingFileLoadsAsNothing)
{
  jarvis_ros::StateFile f((dir_ / "nested").string(), "x.json");
  EXPECT_FALSE(f.load().has_value());
}

TEST_F(StateFileTest, SavesAtomicallyAndLoadsBack)
{
  jarvis_ros::StateFile f((dir_ / "nested").string(), "x.json");
  f.save({{"boot_count", 3}});
  EXPECT_TRUE(fs::exists(f.path()));
  EXPECT_FALSE(fs::exists(f.path() + ".tmp")) << "temporary file is renamed away";
  EXPECT_EQ(f.load()->at("boot_count"), 3);
  f.save({{"boot_count", 4}});
  EXPECT_EQ(f.load()->at("boot_count"), 4);
}

TEST_F(StateFileTest, CorruptFileIsReportedNotSilentlyIgnored)
{
  fs::create_directories(dir_);
  std::ofstream(dir_ / "x.json") << "{ not json";
  jarvis_ros::StateFile f(dir_.string(), "x.json");
  EXPECT_THROW(f.load(), std::runtime_error);
}

TEST_F(StateFileTest, EmptyDirectoryDisablesPersistence)
{
  jarvis_ros::StateFile f("", "x.json");
  EXPECT_FALSE(f.enabled());
  f.save({{"a", 1}});
  EXPECT_FALSE(f.load().has_value());
}
