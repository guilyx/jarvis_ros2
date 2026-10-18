// Copyright 2026 Erwin L.
// SPDX-License-Identifier: Apache-2.0
//
// Shared helpers for Jarvis lifecycle components: QoS profiles, autostart, clock access.

#pragma once

#include <chrono>
#include <jarvis_core/types.hpp>
#include <lifecycle_msgs/msg/state.hpp>
#include <rclcpp/rclcpp.hpp>
#include <rclcpp_lifecycle/lifecycle_node.hpp>
#include <string>

#include "jarvis_ros/conversions.hpp"

namespace jarvis_ros
{

/// The event stream's topics, single and batched: reliable, bounded memory
/// (docs/design/03-data-model.md §11).
inline rclcpp::QoS stream_qos(std::size_t depth)
{
  return rclcpp::QoS(rclcpp::KeepLast(depth)).reliable().durability_volatile();
}

/// Latched state topics (/jarvis/faults): late joiners get the current snapshot.
inline rclcpp::QoS latched_qos()
{
  return rclcpp::QoS(rclcpp::KeepLast(1)).reliable().transient_local();
}

inline jarvis_core::Timestamp now_ns(rclcpp_lifecycle::LifecycleNode & node)
{
  return node.now().nanoseconds();
}

/// Configures then activates the node. Used by `autostart: true`, handy in a component container
/// without a lifecycle manager.
inline void autostart(rclcpp_lifecycle::LifecycleNode & node)
{
  if (node.configure().id() != lifecycle_msgs::msg::State::PRIMARY_STATE_INACTIVE) {
    RCLCPP_ERROR(node.get_logger(), "autostart: configure failed, staying unconfigured");
    return;
  }
  if (node.activate().id() != lifecycle_msgs::msg::State::PRIMARY_STATE_ACTIVE) {
    RCLCPP_ERROR(node.get_logger(), "autostart: activate failed");
  }
}

/// Publisher of core Events through the REP-2007 type adapter. Jazzy's LifecyclePublisher can't
/// publish adapted types, so Jarvis nodes use a plain publisher and only publish while active.
using EventPublisher =
  rclcpp::Publisher<rclcpp::TypeAdapter<jarvis_core::Event, jarvis_interfaces::msg::Event>>;

inline EventPublisher::SharedPtr create_event_publisher(
  rclcpp_lifecycle::LifecycleNode & node, const std::string & topic, const rclcpp::QoS & qos)
{
  auto parameters = node.get_node_parameters_interface();
  auto topics = node.get_node_topics_interface();
  return rclcpp::create_publisher<
    rclcpp::TypeAdapter<jarvis_core::Event, jarvis_interfaces::msg::Event>>(
    parameters, topics, topic, qos);
}

/// Publisher of event batches (/jarvis/event_batches), also through a type adapter.
using BatchPublisher = rclcpp::Publisher<AdaptedBatch>;

inline BatchPublisher::SharedPtr create_batch_publisher(
  rclcpp_lifecycle::LifecycleNode & node, const std::string & topic, const rclcpp::QoS & qos)
{
  auto parameters = node.get_node_parameters_interface();
  auto topics = node.get_node_topics_interface();
  return rclcpp::create_publisher<AdaptedBatch>(parameters, topics, topic, qos);
}

template <typename T>
T declare(
  rclcpp_lifecycle::LifecycleNode & node, const std::string & name, const T & default_value,
  const std::string & description, bool read_only = true)
{
  rcl_interfaces::msg::ParameterDescriptor d;
  d.description = description;
  d.read_only = read_only;
  return node.declare_parameter<T>(name, default_value, d);
}

}  // namespace jarvis_ros
