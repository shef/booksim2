// Message workloads; see messagetrafficmanager.hpp and doc/fabric/messages.md.

#include <cmath>
#include <cstdlib>
#include <fstream>
#include <map>
#include <sstream>

#include "messagetrafficmanager.hpp"
#include "networks/fabric.hpp"
#include "stats_json.hpp"

using namespace std;

static void Fail(const string &msg) {
  cerr << "messages: " << msg << endl;
  exit(-1);
}

MessageTrafficManager::MessageTrafficManager(const Configuration &config,
                                             const vector<Network *> &net)
    : TrafficManager(config, net), _finished(0), _last_retire(0) {
  if (_classes != 1) Fail("sim_type = messages supports one traffic class");
  if (_use_read_write[0]) Fail("sim_type = messages does not use read/write packets");
  _flit_bytes = config.GetInt("flit_bytes");
  _packet_size = config.GetInt("packet_size");
  _cycle_ns = config.GetFloat("cycle_ns");
  _stall_cycles = config.GetInt("message_stall_cycles");
  _out_file = config.GetStr("message_out_file");
  if (_flit_bytes <= 0 || _packet_size <= 0 || _cycle_ns <= 0)
    Fail("flit_bytes, packet_size and cycle_ns must be positive");

  _ReadEndpoints();
  string file = config.GetStr("message_file");
  if (file.empty()) Fail("message_file is required");
  _ReadMessages(file);

  _ready.assign(_ep_first.size(), vector<int>());
  _rr.assign(_ep_first.size(), 0);
  _next_msg.assign(_nodes, -1);
  _next_dest.assign(_nodes, -1);
  _next_size.assign(_nodes, 0);
}

void MessageTrafficManager::_ReadEndpoints() {
  vector<int> first, count, compute;
  FabricTerminals(&first, &count, &compute);
  if (first.empty()) {  // not a fabric network: one endpoint per node
    for (int n = 0; n < _nodes; ++n) {
      first.push_back(n);
      count.push_back(1);
      compute.push_back(0);
    }
  }
  _routers_n = first.size();
  _node_ep.assign(_nodes, -1);
  _ep_first.assign(2 * _routers_n, 0);
  _ep_count.assign(2 * _routers_n, 0);
  for (int r = 0; r < _routers_n; ++r) {
    _ep_first[r] = first[r];
    _ep_count[r] = count[r];
    _ep_first[_routers_n + r] = first[r] + count[r];
    _ep_count[_routers_n + r] = compute[r];
    for (int t = 0; t < count[r] + compute[r]; ++t)
      _node_ep[first[r] + t] = t < count[r] ? r : _routers_n + r;
  }
}

// "R" is router R's own terminals, "cR" its compute terminals; -1 if bad.
int MessageTrafficManager::_ParseEndpoint(const string &tok) const {
  bool compute = !tok.empty() && tok[0] == 'c';
  string digits = compute ? tok.substr(1) : tok;
  if (digits.empty() || digits.find_first_not_of("0123456789") != string::npos) return -1;
  int r = atoi(digits.c_str());
  if (r < 0 || r >= _routers_n) return -1;
  return compute ? _routers_n + r : r;
}

string MessageTrafficManager::_EndpointName(int ep) const {
  ostringstream os;
  if (ep >= _routers_n) os << 'c' << ep - _routers_n;
  else os << ep;
  return os.str();
}

void MessageTrafficManager::_ReadMessages(const string &file) {
  ifstream in(file.c_str());
  if (!in) Fail("cannot open message_file " + file);
  map<int, int> index;  // file id -> message index
  map<string, int> tag_index;
  string line;
  int lineno = 0;
  while (getline(in, line)) {
    ++lineno;
    size_t hash = line.find('#');
    if (hash != string::npos) line.erase(hash);
    istringstream ss(line);
    Message m;
    if (!(ss >> m.id)) continue;  // blank or comment
    ostringstream where;
    where << file << ":" << lineno << ": ";
    string src_tok, dst_tok;
    if (!(ss >> src_tok >> dst_tok >> m.bytes)) Fail(where.str() + "expected: id src dst bytes");
    if (!_msgs.empty() && m.id <= _msgs.back().id) Fail(where.str() + "ids must increase");
    m.src = _ParseEndpoint(src_tok);
    m.dst = _ParseEndpoint(dst_tok);
    if (m.src < 0 || m.dst < 0) Fail(where.str() + "endpoint out of range: " + src_tok + " " + dst_tok);
    if (_ep_count[m.src] == 0 || _ep_count[m.dst] == 0)
      Fail(where.str() + "endpoint has no terminals (a switch, or no compute terminals)");
    if (m.src == m.dst) Fail(where.str() + "source and destination are the same");
    if (m.bytes <= 0) Fail(where.str() + "bytes must be positive");
    vector<int> deps, streams;
    double delay_ns = 0;
    string tag = "-", field;
    while (ss >> field) {
      size_t eq = field.find('=');
      string key = field.substr(0, eq), val = eq == string::npos ? "" : field.substr(eq + 1);
      if (key == "after") {
        istringstream ds(val);
        string d;
        while (getline(ds, d, ',')) {
          map<int, int>::const_iterator it = index.find(atoi(d.c_str()));
          if (d.empty() || it == index.end())
            Fail(where.str() + "after= must name earlier message ids: " + d);
          deps.push_back(it->second);
        }
      } else if (key == "stream") {
        istringstream ds(val);
        string d;
        while (getline(ds, d, ',')) {
          map<int, int>::const_iterator it = index.find(atoi(d.c_str()));
          if (d.empty() || it == index.end())
            Fail(where.str() + "stream= must name earlier message ids: " + d);
          streams.push_back(it->second);
        }
      } else if (key == "delay") {
        delay_ns = atof(val.c_str());
        if (delay_ns < 0) Fail(where.str() + "delay must not be negative");
      } else if (key == "tag") {
        tag = val;
      } else {
        Fail(where.str() + "unknown field " + field);
      }
    }
    long long flits = (m.bytes + _flit_bytes - 1) / _flit_bytes;
    m.packets = (flits + _packet_size - 1) / _packet_size;
    m.last_size = flits - (long long)(m.packets - 1) * _packet_size;
    m.issued = m.retired = 0;
    m.deps_left = deps.size();
    m.delay = (long long)llround(delay_ns / _cycle_ns);
    m.ready = m.first_inject = m.finish = -1;
    m.released = m.listed = false;
    map<string, int>::const_iterator t = tag_index.find(tag);
    if (t == tag_index.end()) {
      t = tag_index.insert(make_pair(tag, (int)_tags.size())).first;
      _tags.push_back(tag);
    }
    m.tag = t->second;
    int i = _msgs.size();
    index[m.id] = i;
    for (size_t k = 0; k < deps.size(); ++k) _msgs[deps[k]].dependents.push_back(i);
    for (size_t k = 0; k < streams.size(); ++k) {
      m.streams.push_back(make_pair(streams[k], 0));
      _msgs[streams[k]].stream_dependents.push_back(make_pair(i, (int)k));
    }
    _msgs.push_back(m);
  }
  if (_msgs.empty()) Fail("message_file " + file + " has no messages");
}

// Packets a message may have sent so far: all of them, or with stream=
// sources the least granted share (granted / source packets) of its own.
int MessageTrafficManager::_Allowed(const Message &m) const {
  long long allowed = m.packets;
  for (size_t k = 0; k < m.streams.size(); ++k) {
    const Message &s = _msgs[m.streams[k].first];
    allowed = min(allowed, (long long)m.streams[k].second * m.packets / s.packets);
  }
  return allowed;
}

// Put a released message in its source's ready list if it may send.
void MessageTrafficManager::_Requeue(int i) {
  Message &m = _msgs[i];
  if (m.released && !m.listed && m.issued < _Allowed(m)) {
    _ready[m.src].push_back(i);
    m.listed = true;
  }
}

// Release messages whose ready time has come, and apply streamed grants.
void MessageTrafficManager::_Release() {
  while (!_pending.empty() && _pending.top().first <= _time) {
    int i = _pending.top().second;
    _pending.pop();
    _msgs[i].released = true;
    _Requeue(i);
  }
  while (!_grants.empty() && _grants.top().first <= _time) {
    int i = _grants.top().second.first, slot = _grants.top().second.second;
    _grants.pop();
    ++_msgs[i].streams[slot].second;
    _Requeue(i);
  }
}

int MessageTrafficManager::_IssuePacket(int source, int cl) {
  int ep = _node_ep[source];
  vector<int> &ready = _ready[ep];
  if (ready.empty()) return 0;
  size_t &rr = _rr[ep];
  if (rr >= ready.size()) rr = 0;
  int i = ready[rr];
  Message &m = _msgs[i];
  int t = source - _ep_first[ep];
  _next_msg[source] = i;
  // Rotate destination terminals per packet so each message spreads over
  // all of them (several single-terminal chips can feed one multi-terminal
  // endpoint at full rate).
  _next_dest[source] = _ep_first[m.dst] + (t + m.issued) % _ep_count[m.dst];
  _next_size[source] = m.issued == m.packets - 1 ? m.last_size : _packet_size;
  if (m.issued == 0) m.first_inject = _time;
  if (++m.issued >= _Allowed(m)) {  // done, or waiting for streamed data
    ready.erase(ready.begin() + rr);  // rr now points at the next message
    m.listed = false;
  } else {
    ++rr;
  }
  ++_packet_seq_no[source];
  return 1;
}

int MessageTrafficManager::_PacketSize(int source, int cl) { return _next_size[source]; }

int MessageTrafficManager::_PacketDestination(int source, int cl) {
  int pid = _cur_pid - 1;
  assert(pid == (int)_pid_msg.size());
  _pid_msg.push_back(_next_msg[source]);
  return _next_dest[source];
}

void MessageTrafficManager::_RetireFlit(Flit *f, int dest) {
  TrafficManager::_RetireFlit(f, dest);
  _last_retire = _time;
  if (!f->tail) return;
  Message &m = _msgs[_pid_msg[f->pid]];
  for (size_t k = 0; k < m.stream_dependents.size(); ++k) {
    int d = m.stream_dependents[k].first;
    _grants.push(make_pair(_time + _msgs[d].delay, make_pair(d, m.stream_dependents[k].second)));
  }
  if (++m.retired < m.packets) return;
  m.finish = _time;
  ++_finished;
  for (size_t k = 0; k < m.dependents.size(); ++k) {
    Message &d = _msgs[m.dependents[k]];
    if (--d.deps_left == 0) {
      d.ready = _time + d.delay;
      _pending.push(make_pair(d.ready, m.dependents[k]));
    }
  }
}

bool MessageTrafficManager::_SingleSim() {
  _sim_state = running;
  for (size_t i = 0; i < _msgs.size(); ++i) {
    if (_msgs[i].deps_left == 0) {
      _msgs[i].ready = _msgs[i].delay;
      _pending.push(make_pair(_msgs[i].ready, (int)i));
    }
  }
  long long bytes = 0;
  for (size_t i = 0; i < _msgs.size(); ++i) bytes += _msgs[i].bytes;
  int chips = 0, engines = 0;
  for (int e = 0; e < _routers_n; ++e) {
    chips += _ep_count[e] > 0;
    engines += _ep_count[_routers_n + e] > 0;
  }
  cout << "Messages: " << _msgs.size() << " (" << bytes << " bytes) between " << chips
       << " endpoints" << (engines ? " and compute terminals on " : "");
  if (engines) cout << engines << " routers";
  cout << endl;
  _last_retire = _time;
  while (_finished < (int)_msgs.size()) {
    _Release();
    _Step();
    bool busy = !_total_in_flight_flits[0].empty();
    for (size_t e = 0; !busy && e < _ready.size(); ++e) busy = !_ready[e].empty();
    if (busy && _time - _last_retire > _stall_cycles) {
      _ReportStall(cout);
      StatsJson::Set("run", "status", string("stalled"));
      StatsJson::Set("run", "cycles", (double)_time);
      StatsJson::Set("messages", "unfinished", (double)(_msgs.size() - _finished));
      StatsJson::Write();
      exit(2);
    }
  }
  _Report(cout);
  UpdateStats();
  DisplayStats();
  _sim_state = draining;
  _drain_time = _time;
  return true;
}

void MessageTrafficManager::_Report(ostream &os) const {
  long long bytes = 0, end = 0;
  for (size_t i = 0; i < _msgs.size(); ++i) {
    bytes += _msgs[i].bytes;
    end = max(end, _msgs[i].finish);
  }
  int active = 0;  // chips; compute terminals do not count
  for (int e = 0; e < _routers_n; ++e) active += _ep_count[e] > 0;
  double ns = end * _cycle_ns;
  StatsJson::Set("messages", "count", (double)_msgs.size());
  StatsJson::Set("messages", "bytes", (double)bytes);
  StatsJson::Set("messages", "completion_cycles", (double)end);
  StatsJson::Set("messages", "completion_ns", ns);
  StatsJson::Set("messages", "throughput_gbps_per_endpoint", ns > 0 ? bytes / ns / active : 0);
  StatsJson::Set("messages", "endpoints", (double)active);
  os << "Message completion time = " << end << " cycles (" << ns << " ns)" << endl
     << "Message throughput = " << (ns > 0 ? bytes / ns / active : 0)
     << " GB/s per endpoint (" << active << " endpoints)" << endl;
  // Per tag: count, bytes, earliest ready, latest finish.
  vector<long long> n(_tags.size(), 0), b(_tags.size(), 0), lo(_tags.size(), -1),
      hi(_tags.size(), 0);
  for (size_t i = 0; i < _msgs.size(); ++i) {
    const Message &m = _msgs[i];
    ++n[m.tag];
    b[m.tag] += m.bytes;
    if (lo[m.tag] < 0 || m.ready < lo[m.tag]) lo[m.tag] = m.ready;
    hi[m.tag] = max(hi[m.tag], m.finish);
  }
  ostringstream tags;
  for (size_t t = 0; t < _tags.size(); ++t) {
    tags << (t ? ", " : "") << "{\"tag\": " << StatsJson::Quote(_tags[t]) << ", \"messages\": "
         << n[t] << ", \"bytes\": " << b[t] << ", \"ready\": " << lo[t] << ", \"finish\": "
         << hi[t] << "}";
  }
  StatsJson::SetRaw("messages", "tags", "[" + tags.str() + "]");
  for (size_t t = 0; t < _tags.size(); ++t) {
    os << "Message tag " << _tags[t] << ": " << n[t] << " messages, " << b[t]
       << " bytes, ready " << lo[t] << ", finish " << hi[t] << ", span "
       << (hi[t] - lo[t]) * _cycle_ns << " ns" << endl;
  }
  if (!_out_file.empty()) {
    ofstream out(_out_file.c_str());
    out << "# id tag ready first_inject finish (cycles)" << endl;
    for (size_t i = 0; i < _msgs.size(); ++i) {
      const Message &m = _msgs[i];
      out << m.id << ' ' << _tags[m.tag] << ' ' << m.ready << ' ' << m.first_inject << ' '
          << m.finish << endl;
    }
  }
}

void MessageTrafficManager::_ReportStall(ostream &os) const {
  os << "Message run stalled: no flit ejected for " << _stall_cycles << " cycles at cycle "
     << _time << "; " << _msgs.size() - _finished << " of " << _msgs.size()
     << " messages unfinished (deadlock, or routes that never deliver)" << endl;
  int shown = 0;
  for (size_t i = 0; i < _msgs.size() && shown < 20; ++i) {
    const Message &m = _msgs[i];
    if (m.finish >= 0 || m.issued == 0) continue;
    os << "  message " << m.id << " (" << _tags[m.tag] << ") " << _EndpointName(m.src) << " -> "
       << _EndpointName(m.dst)
       << ": " << m.issued << " of " << m.packets << " packets sent, " << m.retired
       << " delivered" << endl;
    ++shown;
  }
}
