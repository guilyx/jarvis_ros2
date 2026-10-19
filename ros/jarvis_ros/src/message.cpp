// Copyright 2026 Erwin L.
// SPDX-License-Identifier: Apache-2.0

#include "jarvis_ros/message.hpp"

#include <algorithm>
#include <charconv>
#include <cmath>
#include <cstdint>
#include <limits>
#include <rclcpp/typesupport_helpers.hpp>
#include <regex>
#include <rosidl_typesupport_introspection_cpp/field_types.hpp>
#include <rosidl_typesupport_introspection_cpp/service_introspection.hpp>
#include <stdexcept>
#include <string_view>
#include <type_traits>
#include <utility>

namespace jarvis_ros
{

namespace
{

namespace ts = rosidl_typesupport_introspection_cpp;
using nlohmann::json;
using Member = ts::MessageMember;
using Members = ts::MessageMembers;

constexpr const char * kIntrospection = "rosidl_typesupport_introspection_cpp";

std::shared_ptr<rcpputils::SharedLibrary> library(const std::string & type)
{
  static const std::regex form(R"(^[A-Za-z][A-Za-z0-9_]*/(msg|srv|action)/[A-Za-z][A-Za-z0-9_]*$)");
  if (!std::regex_match(type, form)) {
    throw std::runtime_error(
      "'" + type + "' is not a ROS type name (package/msg/Name, package/srv/Name, " +
      "package/action/Name)");
  }
  try {
    return rclcpp::get_typesupport_library(type, kIntrospection);
  } catch (const std::exception & e) {
    throw std::runtime_error(
      "unknown type '" + type + "': is its package installed and sourced? (" + e.what() + ")");
  }
}

const Members * nested(const Member & f) { return static_cast<const Members *>(f.members_->data); }

/// The placeholder field rosidl gives an empty message (a response with no fields).
bool placeholder(const Member & f)
{
  return std::string_view(f.name_) == "structure_needs_at_least_one_member";
}

std::string type_name(const Members * m)
{
  std::string ns = m->message_namespace_;
  for (std::size_t at; (at = ns.find("::")) != std::string::npos;) {
    ns.replace(at, 2, "/");
  }
  return ns + "/" + m->message_name_;
}

std::string join(const std::string & path, const std::string & key)
{
  return path.empty() ? key : path + "." + key;
}

std::string at(const std::string & path) { return path.empty() ? "" : path + ": "; }

std::string brief(const json & v)
{
  auto s = v.dump(-1, ' ', true);
  return s.size() > 60 ? s.substr(0, 57) + "..." : s;
}

bool is_template(const json & v)
{
  return v.is_string() && v.get_ref<const std::string &>().find("{{") != std::string::npos;
}

/// Calls `f` with a std::type_identity of the C++ type of a primitive field.
template <typename F>
auto visit(std::uint8_t type_id, F && f)
{
  switch (type_id) {
    case ts::ROS_TYPE_FLOAT:
      return f(std::type_identity<float>{});
    case ts::ROS_TYPE_DOUBLE:
      return f(std::type_identity<double>{});
    case ts::ROS_TYPE_LONG_DOUBLE:
      return f(std::type_identity<long double>{});
    case ts::ROS_TYPE_CHAR:
    case ts::ROS_TYPE_OCTET:
      return f(std::type_identity<unsigned char>{});
    case ts::ROS_TYPE_WCHAR:
      return f(std::type_identity<char16_t>{});
    case ts::ROS_TYPE_BOOLEAN:
      return f(std::type_identity<bool>{});
    case ts::ROS_TYPE_UINT8:
      return f(std::type_identity<std::uint8_t>{});
    case ts::ROS_TYPE_INT8:
      return f(std::type_identity<std::int8_t>{});
    case ts::ROS_TYPE_UINT16:
      return f(std::type_identity<std::uint16_t>{});
    case ts::ROS_TYPE_INT16:
      return f(std::type_identity<std::int16_t>{});
    case ts::ROS_TYPE_UINT32:
      return f(std::type_identity<std::uint32_t>{});
    case ts::ROS_TYPE_INT32:
      return f(std::type_identity<std::int32_t>{});
    case ts::ROS_TYPE_UINT64:
      return f(std::type_identity<std::uint64_t>{});
    case ts::ROS_TYPE_INT64:
      return f(std::type_identity<std::int64_t>{});
    case ts::ROS_TYPE_STRING:
      return f(std::type_identity<std::string>{});
    case ts::ROS_TYPE_WSTRING:
      return f(std::type_identity<std::u16string>{});
    default:
      throw std::runtime_error("unsupported field type " + std::to_string(type_id));
  }
}

template <typename T>
bool parse(const std::string & s, T & out)
{
  const auto [end, ec] = std::from_chars(s.data(), s.data() + s.size(), out);
  return ec == std::errc() && end == s.data() + s.size();
}

/// A JSON value into a primitive field. Strings that spell a number or a boolean are taken too:
/// templates render to strings.
template <typename T>
std::optional<std::string> convert(const json & v, T & out, std::size_t bound)
{
  if constexpr (std::is_same_v<T, bool>) {
    if (v.is_boolean()) {
      out = v.get<bool>();
    } else if (v == "true" || v == "false") {
      out = v == "true";
    } else {
      return "expects true or false, not " + brief(v);
    }
  } else if constexpr (std::is_same_v<T, std::string>) {
    if (!v.is_string()) {
      return "expects a string, not " + brief(v);
    }
    out = v.get<std::string>();
    if (bound && out.size() > bound) {
      return "takes at most " + std::to_string(bound) + " characters";
    }
  } else if constexpr (std::is_same_v<T, std::u16string>) {
    if (!v.is_string()) {
      return "expects a string, not " + brief(v);
    }
    out.clear();
    for (const unsigned char c : v.get_ref<const std::string &>()) {
      if (c >= 0x80) {
        return "takes ASCII text only (a wstring)";
      }
      out.push_back(c);
    }
  } else if constexpr (std::is_same_v<T, char16_t>) {
    std::uint16_t code{};
    if (auto err = convert(v, code, bound)) {
      return err;
    }
    out = static_cast<char16_t>(code);
  } else if constexpr (std::is_floating_point_v<T>) {
    double d{};
    if (v.is_number()) {
      d = v.get<double>();
    } else if (!v.is_string() || !parse(v.get<std::string>(), d)) {
      return "expects a number, not " + brief(v);
    }
    out = static_cast<T>(d);
  } else {
    // Integers (and char, octet): exact, and in range.
    std::int64_t i{};
    std::uint64_t u{};
    bool is_unsigned = false;
    if (v.is_number_unsigned()) {
      u = v.get<std::uint64_t>();
      is_unsigned = true;
    } else if (v.is_number_integer()) {
      i = v.get<std::int64_t>();
    } else if (v.is_number_float()) {
      const double d = v.get<double>();
      if (d != std::floor(d) || std::fabs(d) > 9.0e15) {
        return "expects an integer, not " + brief(v);
      }
      i = static_cast<std::int64_t>(d);
    } else if (v.is_string() && parse(v.get<std::string>(), i)) {
    } else if (v.is_string() && parse(v.get<std::string>(), u)) {
      is_unsigned = true;
    } else {
      return "expects an integer, not " + brief(v);
    }
    if (is_unsigned ? !std::in_range<T>(u) : !std::in_range<T>(i)) {
      return brief(v) + " is out of range (" + std::to_string(+std::numeric_limits<T>::min()) +
             " to " + std::to_string(+std::numeric_limits<T>::max()) + ")";
    }
    out = is_unsigned ? static_cast<T>(u) : static_cast<T>(i);
  }
  return std::nullopt;
}

template <typename T>
json scalar_json(const T & v)
{
  if constexpr (std::is_same_v<T, std::u16string>) {
    std::string s;
    for (const char16_t c : v) {
      s.push_back(c < 0x80 ? static_cast<char>(c) : '?');
    }
    return s;
  } else if constexpr (std::is_same_v<T, char16_t>) {
    return static_cast<std::uint16_t>(v);
  } else if constexpr (std::is_same_v<T, long double>) {
    return static_cast<double>(v);
  } else {
    return v;
  }
}

std::optional<std::string> fill_message(
  const Members * m, void * msg, const json & v, const std::string & path, bool templates);

std::optional<std::string> fill_member(
  const Member & f, void * ptr, const json & v, const std::string & path, bool templates)
{
  if (templates && is_template(v)) {
    return std::nullopt;  // checked once rendered
  }
  if (!f.is_array_) {
    if (f.type_id_ == ts::ROS_TYPE_MESSAGE) {
      return fill_message(nested(f), ptr, v, path, templates);
    }
    return visit(f.type_id_, [&](auto tag) -> std::optional<std::string> {
      using T = typename decltype(tag)::type;
      if (auto err = convert(v, *static_cast<T *>(ptr), f.string_upper_bound_)) {
        return at(path) + *err;
      }
      return std::nullopt;
    });
  }
  if (!v.is_array()) {
    return at(path) + "expects a list, not " + brief(v);
  }
  const std::size_t n = v.size();
  const bool fixed = f.array_size_ > 0 && !f.is_upper_bound_;
  if (fixed && n != f.array_size_) {
    return at(path) + "expects exactly " + std::to_string(f.array_size_) + " items, not " +
           std::to_string(n);
  }
  if (f.is_upper_bound_ && n > f.array_size_) {
    return at(path) + "takes at most " + std::to_string(f.array_size_) + " items, not " +
           std::to_string(n);
  }
  if (!fixed) {
    f.resize_function(ptr, n);
  }
  for (std::size_t i = 0; i < n; ++i) {
    const std::string item = path + "[" + std::to_string(i) + "]";
    if (templates && is_template(v[i])) {
      continue;
    }
    if (f.type_id_ == ts::ROS_TYPE_MESSAGE) {
      if (auto err = fill_message(nested(f), f.get_function(ptr, i), v[i], item, templates)) {
        return err;
      }
      continue;
    }
    auto err = visit(f.type_id_, [&](auto tag) -> std::optional<std::string> {
      using T = typename decltype(tag)::type;
      T value{};
      if (auto e = convert(v[i], value, f.string_upper_bound_)) {
        return at(item) + *e;
      }
      f.assign_function(ptr, i, &value);
      return std::nullopt;
    });
    if (err) {
      return err;
    }
  }
  return std::nullopt;
}

std::optional<std::string> fill_message(
  const Members * m, void * msg, const json & v, const std::string & path, bool templates)
{
  if (!v.is_object()) {
    return at(path) + "expects the fields of " + type_name(m) + ", not " + brief(v);
  }
  for (const auto & [key, value] : v.items()) {
    const Member * f = nullptr;
    for (std::uint32_t i = 0; i < m->member_count_; ++i) {
      if (key == m->members_[i].name_ && !placeholder(m->members_[i])) {
        f = &m->members_[i];
      }
    }
    if (!f) {
      std::string fields;
      for (std::uint32_t i = 0; i < m->member_count_; ++i) {
        if (!placeholder(m->members_[i])) {
          fields += (fields.empty() ? "" : ", ") + std::string(m->members_[i].name_);
        }
      }
      return at(path) + type_name(m) + " has no field '" + key + "' (" +
             (fields.empty() ? "it has none" : "it has: " + fields) + ")";
    }
    if (
      auto err =
        fill_member(*f, static_cast<char *>(msg) + f->offset_, value, join(path, key), templates)) {
      return err;
    }
  }
  return std::nullopt;
}

json message_json(const Members * m, const void * msg);

json member_json(const Member & f, const void * ptr)
{
  if (!f.is_array_) {
    if (f.type_id_ == ts::ROS_TYPE_MESSAGE) {
      return message_json(nested(f), ptr);
    }
    return visit(f.type_id_, [&](auto tag) -> json {
      using T = typename decltype(tag)::type;
      return scalar_json(*static_cast<const T *>(ptr));
    });
  }
  json list = json::array();
  const std::size_t n = f.size_function(ptr);
  for (std::size_t i = 0; i < n; ++i) {
    if (f.type_id_ == ts::ROS_TYPE_MESSAGE) {
      list.push_back(message_json(nested(f), f.get_const_function(ptr, i)));
      continue;
    }
    list.push_back(visit(f.type_id_, [&](auto tag) -> json {
      using T = typename decltype(tag)::type;
      T value{};
      f.fetch_function(ptr, i, &value);
      return scalar_json(value);
    }));
  }
  return list;
}

json message_json(const Members * m, const void * msg)
{
  json out = json::object();
  for (std::uint32_t i = 0; i < m->member_count_; ++i) {
    const auto & f = m->members_[i];
    if (!placeholder(f)) {
      out[f.name_] = member_json(f, static_cast<const char *>(msg) + f.offset_);
    }
  }
  return out;
}

bool same(const json & expected, const json & actual)
{
  if (expected.is_number() && actual.is_number()) {
    if (expected.is_number_float() || actual.is_number_float()) {
      const double x = expected.get<double>();
      const double y = actual.get<double>();
      return std::fabs(x - y) <= 1e-6 * std::max({1.0, std::fabs(x), std::fabs(y)});
    }
    return expected == actual;
  }
  if (expected == actual) {
    return true;
  }
  // A rendered template: "904" expected, 904 received.
  return expected.is_string() && actual.is_primitive() && !actual.is_string() &&
         expected.get<std::string>() == actual.dump();
}

}  // namespace

MessageType::MessageType(std::shared_ptr<rcpputils::SharedLibrary> library, const Members * members)
: library_(std::move(library)), members_(members)
{
}

MessageType::Ptr MessageType::message(const std::string & type)
{
  auto lib = library(type);
  try {
    const auto * handle = rclcpp::get_message_typesupport_handle(type, kIntrospection, *lib);
    return std::make_shared<MessageType>(lib, static_cast<const Members *>(handle->data));
  } catch (const std::exception & e) {
    throw std::runtime_error("unknown type '" + type + "' (" + e.what() + ")");
  }
}

std::pair<MessageType::Ptr, MessageType::Ptr> MessageType::service(const std::string & type)
{
  auto lib = library(type);
  try {
    const auto * handle = rclcpp::get_service_typesupport_handle(type, kIntrospection, *lib);
    const auto * members = static_cast<const ts::ServiceMembers *>(handle->data);
    return {
      std::make_shared<MessageType>(lib, members->request_members_),
      std::make_shared<MessageType>(lib, members->response_members_)};
  } catch (const std::exception & e) {
    throw std::runtime_error("unknown type '" + type + "' (" + e.what() + ")");
  }
}

std::string MessageType::name() const { return type_name(members_); }

std::vector<std::string> MessageType::fields() const
{
  std::vector<std::string> out;
  for (std::uint32_t i = 0; i < members_->member_count_; ++i) {
    if (!placeholder(members_->members_[i])) {
      out.emplace_back(members_->members_[i].name_);
    }
  }
  return out;
}

std::shared_ptr<void> MessageType::create() const
{
  void * msg = ::operator new(members_->size_of_);
  members_->init_function(msg, rosidl_runtime_cpp::MessageInitialization::ALL);
  return std::shared_ptr<void>(msg, [self = shared_from_this()](void * p) {
    self->members_->fini_function(p);
    ::operator delete(p);
  });
}

std::optional<std::string> MessageType::fill(
  void * message, const json & value, const std::string & path, bool templates) const
{
  return fill_message(members_, message, value, path, templates);
}

json MessageType::to_json(const void * message) const { return message_json(members_, message); }

json MessageType::default_expect() const
{
  for (std::uint32_t i = 0; i < members_->member_count_; ++i) {
    const auto & f = members_->members_[i];
    if (std::string(f.name_) == "success" && f.type_id_ == ts::ROS_TYPE_BOOLEAN && !f.is_array_) {
      return {{"success", true}};
    }
  }
  return json::object();
}

std::optional<std::string> mismatch(
  const json & expected, const json & actual, const std::string & path)
{
  if (expected.is_object()) {
    if (!actual.is_object()) {
      return path + " is " + brief(actual) + ", expected " + brief(expected);
    }
    for (const auto & [key, value] : expected.items()) {
      const auto it = actual.find(key);
      if (it == actual.end()) {
        return join(path, key) + ": no such field";
      }
      if (auto m = mismatch(value, *it, join(path, key))) {
        return m;
      }
    }
    return std::nullopt;
  }
  if (same(expected, actual)) {
    return std::nullopt;
  }
  return path + " is " + brief(actual) + ", expected " + brief(expected);
}

std::string message_detail(const json & message)
{
  if (message.is_object()) {
    for (const char * key : {"message", "error_msg", "error_message", "reason"}) {
      const auto it = message.find(key);
      if (it != message.end() && it->is_string() && !it->get_ref<const std::string &>().empty()) {
        return it->get<std::string>();
      }
    }
  }
  return "";
}

std::string message_summary(const json & message, std::size_t max)
{
  auto s = message.dump(-1, ' ', true);
  return s.size() > max ? s.substr(0, max - 3) + "..." : s;
}

}  // namespace jarvis_ros
