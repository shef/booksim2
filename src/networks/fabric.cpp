// $Id$

/*
 Copyright (c) 2026, the booksim2 fabric contributors
 All rights reserved.

 Redistribution and use in source and binary forms, with or without
 modification, are permitted provided that the following conditions are met:

 Redistributions of source code must retain the above copyright notice, this
 list of conditions and the following disclaimer.
 Redistributions in binary form must reproduce the above copyright notice, this
 list of conditions and the following disclaimer in the documentation and/or
 other materials provided with the distribution.

 THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
 ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
 WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
 DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE LIABLE FOR
 ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
 (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
 LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON
 ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
 (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
 SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
*/

#include "fabric.hpp"

#include <algorithm>
#include <cassert>
#include <cstdlib>
#include <fstream>
#include <iostream>
#include <sstream>

#include "globals.hpp"
#include "misc_utils.hpp"
#include "random_utils.hpp"

namespace {

// Link kinds with a fixed meaning for the group-based routing functions
// (esc, updown, duato, ugal); dor reads the kind as dimension + 4 * dateline.
enum LinkKind { kLocal = 0, kLocal2 = 1, kGlobal = 2, kNone = -1 };

const int kMaxLinkPorts = 64;  // table masks are 64 bits wide

struct Entry {
  int min, ud0, ud1;
  unsigned long long mask;  // bit p: link port p is a minimal next hop
  int layer;                // escape layer (lduato/ugal tables)
};

struct Hop { int port, vc; };

int gRouters = 0;
std::vector<int> gTerms;       // [router] terminal count
std::vector<int> gFirstNode;   // [router] first node id
std::vector<int> gNodeRouter;  // [node] router
std::vector<std::vector<int> > gPortKind;  // [router][link port] kind
std::vector<unsigned long long> gArrDown;  // [router] bit p: arriving on p was a down hop
std::vector<Entry> gTable;                 // [router * R + dest router]
std::vector<std::vector<Hop> > gPaths;     // [src router * R + dest router]
int gEscLayers = 0;

// Groups (components of the non-global links) and router hop distances.
std::vector<int> gGroup;
int gNumGroups = 0;
std::vector<std::vector<unsigned char> > gDist;

long gHeads = 0, gEscaped = 0;
int gEscapeVcBase = 1 << 30;  // first escape VC, set by the routing function
int gUgalThreshold = 0;
long gUgalMin = 0, gUgalVal = 0;

struct Report {
  ~Report() {
    if (gHeads)
      std::cout << "fabric: packets delivered " << gHeads << ", ended on escape VCs "
                << gEscaped << " (" << 100.0 * gEscaped / gHeads << "%)" << std::endl;
    if (gUgalMin + gUgalVal)
      std::cout << "fabric: UGAL chose minimal " << gUgalMin << ", Valiant " << gUgalVal << " ("
                << 100.0 * gUgalVal / (gUgalMin + gUgalVal) << "% Valiant)" << std::endl;
  }
} gReport;

inline int DestRouter(const Flit *f) { return gNodeRouter[f->dest]; }
inline int SrcRouter(const Flit *f) { return gNodeRouter[f->src]; }
inline const Entry &Lookup(int r, int d) { return gTable[(size_t)r * gRouters + d]; }

// Router port of link port p, and link port of router input in_channel
// (0 for a terminal).
inline int Out(int r, int p) { return gTerms[r] + p - 1; }
inline int LinkPort(int r, int in_channel) {
  return in_channel < gTerms[r] ? 0 : in_channel - gTerms[r] + 1;
}
inline int InKind(int r, int in_channel) {
  int p = LinkPort(r, in_channel);
  return p == 0 ? kNone : gPortKind[r][p];
}
// Up*/down* phase on arrival: 1 if the previous hop went down the tree.
inline int Phase(int r, int in_channel) {
  int p = LinkPort(r, in_channel);
  return p == 0 ? 0 : (int)((gArrDown[r] >> p) & 1);
}
inline int Credits(const Router *r, int p) {
  return std::max(r->GetUsedCredit(Out(r->GetID(), p)), 0);
}

bool Eject(const Router *r, const Flit *f, OutputSet *outputs) {
  int id = r->GetID();
  if (id != DestRouter(f)) return false;
  if (f->head) {
    ++gHeads;
    if (f->vc >= gEscapeVcBase) ++gEscaped;
  }
  outputs->AddRange(f->dest - gFirstNode[id], 0, gNumVCs - 1);
  return true;
}

// Adaptive VCs 0..adaptive-1 on every minimal link port in mask, less
// congested ports at higher priority.
void AddAdaptive(const Router *r, unsigned long long mask, int adaptive, OutputSet *outputs) {
  for (int p = 1; adaptive > 0 && p < kMaxLinkPorts; ++p)
    if (mask >> p & 1)
      outputs->AddRange(Out(r->GetID(), p), 0, adaptive - 1, 1000 - Credits(r, p));
}

int BestPort(const Router *r, unsigned long long mask) {
  int best = -1, bestq = 1 << 30;
  for (int p = 1; p < kMaxLinkPorts; ++p) {
    if (mask >> p & 1) {
      int q = Credits(r, p);
      if (q < bestq) { bestq = q; best = p; }
    }
  }
  return best;
}

}  // namespace

void esc_fabric(const Router *r, const Flit *f, int in_channel, OutputSet *outputs, bool inject) {
  outputs->Clear();
  if (inject) {
    outputs->AddRange(-1, 0, 0);
    return;
  }
  if (Eject(r, f, outputs)) return;
  int id = r->GetID();
  int port = Lookup(id, DestRouter(f)).min;
  int in = InKind(id, in_channel), out = gPortKind[id][port];
  int vc = f->vc + ((in == kGlobal || (in == kLocal2 && out == kLocal)) ? 1 : 0);
  assert(vc < gNumVCs);
  outputs->AddRange(Out(id, port), vc, vc);
}

void updown_fabric(const Router *r, const Flit *f, int in_channel, OutputSet *outputs,
                   bool inject) {
  outputs->Clear();
  if (inject) {
    outputs->AddRange(-1, 0, 0);
    return;
  }
  if (Eject(r, f, outputs)) return;
  int id = r->GetID();
  const Entry &e = Lookup(id, DestRouter(f));
  int port = Phase(id, in_channel) ? e.ud1 : e.ud0;
  int vc = f->vc + (InKind(id, in_channel) == kGlobal ? 1 : 0);
  assert(vc < gNumVCs);
  outputs->AddRange(Out(id, port), vc, vc);
}

void duato_fabric(const Router *r, const Flit *f, int in_channel, OutputSet *outputs,
                  bool inject) {
  outputs->Clear();
  if (inject) {
    outputs->AddRange(-1, 0, 0);
    return;
  }
  if (Eject(r, f, outputs)) return;
  int id = r->GetID(), d = DestRouter(f);
  const Entry &e = Lookup(id, d);
  int adaptive = gNumVCs - 2;
  assert(adaptive >= 1);
  gEscapeVcBase = adaptive;
  int esc_vc = adaptive + (gGroup[id] == gGroup[d] ? 1 : 0);
  if (f->vc >= adaptive && LinkPort(id, in_channel) != 0) {
    // Already in the escape network: stay there.
    int port = Phase(id, in_channel) ? e.ud1 : e.ud0;
    outputs->AddRange(Out(id, port), esc_vc, esc_vc);
    return;
  }
  AddAdaptive(r, e.mask, adaptive, outputs);
  outputs->AddRange(Out(id, e.ud0), esc_vc, esc_vc, 0);
}

// Duato with a layered escape network: VCs below num_vcs - L are adaptive
// over every minimal next hop; the escape VC is adaptive + layer, where the
// layer grows by one per group hop, so any group path length (failures,
// re-planned links) stays deadlock-free. Zero adaptive VCs leaves pure
// escape routing.
void lduato_fabric(const Router *r, const Flit *f, int in_channel, OutputSet *outputs,
                   bool inject) {
  outputs->Clear();
  if (inject) {
    outputs->AddRange(-1, 0, 0);
    return;
  }
  if (Eject(r, f, outputs)) return;
  int id = r->GetID();
  const Entry &e = Lookup(id, DestRouter(f));
  int adaptive = gNumVCs - gEscLayers;
  assert(gEscLayers > 0 && adaptive >= 0);
  gEscapeVcBase = adaptive;
  int esc_vc = adaptive + e.layer;
  if ((f->vc >= adaptive || adaptive == 0) && LinkPort(id, in_channel) != 0) {
    int port = Phase(id, in_channel) ? e.ud1 : e.ud0;
    outputs->AddRange(Out(id, port), esc_vc, esc_vc);
    return;
  }
  AddAdaptive(r, e.mask, adaptive, outputs);
  outputs->AddRange(Out(id, e.ud0), esc_vc, esc_vc, 0);
}

// Fixed source routes, indexed by the flit's hop count.
void src_fabric(const Router *r, const Flit *f, int in_channel, OutputSet *outputs, bool inject) {
  outputs->Clear();
  if (inject) {
    outputs->AddRange(-1, 0, 0);
    return;
  }
  if (Eject(r, f, outputs)) return;
  const std::vector<Hop> &path = gPaths[(size_t)SrcRouter(f) * gRouters + DestRouter(f)];
  assert(f->hops < (int)path.size());
  const Hop &h = path[f->hops];
  assert(h.vc < gNumVCs);
  outputs->AddRange(Out(r->GetID(), h.port), h.vc, h.vc);
}

// UGAL over the lduato tables: at the source, compare the minimal path with a
// Valiant path through a random router in a third group, each weighted as
// (used credits on its best first hop) x (hop count); pick minimal unless the
// Valiant cost is lower by more than ugal_threshold. Adaptive VCs follow
// minimal next hops toward the current target (the intermediate router
// first, then the destination). The escape network always heads for the
// final destination, so Duato's condition still holds.
void ugal_fabric(const Router *r, const Flit *f, int in_channel, OutputSet *outputs, bool inject) {
  outputs->Clear();
  if (inject) {
    outputs->AddRange(-1, 0, 0);
    return;
  }
  if (Eject(r, f, outputs)) return;
  int id = r->GetID(), d = DestRouter(f);
  int adaptive = gNumVCs - gEscLayers;
  assert(gEscLayers > 0 && adaptive >= 1);
  gEscapeVcBase = adaptive;
  const Entry &e = Lookup(id, d);
  int esc_vc = adaptive + e.layer;
  if (f->vc >= adaptive && LinkPort(id, in_channel) != 0) {
    int port = Phase(id, in_channel) ? e.ud1 : e.ud0;
    outputs->AddRange(Out(id, port), esc_vc, esc_vc);
    return;
  }
  if (f->ph < 0) {
    f->ph = 2;
    int gs = gGroup[id], gd = gGroup[d];
    if (gs != gd && gNumGroups > 2) {
      int m;
      do {
        m = RandomInt(gRouters - 1);
      } while (gGroup[m] == gs || gGroup[m] == gd);
      int pmin = BestPort(r, e.mask), pval = BestPort(r, Lookup(id, m).mask);
      long cmin = (long)Credits(r, pmin) * gDist[id][d];
      long cval = (long)Credits(r, pval) * (gDist[id][m] + gDist[m][d]);
      if (cval + gUgalThreshold < cmin) {
        f->ph = 1;
        f->intm = m;
        ++gUgalVal;
      } else {
        ++gUgalMin;
      }
    }
  }
  if (f->ph == 1 && id == f->intm) f->ph = 2;
  int target = f->ph == 1 ? f->intm : d;
  AddAdaptive(r, Lookup(id, target).mask, adaptive, outputs);
  outputs->AddRange(Out(id, e.ud0), esc_vc, esc_vc, 0);
}

// Torus dimension-order routing: link kind = dim + 4 * dateline. VC 0 at the
// start of each dimension, VC 1 after crossing that ring's dateline (Dally &
// Seitz).
void dor_fabric(const Router *r, const Flit *f, int in_channel, OutputSet *outputs, bool inject) {
  outputs->Clear();
  if (inject) {
    outputs->AddRange(-1, 0, 0);
    return;
  }
  if (Eject(r, f, outputs)) return;
  int id = r->GetID();
  int port = Lookup(id, DestRouter(f)).min;
  int out_kind = gPortKind[id][port];
  int out_dim = out_kind & 3;
  int in_kind = InKind(id, in_channel);
  int in_dim = in_kind == kNone ? -1 : (in_kind & 3);
  int vc = (in_dim == out_dim) ? f->vc : 0;
  if (out_kind & 4) vc = 1;
  assert(vc < gNumVCs);
  outputs->AddRange(Out(id, port), vc, vc);
}

Fabric::Fabric(const Configuration &config, const std::string &name) : Network(config, name) {
  _ComputeSize(config);
  _Alloc();
  _BuildNet(config);
}

namespace {

// Topology file: the router count, then one record per line ('#' starts a
// comment):
//   t ROUTER TERMINALS                 terminal count for one router
//   U PORT_U V PORT_V KIND LATENCY     a bidirectional link
struct Link { int u, pu, v, pv, kind, lat; };

void ReadTopology(const std::string &file, int default_terms, int *routers,
                  std::vector<int> *terms, std::vector<Link> *links) {
  std::ifstream in(file.c_str());
  if (!in) {
    std::cerr << "fabric: cannot open network_file " << file << std::endl;
    exit(-1);
  }
  *routers = -1;
  std::string line;
  while (std::getline(in, line)) {
    line = line.substr(0, line.find('#'));
    std::istringstream ss(line);
    std::string first;
    if (!(ss >> first)) continue;
    if (*routers < 0) {
      *routers = atoi(first.c_str());
      assert(*routers > 0);
      terms->assign(*routers, default_terms);
    } else if (first == "t") {
      int r, n;
      ss >> r >> n;
      assert(ss && r >= 0 && r < *routers && n >= 0);
      (*terms)[r] = n;
    } else {
      Link l;
      l.u = atoi(first.c_str());
      ss >> l.pu >> l.v >> l.pv >> l.kind >> l.lat;
      assert(ss && l.u >= 0 && l.u < *routers && l.v >= 0 && l.v < *routers);
      assert(l.pu >= 1 && l.pu < kMaxLinkPorts && l.pv >= 1 && l.pv < kMaxLinkPorts);
      links->push_back(l);
    }
  }
}

}  // namespace

void Fabric::_ComputeSize(const Configuration &config) {
  _topo_file = config.GetStr("network_file");
  std::vector<Link> links;
  ReadTopology(_topo_file, config.GetInt("fabric_terminals"), &gRouters, &gTerms, &links);
  gFirstNode.assign(gRouters, 0);
  gNodeRouter.clear();
  for (int r = 0; r < gRouters; ++r) {
    gFirstNode[r] = gNodeRouter.size();
    gNodeRouter.insert(gNodeRouter.end(), gTerms[r], r);
  }
  _size = gRouters;
  _nodes = gNodeRouter.size();
  _channels = 2 * links.size();
  assert(_nodes > 0);
}

void FabricTerminals(std::vector<int> *first, std::vector<int> *count) {
  *first = gFirstNode;
  *count = gTerms;
}

void Fabric::_BuildNet(const Configuration &config) {
  struct End { int chan_out, chan_in, kind; };
  int routers;
  std::vector<int> terms;
  std::vector<Link> links;
  ReadTopology(_topo_file, config.GetInt("fabric_terminals"), &routers, &terms, &links);

  std::vector<std::vector<End> > ports(_size, std::vector<End>(1, End{-1, -1, kNone}));
  for (size_t i = 0; i < links.size(); ++i) {
    const Link &l = links[i];
    int c_uv = 2 * i, c_vu = 2 * i + 1;
    _chan[c_uv]->SetLatency(l.lat);
    _chan_cred[c_uv]->SetLatency(l.lat);
    _chan[c_vu]->SetLatency(l.lat);
    _chan_cred[c_vu]->SetLatency(l.lat);
    if ((int)ports[l.u].size() <= l.pu) ports[l.u].resize(l.pu + 1, End{-1, -1, kNone});
    if ((int)ports[l.v].size() <= l.pv) ports[l.v].resize(l.pv + 1, End{-1, -1, kNone});
    assert(ports[l.u][l.pu].chan_out < 0 && ports[l.v][l.pv].chan_out < 0);
    ports[l.u][l.pu] = End{c_uv, c_vu, l.kind};
    ports[l.v][l.pv] = End{c_vu, c_uv, l.kind};
  }

  gPortKind.assign(_size, std::vector<int>());
  for (int r = 0; r < _size; ++r) {
    int nlinks = ports[r].size() - 1;
    int radix = gTerms[r] + nlinks;
    std::ostringstream router_name;
    router_name << "router_" << r;
    _routers[r] = Router::NewRouter(config, this, router_name.str(), r, radix, radix);
    _timed_modules.push_back(_routers[r]);
    for (int t = 0; t < gTerms[r]; ++t) {
      int n = gFirstNode[r] + t;
      _routers[r]->AddInputChannel(_inject[n], _inject_cred[n]);
      _routers[r]->AddOutputChannel(_eject[n], _eject_cred[n]);
    }
    gPortKind[r].push_back(kNone);
    for (int p = 1; p <= nlinks; ++p) {
      if (ports[r][p].chan_out < 0) {
        std::cerr << "fabric: router " << r << " link port " << p
                  << " is unconnected; number link ports 1..n without gaps" << std::endl;
        exit(-1);
      }
      _routers[r]->AddOutputChannel(_chan[ports[r][p].chan_out], _chan_cred[ports[r][p].chan_out]);
      _routers[r]->AddInputChannel(_chan[ports[r][p].chan_in], _chan_cred[ports[r][p].chan_in]);
      gPortKind[r].push_back(ports[r][p].kind);
    }
  }

  // Groups and hop distances.
  std::vector<std::vector<std::pair<int, int> > > adj(_size);
  for (size_t i = 0; i < links.size(); ++i) {
    adj[links[i].u].push_back(std::make_pair(links[i].v, links[i].kind));
    adj[links[i].v].push_back(std::make_pair(links[i].u, links[i].kind));
  }
  gGroup.assign(_size, -1);
  gNumGroups = 0;
  for (int r = 0; r < _size; ++r) {
    if (gGroup[r] >= 0) continue;
    std::vector<int> st(1, r);
    gGroup[r] = gNumGroups;
    while (!st.empty()) {
      int x = st.back();
      st.pop_back();
      for (size_t i = 0; i < adj[x].size(); ++i)
        if (adj[x][i].second != kGlobal && gGroup[adj[x][i].first] < 0) {
          gGroup[adj[x][i].first] = gNumGroups;
          st.push_back(adj[x][i].first);
        }
    }
    ++gNumGroups;
  }
  gDist.assign(_size, std::vector<unsigned char>(_size, 255));
  for (int s = 0; s < _size; ++s) {
    std::vector<int> q(1, s);
    gDist[s][s] = 0;
    for (size_t h = 0; h < q.size(); ++h) {
      int x = q[h];
      for (size_t i = 0; i < adj[x].size(); ++i) {
        int y = adj[x][i].first;
        if (gDist[s][y] == 255) {
          gDist[s][y] = gDist[s][x] + 1;
          q.push_back(y);
        }
      }
    }
  }
  gUgalThreshold = config.GetInt("ugal_threshold");
  gEscLayers = config.GetInt("fabric_esc_layers");

  // Source routes: "SRC DEST N (PORT VC) x N" per line, routers and link ports.
  std::string path_file = config.GetStr("fabric_path_file");
  if (!path_file.empty()) {
    std::ifstream pf(path_file.c_str());
    if (!pf) {
      std::cerr << "fabric: cannot open fabric_path_file " << path_file << std::endl;
      exit(-1);
    }
    gPaths.assign((size_t)_size * _size, std::vector<Hop>());
    int src, dst, n;
    while (pf >> src >> dst >> n) {
      std::vector<Hop> &path = gPaths[(size_t)src * _size + dst];
      path.resize(n);
      for (int i = 0; i < n; ++i) pf >> path[i].port >> path[i].vc;
    }
  }

  // Routing tables: R x R entries "MIN UD0 UD1 MASK [LAYER]" (LAYER when
  // fabric_esc_layers > 0), then one up*/down* arrival bitmask per router.
  std::string table_file = config.GetStr("fabric_table_file");
  if (!table_file.empty()) {
    std::ifstream tf(table_file.c_str());
    if (!tf) {
      std::cerr << "fabric: cannot open fabric_table_file " << table_file << std::endl;
      exit(-1);
    }
    gTable.resize((size_t)_size * _size);
    for (size_t i = 0; i < gTable.size(); ++i) {
      tf >> gTable[i].min >> gTable[i].ud0 >> gTable[i].ud1 >> gTable[i].mask;
      gTable[i].layer = 0;
      if (gEscLayers > 0) tf >> gTable[i].layer;
    }
    gArrDown.resize(_size);
    for (int r = 0; r < _size; ++r) tf >> gArrDown[r];
    if (!tf) {
      std::cerr << "fabric: fabric_table_file " << table_file << " is truncated" << std::endl;
      exit(-1);
    }
  }
}

void Fabric::RegisterRoutingFunctions() {
  gRoutingFunctionMap["esc_fabric"] = &esc_fabric;
  gRoutingFunctionMap["updown_fabric"] = &updown_fabric;
  gRoutingFunctionMap["duato_fabric"] = &duato_fabric;
  gRoutingFunctionMap["lduato_fabric"] = &lduato_fabric;
  gRoutingFunctionMap["src_fabric"] = &src_fabric;
  gRoutingFunctionMap["ugal_fabric"] = &ugal_fabric;
  gRoutingFunctionMap["dor_fabric"] = &dor_fabric;
}
