// See stats_json.hpp.

#include <cmath>
#include <fstream>
#include <map>
#include <sstream>

#include "stats_json.hpp"

namespace {
std::string gFile;
std::map<std::string, std::map<std::string, std::string> > gSections;
}

namespace StatsJson {

void SetFile(const std::string &path) { gFile = path; }

std::string Quote(const std::string &s) {
  std::ostringstream os;
  os << '"';
  for (size_t i = 0; i < s.size(); ++i) {
    char c = s[i];
    if (c == '"' || c == '\\') os << '\\' << c;
    else if (c == '\n') os << "\\n";
    else if ((unsigned char)c < 0x20) os << ' ';
    else os << c;
  }
  os << '"';
  return os.str();
}

void Set(const std::string &section, const std::string &key, double value) {
  std::ostringstream os;
  if (std::isfinite(value)) {
    os.precision(10);
    os << value;
  } else {
    os << "null";
  }
  gSections[section][key] = os.str();
}

void Set(const std::string &section, const std::string &key, const std::string &value) {
  gSections[section][key] = Quote(value);
}

void SetRaw(const std::string &section, const std::string &key, const std::string &json) {
  gSections[section][key] = json;
}

void Write() {
  if (gFile.empty()) return;
  std::ofstream out(gFile.c_str());
  out << "{";
  for (std::map<std::string, std::map<std::string, std::string> >::const_iterator s =
           gSections.begin(); s != gSections.end(); ++s) {
    out << (s == gSections.begin() ? "\n  " : ",\n  ") << Quote(s->first) << ": {";
    for (std::map<std::string, std::string>::const_iterator k = s->second.begin();
         k != s->second.end(); ++k) {
      out << (k == s->second.begin() ? "\n    " : ",\n    ") << Quote(k->first) << ": "
          << k->second;
    }
    out << "\n  }";
  }
  out << "\n}\n";
}

}  // namespace StatsJson
