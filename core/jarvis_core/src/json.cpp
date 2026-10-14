// Copyright 2026 Erwin L.
// SPDX-License-Identifier: Apache-2.0

#include "jarvis_core/json.hpp"

#include <stdexcept>
#include <string>

#include "jarvis_core/util.hpp"

namespace jarvis_core
{

namespace
{

Timestamp time_from_json(const nlohmann::json & j, const char * field)
{
  const auto parsed = parse_iso8601(j.get<std::string>());
  if (!parsed) {
    throw std::invalid_argument(std::string("invalid timestamp in '") + field + "'");
  }
  return *parsed;
}

}  // namespace

void to_json(nlohmann::json & j, const Source & s)
{
  j = nlohmann::json{{"robot", s.robot}, {"node", s.node}};
  if (!s.component.empty()) {
    j["component"] = s.component;
  }
  if (!s.host.empty()) {
    j["host"] = s.host;
  }
}

void from_json(const nlohmann::json & j, Source & s)
{
  s.robot = j.at("robot").get<std::string>();
  s.node = j.at("node").get<std::string>();
  s.component = j.value("component", "");
  s.host = j.value("host", "");
}

nlohmann::json attrs_to_json(const Attributes & attrs)
{
  nlohmann::json out = nlohmann::json::object();
  for (const auto & [key, value] : attrs) {
    std::visit([&out, &key](const auto & v) { out[key] = v; }, value);
  }
  return out;
}

Attributes attrs_from_json(const nlohmann::json & j)
{
  Attributes attrs;
  for (const auto & [key, value] : j.items()) {
    if (value.is_boolean()) {
      attrs[key] = value.get<bool>();
    } else if (value.is_number_integer()) {
      attrs[key] = value.get<std::int64_t>();
    } else if (value.is_number_float()) {
      attrs[key] = value.get<double>();
    } else if (value.is_string()) {
      attrs[key] = value.get<std::string>();
    } else {
      throw std::invalid_argument("attribute '" + key + "' must be a string, number or boolean");
    }
  }
  return attrs;
}

void to_json(nlohmann::json & j, const Event & e)
{
  j = nlohmann::json{
    {"id", e.id},
    {"seq", std::to_string(e.seq)},
    {"stamp", format_iso8601(e.stamp)},
    {"source", e.source},
    {"kind", std::string(to_string(e.kind))},
    {"code", e.code},
    {"severity", std::string(to_string(e.severity))},
  };
  if (e.recv_stamp != 0) {
    j["recv_stamp"] = format_iso8601(e.recv_stamp);
  }
  if (!e.message.empty()) {
    j["message"] = e.message;
  }
  if (!e.attrs.empty()) {
    j["attrs"] = attrs_to_json(e.attrs);
  }
  if (!e.tags.empty()) {
    j["tags"] = e.tags;
  }
  if (!e.payload.empty()) {
    j["payload"] = e.payload;
  }
  if (!e.payload_schema.empty()) {
    j["payload_schema"] = e.payload_schema;
  }
  if (!e.correlation_id.empty()) {
    j["correlation_id"] = e.correlation_id;
  }
  if (!e.causation_id.empty()) {
    j["causation_id"] = e.causation_id;
  }
}

void from_json(const nlohmann::json & j, Event & e)
{
  e = Event{};
  e.id = j.at("id").get<std::string>();
  if (j.contains("seq")) {
    e.seq = std::stoull(j.at("seq").get<std::string>());
  }
  e.stamp = time_from_json(j.at("stamp"), "stamp");
  if (j.contains("recv_stamp")) {
    e.recv_stamp = time_from_json(j.at("recv_stamp"), "recv_stamp");
  }
  e.source = j.at("source").get<Source>();
  const auto kind = parse_kind(j.at("kind").get<std::string>());
  const auto severity = parse_severity(j.at("severity").get<std::string>());
  if (!kind || !severity) {
    throw std::invalid_argument("invalid kind or severity");
  }
  e.kind = *kind;
  e.severity = *severity;
  e.code = j.at("code").get<std::string>();
  e.message = j.value("message", "");
  if (j.contains("attrs")) {
    e.attrs = attrs_from_json(j.at("attrs"));
  }
  if (j.contains("tags")) {
    e.tags = j.at("tags").get<std::vector<std::string>>();
  }
  e.payload = j.value("payload", "");
  e.payload_schema = j.value("payload_schema", "");
  e.correlation_id = j.value("correlation_id", "");
  e.causation_id = j.value("causation_id", "");
}

}  // namespace jarvis_core
