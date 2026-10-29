// Structured results (stats_json = FILE): sections of key/value pairs that
// parts of the simulator fill in, written as one JSON object at exit.

#ifndef _STATS_JSON_HPP_
#define _STATS_JSON_HPP_

#include <string>

namespace StatsJson {
void SetFile(const std::string &path);  // empty: disabled
void Set(const std::string &section, const std::string &key, double value);
void Set(const std::string &section, const std::string &key, const std::string &value);
void SetRaw(const std::string &section, const std::string &key, const std::string &json);
void Write();  // no-op when disabled
std::string Quote(const std::string &s);
}

#endif
